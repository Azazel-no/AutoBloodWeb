"""Versioned provenance and quality checks for every legacy tile, without deletion."""
import hashlib,json
from pathlib import Path
import cv2,numpy as np
from src.core.icon_library import interior_mask
from src.core.node_classifier import glyph
from src.utils.images import read_image


def audit_templates(library):
    root=library.root; sources={}; rows=[]
    truth=json.loads((root/'tests/fixtures/icon_validation_v3.json').read_text(encoding='utf-8'))
    manually_checked={ident for case in truth.values() for x,y,ident in case['icons'] if ident}
    for entry in library.entries:
        if not entry.source.startswith('references/inventory/'): continue
        issues=[]; crop=None; native_feature=None; valid_hash=False; reproducible=False
        source=root/entry.source
        if source.is_file():
            if entry.source not in sources: sources[entry.source]=read_image(source)
            raw=sources[entry.source]; x,y,w,h=entry.box
            if x<0 or y<0 or w<1 or h<1 or x+w>raw.shape[1] or y+h>raw.shape[0]: issues.append('crop_out_of_bounds')
            else:
                crop=raw[y:y+h,x:x+w]; valid_hash=hashlib.sha256(crop.tobytes()).hexdigest()==entry.fingerprint
                if not valid_hash: issues.append('source_crop_hash_changed')
                mask=interior_mask((h,w),entry.category=='offerings'); native_feature=glyph(crop,mask)
                if native_feature is None: issues.append('no_stable_source_glyph')
                expected=cv2.cvtColor(crop,cv2.COLOR_BGR2BGRA); expected[:,:,3]=mask
                expected=cv2.resize(expected,(96,96),interpolation=cv2.INTER_AREA)
                cached=cv2.imdecode(np.fromfile(root/entry.image,np.uint8),cv2.IMREAD_UNCHANGED)
                reproducible=cached is not None and cached.shape==expected.shape and np.array_equal(cached,expected)
                if not reproducible: issues.append('cached_tile_differs_from_source')
        else: issues.append('source_missing')
        rows.append(dict(old_id=entry.id,active_id=entry.id,source=entry.source,box=entry.box,
            source_crop_hash_valid=valid_hash,cached_tile_reproducible=reproducible,
            source_resolution=list(crop.shape[:2][::-1]) if crop is not None else None,
            original=entry.image,native_reference=dict(source=entry.source,box=entry.box),
            identity_review='confirmed_on_labeled_bloodweb' if entry.id in manually_checked else 'not_manually_confirmed',
            wiki_id=None,wiki_comparison='mapping_review_pending',keep_legacy_tile=True,
            use_native_detail=not issues and native_feature is not None,issues=issues))
    wiki_path=root/'assets/wiki_manifest.json'
    wiki=json.loads(wiki_path.read_text(encoding='utf-8')) if wiki_path.exists() else {}
    report=dict(schema=1,version='v4_user_native',entries=rows,count=len(rows),
        structurally_valid=sum(not r['issues'] for r in rows),identity_checked=sum(r['identity_review']=='confirmed_on_labeled_bloodweb' for r in rows),
        wiki_originals=len({r['url'] for r in wiki.get('records',[])}),limitations='Structural crop checks do not prove the identity of every unlabeled icon.')
    path=root/'assets/reference_versions/v4_user_native.json'; path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
