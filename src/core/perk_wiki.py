"""Import the Wiki perk tables through the existing public image pipeline.

Run only on request (--sync-perks). The match overlay itself never uses the
network; it reads the catalog this writes.
"""
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from html.parser import HTMLParser
import json,re,time
from pathlib import Path
from urllib.error import HTTPError
from src.core.perk_catalog import CATALOG
from src.core.wiki_library import SITE,import_original,original_url,public_fetch

PAGE=SITE+'/wiki/Perks'
ROLES={'survivor perks':'survivor','killer perks':'killer'}
GENERAL=('.all','all','general','none','','-','—')


def clean(text):
    return re.sub(r'\s+([,.;:!?)])',r'\1',re.sub(r'\s+',' ',text)).strip()


class PerkTable(HTMLParser):
    """Reads Icon/Name/Description/Character rows under the two perk headings."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows=[];self.role=None;self.heading=None;self.heading_text=[]
        self.row=None;self.cell=None;self.italic=0;self.flavor_span=0;self.span_stack=[]

    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if re.fullmatch('h[1-6]',tag):self.heading=tag;self.heading_text=[]
        if not self.role:return
        if tag=='tr':self.row=[]
        elif tag in ('th','td') and self.row is not None:
            self.cell=dict(text=[],flavor=[],image=None);self.row.append(self.cell);self.italic=0;self.flavor_span=0;self.span_stack=[]
        elif self.cell is None:return
        elif tag=='i':self.italic+=1
        elif tag=='span':
            flavor=self.italic>0 and 'luaClr' in (a.get('class') or '')
            self.span_stack.append(flavor);self.flavor_span+=flavor
        elif tag=='img' and self.cell['image'] is None and len(self.row)==1:
            self.cell['image']=a.get('src','')

    def handle_endtag(self,tag):
        if tag==self.heading:
            title=clean(''.join(self.heading_text)).casefold();self.heading=None
            self.role=next((role for key,role in ROLES.items() if title.startswith(key)),None)
            return
        if not self.role:return
        if tag in ('th','td'):self.cell=None
        elif tag=='tr':
            if self.row and len(self.row)==4 and self.row[0]['image']:self.rows.append((self.role,self.row))
            self.row=None
        elif tag=='i' and self.italic:self.italic-=1
        elif tag=='span' and self.span_stack:self.flavor_span-=self.span_stack.pop()

    def handle_data(self,data):
        if self.heading:self.heading_text.append(data)
        elif self.cell is not None:(self.cell['flavor'] if self.flavor_span else self.cell['text']).append(data)


def slug(name):
    return re.sub(r'[^a-z0-9]+','_',name.casefold()).strip('_')


def parse_perks(html,page=PAGE):
    parser=PerkTable();parser.feed(html);records=[];seen=set()
    for role,(icon,name,description,owner) in parser.rows:
        title=clean(''.join(name['text']));url=original_url(icon['image'],page)
        if not title or not url:continue
        ident=f'perk_{role}_{slug(title)}'
        if ident in seen:raise ValueError('duplicate_perk: '+ident)
        seen.add(ident);holder=clean(''.join(owner['text']))
        records.append(dict(id=ident,name=title,role=role,owner=None if holder.casefold() in GENERAL else holder,
            description=clean(' '.join(description['text'])),flavor=clean(' '.join(description['flavor'])),
            url=url,page=page,aliases=[]))
    return records


def sync_perks(root,fetch=public_fetch,pause=time.sleep,max_seconds=300):
    """Resumable. Stops on HTTP 401/403/429 without any bypass, as sync_wiki does."""
    root=Path(root).resolve();path=root/CATALOG
    previous=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else dict(records=[])
    known={r['url']:r for r in previous['records']}
    body,_,effective=fetch(PAGE,16_000_000)
    parsed=parse_perks(body.decode('utf-8'),effective)
    records=[];errors=[];denied=False;deadline=time.monotonic()+max_seconds;pending=[]
    for record in parsed:
        old=known.get(record['url'])
        if old and (root/old['original']).is_file():records.append({**old,**record,'aliases':old.get('aliases',[])})
        else:pending.append(record)
    def save(complete):
        ordered=sorted(records,key=lambda r:r['id'])
        temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(schema=1,source=PAGE,updated=datetime.now(timezone.utc).isoformat(),complete=complete,
            parsed=len(parsed),errors=errors,access_denied=denied,records=ordered,
            policy='Public HTTP only, four concurrent requests. Stop on HTTP 401/403/429. Runtime never uses the network.'),
            ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(path)
    def retrieve(record):
        data,kind,_=fetch(record['url'])
        if not kind.startswith('image/'):raise ValueError('not_an_image')
        return data
    with ThreadPoolExecutor(max_workers=4) as pool:
        for start in range(0,len(pending),4):
            if denied or time.monotonic()>=deadline:break
            futures={pool.submit(retrieve,record):record for record in pending[start:start+4]}
            for future in as_completed(futures):
                record=futures[future]
                try:
                    source=dict(url=record['url'],page=record['page'],filename=record['url'].rsplit('/',1)[-1],
                                role=record['role'],category='perks',character=None)
                    imported=import_original(root,source,future.result())
                    records.append({**record,'sha256':imported['sha256'],'original':imported['original'],'normalized':imported['normalized']})
                except HTTPError as exc:
                    errors.append(dict(url=record['url'],http_status=exc.code));denied=denied or exc.code in (401,403,429)
                except Exception as exc:errors.append(dict(url=record['url'],error=str(exc)))
            save(False);pause(.25)
    complete=len(records)==len(parsed) and not errors
    save(complete)
    return dict(parsed=len(parsed),stored=len(records),complete=complete,errors=errors,access_denied=denied)
