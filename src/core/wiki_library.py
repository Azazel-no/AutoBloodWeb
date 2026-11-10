"""Ordinary public Wiki requests; HTTP denial stops the sync without a bypass.

Originals and derived images stay in the existing role/character folders.
Downloaded records require a review before joining the active library.
"""
from datetime import datetime,timezone
import hashlib,json,re,time,subprocess,sys,base64,tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin,urlsplit,unquote
from urllib.error import HTTPError
import cv2,numpy as np
from concurrent.futures import ThreadPoolExecutor,as_completed
from src.utils.images import write_image
from src.core.runtime_paths import project_root

SITE='https://deadbydaylight.wiki.gg'


def is_addon_filename(filename):
    return bool(re.match(r'^(?:t_ui_)?iconaddon',filename,re.I))


def character_sources(root):
    canonical={'nurse':'Sally_Smithson','blight':'Talbot_Grimes','trapper':'The_Trapper','terrifier':'Art_The_Clown'}
    return [dict(killer_id=p.name,page=SITE+'/wiki/'+canonical.get(p.name,'The_'+p.name.title()),
                 page_mapping='user_provided' if p.name in ('nurse','trapper') else 'known_canonical' if p.name in ('blight','terrifier') else 'unverified_alias',
                 folder=p.relative_to(root).as_posix())
            for p in sorted((root/'assets/killers/characters').iterdir()) if p.is_dir()]


def original_url(value,page):
    url=urljoin(page,value); parts=urlsplit(url)
    if parts.scheme!='https' or parts.hostname!='deadbydaylight.wiki.gg': return None
    path=parts.path
    if '/images/' not in path: return None
    if '/thumb/' in path:
        path=path.replace('/thumb/','/',1).rsplit('/',1)[0]
    if not unquote(path).lower().endswith(('.png','.webp')): return None
    return SITE+path


class WikiImages(HTMLParser):
    def __init__(self,page,role,category,character=None):
        super().__init__(); self.page=page; self.role=role; self.category=category; self.character=character
        self.heading=None; self.text=[]; self.section=''; self.sections={}; self.images=[]

    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if re.fullmatch('h[1-6]',tag): self.heading=tag; self.text=[]
        if tag!='img': return
        url=original_url(a.get('data-src') or a.get('src',''),self.page)
        if not url: return
        filename=unquote(urlsplit(url).path.rsplit('/',1)[-1]); lower=filename.lower()
        kind=None
        if self.category=='addons' and is_addon_filename(lower):
            if self.character and re.search(r'add[- ]?ons? (?:for|of)',self.section,re.I): kind='addons'
            elif not self.character and re.search(r'flashlight|med[- ]?kit|toolbox|maps?|keys?|item add[- ]?ons',self.section,re.I): kind='addons'
        elif self.category=='items' and re.match(r'^(?:t_ui_)?iconitem',lower) and 'Regular Items' in self.section: kind='items'
        elif self.category=='offerings' and ('iconoffering' in lower or 'iconfavors' in lower):
            # Unknown scope stays unimported rather than mixing role-exclusive offerings.
            shared=bool(re.search(r'Bonus Bloodpoints.*All Categories|Realm Selection|Basement Location|Chest Numbers|Fog Thickness|Hatch Location',self.section,re.I))
            own=bool(re.search(self.role+r'|shared offerings|common offerings',self.section,re.I))
            if self.role=='killer' and 'Memento Mori' in self.section: own=True
            if self.role=='survivor' and 'Luck' in self.section: own=True
            if 'List of Offerings' in self.section and (own or shared): kind='offerings'
        portrait=re.fullmatch(r'K\d+_The(.+)_Portrait\.(?:png|webp)',filename,re.I)
        if (self.character and portrait and portrait.group(1).replace('_','').lower()==self.character.replace('_','').lower()
                and not any(r['category']=='portrait' for r in self.images)): kind='portrait'
        if not self.character and self.role=='survivor' and 'iconhelploading' in lower and ('survivor' in lower or 'players' in lower): kind='role_icon'
        if kind and url not in {r['url'] for r in self.images}:
            self.images.append(dict(url=url,page=self.page,filename=filename,role=self.role,category=kind,character=self.character))

    def handle_data(self,data):
        if self.heading: self.text.append(data)

    def handle_endtag(self,tag):
        if tag==self.heading:
            level=int(tag[1]); self.sections={k:v for k,v in self.sections.items() if k<level}
            self.sections[level]=''.join(self.text); self.section=' '.join(self.sections.values()); self.heading=None


