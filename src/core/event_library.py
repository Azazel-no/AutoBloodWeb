"""Verified concrete event icons in the existing library; no virtual identity."""
import hashlib,json
from pathlib import Path
from src.core.models import IconEntry


def load_events(library):
    library.event_metadata={}
    path=library.root/'assets/event_catalog.json'
    if not path.is_file():return
    try:
        data=json.loads(path.read_text(encoding='utf-8'));known={e.id for e in library.entries}
        for record in data['records']:
            source=library.root/record['original'];normal=library.root/record['normalized'];display=library.root/record['display']
            if any(not p.resolve().is_relative_to(library.root/'assets') for p in (source,normal,display)):raise ValueError('event_path')
            if any(hashlib.sha256(p.read_bytes()).hexdigest()!=record[k] for p,k in [(source,'sha256'),(normal,'normalized_sha256'),(display,'display_sha256')]):raise ValueError('event_hash')
            for item in record['entries']:
                e=IconEntry(**item)
                if e.category not in ('items','addons','offerings') or e.role not in record['roles'] or e.character is not None:raise ValueError('event_scope')
                if e.source!=record['original'] or e.image!=record['normalized'] or e.fingerprint!=record['sha256']:raise ValueError('event_provenance')
                if e.id not in known:library.entries.append(e);known.add(e.id)
                library.event_metadata[e.id]={k:v for k,v in record.items() if k!='entries'}
                library.display_images.setdefault(e.id,record['display'])
    except (OSError,ValueError,KeyError,TypeError) as exc:library.warnings.append('Event catalog unavailable: '+str(exc))
