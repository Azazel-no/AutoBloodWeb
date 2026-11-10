"""Reproduce Wiki game-icon layers, with explicit HTML/CSS provenance.

Portraits are strictly character portraits. Colour is obtained from the table's
CSS class and the Wiki's own background PNG, never from a guessed filename.
"""
import base64,hashlib,json,re
from pathlib import Path
from urllib.parse import unquote,urlsplit
from urllib.error import HTTPError
from PIL import Image
import io
import cv2,numpy as np
from src.core.wiki_library import WikiImages,public_fetch,character_sources,SITE,original_url
from src.core.icon_library import rarity_of

CSS_URL=SITE+'/MediaWiki:Base64Files.css?action=raw&ctype=text/css'
BACKGROUNDS=re.compile(r'^(common|uncommon|rare|very-rare|visceral|unused|event|limited)-(item|addon|offering)-element$')


def portrait_character(filename):
    match=re.fullmatch(r'K\d+_The(.+)_Portrait\.(?:png|webp)',filename,re.I)
    return match.group(1).replace('_','').lower() if match else None


class LayeredWikiImages(WikiImages):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs); self.divs=[]

    def handle_starttag(self,tag,attrs):
        a=dict(attrs); classes=a.get('class','').split()
        if tag in ('div','span'):
            context=dict(background=None,marker=False,inline=tag=='span') if 'game-element-container' in classes else None
            self.divs.append((tag,context))
            container=next((v for t,v in reversed(self.divs) if v is not None),None)
            if container is not None:
                for value in classes:
                    if BACKGROUNDS.fullmatch(value): container['background']=value
                    if value=='addon-marker': container['marker']=True
        old=len(self.images); super().handle_starttag(tag,attrs)
        if len(self.images)>old:
            record=self.images[-1]
            if record['category']=='portrait':
                if portrait_character(record['filename'])!=(self.character or '').replace('_','').lower(): self.images.pop()
            else:
                context=next((v for t,v in reversed(self.divs) if v is not None),None)
                if context: record.update(background_class=context['background'],addon_marker=context['marker'])
        elif tag=='img':
            context=next((v for t,v in reversed(self.divs) if v is not None),None)
            url=original_url(a.get('data-src') or a.get('src',''),self.page)
            if context and not context['inline']:
                # A description may mention another add-on before its table row.
                # Prefer the complete main-row assembly over that inline icon.
                record=next((r for r in self.images if r['url']==url),None)
                if record: record.update(background_class=context['background'],addon_marker=context['marker'])

    def handle_endtag(self,tag):
        super().handle_endtag(tag)
        if tag in ('div','span'):
            for index in range(len(self.divs)-1,-1,-1):
                if self.divs[index][0]==tag:
                    del self.divs[index:]; break


def css_layers(css):
    # Older yellow/green textures remain commented out in the Wiki stylesheet.
    # They are not current CSS declarations and must not override live variables.
    css=re.sub(r'/\*.*?\*/','',css,flags=re.S)
    layers={}
    for key,value in re.findall(r'--([a-z-]+)\s*:\s*url\(([^)]+)\)',css):
        value=value.strip().strip('\'"')
        if not value.startswith('data:image/png;base64,'): continue
        layers[key]=base64.b64decode(value.split(',',1)[1],validate=True)
    return layers