def public_fetch(url,limit=8_000_000):
    # Native proxy/DNS lookup can block a Python thread before its socket timeout.
    # Use a bounded, disposable standard-library-only process. It never writes files.
    # Files avoid an unbounded pipe-reader join after a Windows timeout.
    with tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as errors:
        process=subprocess.Popen([sys._base_executable,'-m','src.core.wiki_fetch',url,str(limit)],
            cwd=project_root(),stdout=output,stderr=errors,creationflags=0x08000000)
        try: process.wait(timeout=25)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=2)
            raise TimeoutError('Wiki request exceeded 25 seconds (including DNS/proxy lookup)')
        output.seek(0); errors.seek(0)
        if process.returncode: raise RuntimeError('wiki_fetch_failed: '+errors.read().decode('utf-8',errors='replace')[:500])
        packet=json.loads(output.read())
    if packet.get('status',0)>=400: raise HTTPError(url,packet['status'],'Wiki access error',{},None)
    if 'error' in packet: raise RuntimeError(packet['error'])
    return base64.b64decode(packet['body']),packet['content_type'],packet['effective']


def import_original(root,record,body):
    root=Path(root).resolve(); image=cv2.imdecode(np.frombuffer(body,np.uint8),cv2.IMREAD_UNCHANGED)
    if image is None or not 16<=min(image.shape[:2]) or max(image.shape[:2])>4096:
        raise ValueError('invalid_wiki_image')
    category=record['category']; character=record['character']
    if character:
        folder=root/'assets/killers/characters'/character
        if not folder.is_dir() or folder.parent.resolve()!=root/'assets/killers/characters': raise ValueError('unknown_character_folder')
        folder=folder/('addons' if category=='addons' else 'portrait')
    elif category=='role_icon': folder=root/'assets/survivors/role_icon'
    else:
        if record['role'] not in ('survivor','killer') or category not in ('items','addons','offerings','perks'): raise ValueError('invalid_category')
        folder=root/('assets/survivors' if record['role']=='survivor' else 'assets/killers')/category
    sha=hashlib.sha256(body).hexdigest(); suffix='.webp' if body[:4]==b'RIFF' else '.png'
    original=folder/'wiki_originals'/(sha+suffix); original.parent.mkdir(parents=True,exist_ok=True)
    if original.exists():
        if original.read_bytes()!=body: raise ValueError('original_hash_collision')
    else: original.write_bytes(body)
    # Preserve aspect ratio and alpha with padding; never modify the original.
    image=cv2.cvtColor(image,cv2.COLOR_GRAY2BGRA) if image.ndim==2 else cv2.cvtColor(image,cv2.COLOR_BGR2BGRA) if image.shape[2]==3 else image
    scale=96/max(image.shape[:2]); small=cv2.resize(image,(max(1,round(image.shape[1]*scale)),max(1,round(image.shape[0]*scale))),interpolation=cv2.INTER_AREA)
    canvas=np.zeros((96,96,4),np.uint8); y=(96-small.shape[0])//2; x=(96-small.shape[1])//2
    canvas[y:y+small.shape[0],x:x+small.shape[1]]=small
    normalized=folder/'wiki_normalized'/(sha+'.png'); write_image(normalized,canvas)
    return dict(**record,sha256=sha,original=original.relative_to(root).as_posix(),normalized=normalized.relative_to(root).as_posix(),
                original_size=list(image.shape[:2][::-1]),review_status='pending',old_id=None,
                wiki_id='wiki_'+record['role']+'_'+(character or 'common')+'_'+category+'_'+sha[:12])


