"""File and page coverage, distinct from unproven gameplay recognition."""
import hashlib,json
import cv2,numpy as np


def audit_libraries(library):
    path=library.root/'assets/wiki_manifest.json'
    manifest=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    records=manifest.get('records',[]); pages=manifest.get('page_reports',[])
    reports=[]
    for folder in sorted((library.root/'assets/killers/characters').iterdir()):
        if not folder.is_dir(): continue
        files=sorted((folder/'addons').rglob('*')); images=[]; errors=[]; tiny=[]; hashes=[]
        for path in files:
            if path.suffix.lower() not in ('.png','.webp'): continue
            image=cv2.imdecode(np.fromfile(path,np.uint8),cv2.IMREAD_UNCHANGED)
            relative=path.relative_to(library.root).as_posix()
            if image is None: errors.append(relative); continue
            images.append(relative); hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
            if min(image.shape[:2])<32: tiny.append(relative)
        entries=library.filter('killer','addons',folder.name)
        page=next((p for p in pages if p.get('character')==folder.name),{})
        originals=[r for r in records if r['character']==folder.name and r['category']=='addons']
        active=library.wiki_status.get(folder.name,{})
        reports.append(dict(killer_id=folder.name,wiki_page=page.get('effective'),
            wiki_status=page.get('status','not_attempted'),
            addons_discovered_on_wiki=active.get('addons',len(page.get('addon_urls',[]))),downloaded_images=len(originals),
            existing_images=len(images),registered_icons=len(entries),corrupt_files=errors,
            missing_manifest_files=[e.image for e in entries if not (library.root/e.image).is_file()],
            duplicate_file_hashes=len(hashes)-len(set(hashes)),suspiciously_small=tiny,
            completeness='files_verified_gameplay_unverified' if active.get('files_verified') else 'not_verified',
            portrait=library.portraits.get(folder.name),source='wiki_and_preserved_user_inventory' if originals else 'user_inventory_or_empty'))
    return dict(characters=len(reports),images_added=len(records),portraits_added=sum(r['category']=='portrait' for r in records),
        recorded_access_date=manifest.get('updated'),network_access='Public HTTP; recorded results, not a new network check',
        access_denied=manifest.get('access_denied'),errors=manifest.get('errors',[]),killers=reports)