def compose_icon(glyph_bytes,background_bytes,marker_bytes=None,size=256):
    def rgba(body): return Image.open(io.BytesIO(body)).convert('RGBA')
    background=rgba(background_bytes); glyph=rgba(glyph_bytes)
    canvas=Image.new('RGBA',(size,size))
    # Source CSS: addon-container scale:86.5%, img fills the square assembly.
    factor=.865 if marker_bytes else 1.; side=round(size*factor); inset=(size-side)//2
    bg=background.copy(); bg.thumbnail((side,side),Image.Resampling.LANCZOS)
    canvas.alpha_composite(bg,(inset+(side-bg.width)//2,inset+(side-bg.height)//2))
    glyph.thumbnail((side,side),Image.Resampling.LANCZOS)
    canvas.alpha_composite(glyph,(inset+(side-glyph.width)//2,inset+(side-glyph.height)//2))
    if marker_bytes:
        marker=rgba(marker_bytes)
        # Wiki's 128px assembly: 76x90 marker, 0.5 scale, top:-35/right:-31,
        # transform origin at its centre. Preserve the source PNG's proportions.
        ratio=size/128*factor
        marker=marker.resize((round(38*ratio),round(45*ratio)),Image.Resampling.LANCZOS)
        x=round(inset+102*ratio); y=round(inset-12.5*ratio)
        canvas.alpha_composite(marker,(x,y))
    return canvas


def repair_wiki_assets(library,character_ids=None,fetch=public_fetch):
    root=library.root; cache=root/'debug/v42/wiki_html'; cache.mkdir(parents=True,exist_ok=True)
    path=root/'assets/wiki_ui_manifest.json'
    report=json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(schema=1,version='v4.2',portraits={},icons={},characters={},errors=[])
    report['errors']=[]; records=json.loads((root/'assets/wiki_manifest.json').read_text(encoding='utf-8'))['records']
    by_url={r['url']:r for r in records}
    def body(url):
        record=by_url.get(url)
        if record:
            source=root/record['original']
            if source.exists() and hashlib.sha256(source.read_bytes()).hexdigest()==record['sha256']: return source.read_bytes()
        data,kind,effective=fetch(url)
        if not kind.startswith('image/'): raise ValueError('not_an_image')
        return data
    def checkpoint():
        temp=path.with_suffix('.tmp'); temp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); temp.replace(path)
    css_path=cache/'base64.css'
    if not css_path.exists(): css_path.write_bytes(fetch(CSS_URL)[0])
    css=css_path.read_text(encoding='utf-8'); layers=css_layers(css)
    layer_paths={}; folder=root/'assets/ui/wiki_layers'; folder.mkdir(parents=True,exist_ok=True)
    for key,data in layers.items():
        if key!='addon-marker' and not key.endswith(('-item-bg','-offering-bg','-addon-bg')): continue
        target=folder/(key+'.png')
        if not target.exists(): target.write_bytes(data)
        layer_paths[key]=target.relative_to(root).as_posix()
    report['css_source']=dict(url=CSS_URL,sha256=hashlib.sha256(css_path.read_bytes()).hexdigest(),
        page_styles=SITE+'/load.php?lang=en-gb&modules=site.styles&only=styles&skin=vector',layers=layer_paths)
    sources=character_sources(root)
    if character_ids is not None: sources=[s for s in sources if s['killer_id'] in character_ids]
    sources.extend(dict(killer_id=None,page=SITE+'/wiki/'+page,role=role,category=category) for page,role,category in
        (('Items','survivor','items'),('Add-ons','survivor','addons'),('Offerings','survivor','offerings'),('Offerings','killer','offerings')) if character_ids is None)
    for source in sources:
        character=source['killer_id']; role=source.get('role','killer'); category=source.get('category','addons')
        try:
            html_path=cache/((character or f'{role}_{category}')+'.html')
            if not html_path.exists(): html_path.write_bytes(fetch(source['page'])[0])
            parser=LayeredWikiImages(source['page'],role,category,character); parser.feed(html_path.read_text(encoding='utf-8'))
            found=[]; misses=[]
            for record in parser.images:
                if record['category']=='portrait':
                    data=body(record['url']); raw=Image.open(io.BytesIO(data))
                    if raw.width!=raw.height or raw.width<256: raise ValueError('unexpected_portrait_format')
                    target=root/'assets/ui/killers'/(character+'.png'); target.parent.mkdir(parents=True,exist_ok=True)
                    if raw.format=='PNG': target.write_bytes(data)
                    else: raw.save(target)
                    report['portraits'][character]=dict(path=target.relative_to(root).as_posix(),url=record['url'],page=record['page'],
                        filename=record['filename'],type='official_K_series_portrait',sha256=hashlib.sha256(target.read_bytes()).hexdigest())
                    continue
                if record['category']=='role_icon':
                    if 'survivor' in record['filename'].lower():
                        target=root/'assets/ui/roles/survivor.png'; target.parent.mkdir(parents=True,exist_ok=True)
                        data=body(record['url']); Image.open(io.BytesIO(data)).save(target)
                        report['portraits']['survivor']=dict(path=target.relative_to(root).as_posix(),url=record['url'],type='role_icon')
                    continue
                old=next((r for r in records if r['url']==record['url'] and r['role']==role and r['character']==character and r['category']==record['category']),None)
                if old is None: misses.append(record['url']); continue  # No new ID or duplicate library here.
                background_class=record.get('background_class'); match=BACKGROUNDS.fullmatch(background_class or '')
                if not match: misses.append(record['url']); continue
                variable=match.group(1)+'-'+match.group(2)+'-bg'; background=layers.get(variable)
                if background is None: misses.append(record['url']); continue
                marker=layers.get('addon-marker') if record.get('addon_marker') else None
                original=body(record['url']); composed=compose_icon(original,background,marker)
                addon_folder=root/old['original']; addon_folder=addon_folder.parent.parent
                target=addon_folder/'original_color'/(old['wiki_id']+'.png'); target.parent.mkdir(parents=True,exist_ok=True)
                composed.save(target)
                raw_bg=cv2.imdecode(np.frombuffer(background,np.uint8),cv2.IMREAD_UNCHANGED)
                report['icons'][old['wiki_id']]=dict(path=target.relative_to(root).as_posix(),glyph=old['original'],
                    glyph_url=record['url'],page=source['page'],background_class=background_class,background=layer_paths[variable],
                    marker=layer_paths.get('addon-marker') if marker else None,rarity=rarity_of(raw_bg[:,:,:3],raw_bg[:,:,3]),
                    table_rarity=match.group(1),source='composed_from_verified_Wiki_PNG_and_CSS_layers',
                    source_html_sha256=hashlib.sha256(html_path.read_bytes()).hexdigest(),sha256=hashlib.sha256(target.read_bytes()).hexdigest())
                found.append(old['wiki_id'])
            if character:
                report['characters'][character]=dict(page=source['page'],colored=len(found),missing=misses,
                    portrait=character in report['portraits'],complete=len(found)>0 and not misses)
                if character not in report['portraits']: report['errors'].append(dict(character=character,error='portrait_missing_no_power_icon_fallback'))
            checkpoint()
        except Exception as exc:
            report['errors'].append(dict(character=character,page=source['page'],error=str(exc))); checkpoint()
            if isinstance(exc,HTTPError) and exc.code in (401,403,429): break
    checkpoint(); return report
