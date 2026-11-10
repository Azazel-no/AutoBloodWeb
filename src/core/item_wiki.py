"""Import add-on, item and offering descriptions from the public Wiki tables.

Run only on request (--sync-items). The app itself never uses the network; it
reads the catalog this writes.
"""
from datetime import datetime,timezone
from html.parser import HTMLParser
import json,re,time
from pathlib import Path
from urllib.error import HTTPError
from src.core.item_info import CATALOG
from src.core.wiki_library import SITE,original_url,public_fetch

PAGES=tuple(SITE+'/wiki/'+name for name in ('Add-ons','Items','Offerings'))
RARITY=re.compile(r'\b([a-z]+(?:-[a-z]+)*?)-(?:item|addon|offering)-element\b')
BLOCKS=('p','li','br','ul','div')


def tidy(text):
    lines=[re.sub(r'\s+([,.;:!?)])',r'\1',re.sub(r'[ \t\r\f\v\xa0]+',' ',line)).strip() for line in text.split('\n')]
    return '\n'.join(line for line in lines if line)


class ItemTable(HTMLParser):
    """Reads Icon/Name/Description rows; hover-only tooltip text is left out."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows=[];self.row=None;self.cell=None;self.italic=0;self.spans=[]

    def handle_starttag(self,tag,attrs):
        a=dict(attrs);kind=a.get('class') or ''
        if tag=='tr':self.row=[];self.cell=None
        elif tag in ('th','td') and self.row is not None:
            self.cell=dict(text=[],flavor=[],image=None,rarity='');self.row.append(self.cell);self.italic=0;self.spans=[]
        elif self.cell is None:return
        elif tag=='i':self.italic+=1
        elif tag=='span':
            self.spans.append(('hidden' if 'tooltiptext' in kind.split() else 'flavor' if self.italic and 'luaClr' in kind else ''))
        elif tag=='img' and self.cell['image'] is None:self.cell['image']=a.get('src','')
        elif tag=='div' and not self.cell['rarity'] and (match:=RARITY.search(kind)):self.cell['rarity']=match[1]
        if tag in BLOCKS and self.cell is not None:
            self.cell['text'].append('\n• ' if tag=='li' else '\n')

    def handle_endtag(self,tag):
        if tag in ('th','td'):self.cell=None
        elif tag=='tr':
            if self.row and len(self.row)==3 and self.row[0]['image']:self.rows.append(self.row)
            self.row=None
        elif tag=='i' and self.italic:self.italic-=1
        elif tag=='span' and self.spans:self.spans.pop()

    def handle_data(self,data):
        if self.cell is None or 'hidden' in self.spans:return
        (self.cell['flavor'] if 'flavor' in self.spans else self.cell['text']).append(data)


def parse_items(html,page):
    parser=ItemTable();parser.feed(html);records={}
    for icon,name,description in parser.rows:
        title=tidy(''.join(name['text'])).replace('\n',' ');url=original_url(icon['image'],page)
        text=tidy(''.join(description['text']))
        if title and url and text:
            records[url]=dict(name=title,rarity=icon['rarity'].replace('-',' ').title(),description=text,
                              flavor=tidy(''.join(description['flavor'])).replace('\n',' '),page=page)
    return records


def sync_items(root,fetch=public_fetch,pause=time.sleep):
    """All or nothing: an incomplete run leaves the existing catalog in place."""
    path=Path(root).resolve()/CATALOG;records={}
    try:
        for page in PAGES:
            body,_,effective=fetch(page,16_000_000)
            records.update(parse_items(body.decode('utf-8'),effective));pause(.25)
    except HTTPError as exc:
        return dict(stored=0,complete=False,access_denied=exc.code in (401,403,429),error=f'HTTP {exc.code}')
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(dict(schema=1,sources=list(PAGES),updated=datetime.now(timezone.utc).isoformat(),
        license='Text from the Dead by Daylight Wiki (deadbydaylight.wiki.gg), CC BY-SA.',records=records),
        ensure_ascii=False,indent=1,sort_keys=True),encoding='utf-8')
    temporary.replace(path)
    return dict(stored=len(records),complete=True,access_denied=False,error=None)