def sync_wiki(root,fetch=public_fetch,pause=time.sleep,max_seconds=300,refresh=False):
    root=Path(root).resolve(); sources=character_sources(root)
    sources_path=root/'data/wiki_sources.json'; sources_path.write_text(json.dumps(dict(schema=1,characters=sources),indent=2),encoding='utf-8')
    path=root/'assets/wiki_manifest.json'
    previous=json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(schema=1,records=[])
    records=previous.get('records',[])
    def identity(r): return (r['url'],r['role'],r['category'],r['character'])
    by_key={identity(r):r for r in records}; by_url={r['url']:r for r in records}
    attempts=[]; errors=[]; denied=False; added=0; page_reports=[]; deadline=time.monotonic()+max_seconds
    pages=[dict(page=SITE+'/wiki/'+page,role=role,category=category,character=None) for page,role,category in
           (('Items','survivor','items'),('Add-ons','survivor','addons'),('Offerings','survivor','offerings'),('Offerings','killer','offerings'))]
    pages.extend(dict(page=s['page'],role='killer',category='addons',character=s['killer_id']) for s in sources)
    def page_key(s): return (s['page'],s['role'],s['category'],s['character'])
    cached={page_key(p):p for p in previous.get('page_reports',[]) if p.get('status')=='files_verified'}
    if not refresh:
        page_reports=[cached[page_key(s)] for s in pages if page_key(s) in cached and
            all((root/by_key[identity(r)]['original']).is_file() for r in records if
                r['page']==cached[page_key(s)]['effective'] and r['role']==s['role'] and r['character']==s['character'])]
    def checkpoint():
        report=dict(schema=1,updated=datetime.now(timezone.utc).isoformat(),records=records,attempts=attempts,errors=errors,
                    access_denied=denied,added=added,complete=False,page_reports=page_reports,
                    skipped_pages=[s['page'] for s in pages if page_key(s) not in {page_key(p) for p in page_reports if p['status']=='files_verified'}],
                    policy='Public HTTP only; four concurrent image requests. No login, challenges, proxies or bypass. Stop on HTTP 401/403/429.')
        temporary=path.with_suffix('.tmp'); temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); temporary.replace(path)
        return report
    for source in pages:
        if denied or time.monotonic()>=deadline: break
        if any(page_key(p)==page_key(source) for p in page_reports): continue
        try:
            body,content_type,effective=fetch(source['page']); attempts.append(dict(page=source['page'],status=200,effective=effective))
            parser=WikiImages(effective,source['role'],source['category'],source['character']); parser.feed(body.decode('utf-8'))
            page_report=dict(**source,effective=effective,discovered=len(parser.images),
                addon_urls=[r['url'] for r in parser.images if r['category']=='addons'],
                image_scope='current_page_section',status='in_progress')
            page_reports.append(page_report); checkpoint()
            pending=[r for r in parser.images if identity(r) not in by_key]
            def retrieve(record):
                existing=by_url.get(record['url'])
                if existing and (root/existing['original']).is_file():
                    return (root/existing['original']).read_bytes(),'image/cached',record['url']
                return fetch(record['url'])
            with ThreadPoolExecutor(max_workers=4) as pool:
                # Dispatch in groups to respect denial and the overall deadline.
                for start in range(0,len(pending),4):
                    if denied or time.monotonic()>=deadline: break
                    futures={pool.submit(retrieve,record):record for record in pending[start:start+4]}
                    for future in as_completed(futures):
                        record=futures[future]
                        try:
                            data,kind,effective_image=future.result()
                            if not kind.startswith('image/'): raise ValueError('not_an_image')
                            item=import_original(root,record,data); records.append(item)
                            by_key[identity(item)]=item; by_url[item['url']]=item; added+=1
                        except HTTPError as exc:
                            errors.append(dict(url=record['url'],http_status=exc.code)); denied=denied or exc.code in (401,403,429)
                        except Exception as exc: errors.append(dict(url=record['url'],error=str(exc)))
                        checkpoint()
                    pause(.25)
            page_report['downloaded']=sum(identity(r) in by_key for r in parser.images)
            page_report['status']='files_verified' if page_report['downloaded']==len(parser.images) and parser.images else 'partial_or_unavailable'
            checkpoint()
        except HTTPError as exc:
            attempts.append(dict(page=source['page'],status=exc.code)); denied=exc.code in (401,403,429)
        except Exception as exc:
            errors.append(dict(url=source['page'],error=str(exc)))
            # A failed connection is not a reason to spend 20 seconds on every
            # character on the same unreachable host. Persist and finish now.
            break
        pause(.25)
    return checkpoint()
