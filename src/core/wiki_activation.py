"""Verify downloaded files and publish character libraries without changing legacy IDs."""
import hashlib,json
from pathlib import Path
from urllib.parse import urlsplit,unquote
import cv2,numpy as np
from src.core.models import IconEntry
from src.core.node_classifier import glyph
from src.core.wiki_library import is_addon_filename
from src.core.wiki_assets import portrait_character


def verify_wiki(library):
    root=library.root; path=root/'assets/wiki_manifest.json'
    if not path.exists(): return {}
    manifest=json.loads(path.read_text(encoding='utf-8')); records=manifest['records']; valid=[]; errors=[]
    for record in records:
        original=(root/record['original']).resolve(); normalized=(root/record['normalized']).resolve()
        if not original.is_relative_to(root/'assets') or not normalized.is_relative_to(root/'assets'):
            errors.append(record['url']); continue
        if not original.exists() or hashlib.sha256(original.read_bytes()).hexdigest()!=record['sha256']:
            errors.append(record['url']); continue
        raw=cv2.imdecode(np.fromfile(original,np.uint8),cv2.IMREAD_UNCHANGED)
        derived=cv2.imdecode(np.fromfile(normalized,np.uint8),cv2.IMREAD_UNCHANGED) if normalized.exists() else None
        if raw is None or derived is None or derived.shape!=(96,96,4): errors.append(record['url']); continue
        valid.append(record)
    complete={p['character']:p for p in manifest.get('page_reports',[]) if p.get('character') and p['status']=='files_verified' and p['addon_urls']}
    entries=[]; portraits={}; status={}; suggestions=[]; references={}
    previous=root/'assets/wiki_active.json'
    if previous.exists():
        references=json.loads(previous.read_text(encoding='utf-8')).get('additional_references',{})
    # All original legacy IDs remain the active IDs, including Blight and priorities.
    for character,page in complete.items():
        addon_urls={u for u in page['addon_urls'] if is_addon_filename(unquote(urlsplit(u).path.rsplit('/',1)[-1]))}
        images=[r for r in valid if r['character']==character and r['category']=='addons' and r['url'] in addon_urls]
        if not images or len(images)!=len(addon_urls): continue
        status[character]=dict(addons=len(images),source='Wiki current add-on section',files_verified=True,gameplay_verified=False,page=page['effective'])
        if any(e.source.startswith('references/inventory/') for e in library.filter('killer','addons',character)): continue
        for record in images:
            raw=cv2.imdecode(np.fromfile(root/record['original'],np.uint8),cv2.IMREAD_UNCHANGED)
            if raw.ndim==2: raw=cv2.cvtColor(raw,cv2.COLOR_GRAY2BGRA)
            entries.append(IconEntry(record['wiki_id'],'killer','addons',character,record['normalized'],
                record['original'],[0,0,raw.shape[1],raw.shape[0]],'unknown',hashlib.sha256(raw[:,:,:3].tobytes()).hexdigest()).__dict__)
    for record in valid:
        if (record['category']=='portrait' and record['character'] in complete and
                portrait_character(record['filename'])==record['character'].replace('_','').lower()):
            portraits.setdefault(record['character'],record['normalized'])
        elif record['category']=='role_icon' and record['role']=='survivor':
            if 'survivor' in record['filename'].lower() or 'survivor' not in portraits: portraits['survivor']=record['normalized']
    # Produce review suggestions rather than introducing a second icon for the
    # same legacy item or silently reassigning a saved priority.
    legacy=[]
    for entry in library.entries:
        if not entry.source.startswith('references/inventory/'): continue
        raw=cv2.imdecode(np.fromfile(root/entry.image,np.uint8),cv2.IMREAD_UNCHANGED)
        feature=glyph(raw[:,:,:3],raw[:,:,3] if raw.shape[2]==4 else None)
        if feature is not None: legacy.append((entry,feature.astype(np.float32)/255))
    for record in valid:
        if record['category'] not in ('items','addons','offerings'): continue
        candidates=[(e,f) for e,f in legacy if e.role==record['role'] and e.category==record['category'] and e.character==record['character']]
        if not candidates: continue
        raw=cv2.imdecode(np.fromfile(root/record['original'],np.uint8),cv2.IMREAD_UNCHANGED)
        if raw.ndim==2: raw=cv2.cvtColor(raw,cv2.COLOR_GRAY2BGRA)
        feature=glyph(raw[:,:,:3],raw[:,:,3] if raw.shape[2]==4 else None)
        if feature is None: continue
        f=feature.astype(np.float32)/255
        scores=sorted([(float(2*np.sum(f*g)/(np.sum(f*f)+np.sum(g*g)+1e-6)),e.id) for e,g in candidates],reverse=True)
        score,ident=scores[0]; gap=score-scores[1][0] if len(scores)>1 else score
        suggestions.append(dict(old_id=ident,wiki_id=record['wiki_id'],original=record['original'],page=record['page'],url=record['url'],
            glyph_similarity=round(score,4),margin=round(gap,4),status='manual_review_required'))
    report=dict(schema=1,version='wiki_v4',entries=entries,portraits=portraits,character_status=status,
        records_verified=len(valid),errors=errors,legacy_mapping_suggestions=suggestions,
        additional_references=references,
        limitations='Files and page scope verified; recognition on killer Bloodwebs is unverified. Legacy alias suggestions are not automatic identity approvals.')
    active=root/'assets/wiki_active.json'; temp=active.with_suffix('.tmp'); temp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); temp.replace(active)
    mapping=root/'data/wiki_sources.json'
    if mapping.exists():
        data=json.loads(mapping.read_text(encoding='utf-8'))
        for character in data['characters']:
            verified=status.get(character['killer_id'])
            character['verification']=verified or dict(files_verified=False,gameplay_verified=False)
        mapping.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
