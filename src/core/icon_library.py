"""Extract the user's inventory tiles, preserving source provenance and IDs."""
import hashlib
import json
import re
from pathlib import Path

import cv2
import numpy as np

from src.core.models import IconEntry
from src.utils.images import read_image, write_image

SCHEMA_VERSION = 1
EXTRACTOR_VERSION = 2


def source_group(path: Path) -> tuple[str, str, str | None, str]:
    name = path.stem.lower().lstrip('- _')
    if 'survivor' in name:
        for category in ('items', 'addons', 'offerings'):
            if category in name:
                return 'survivor', category, None, f'assets/survivors/{category}'
    if 'killer' in name and 'offering' in name:
        return 'killer', 'offerings', None, 'assets/killers/offerings'
    character=re.search(r'killer_([a-z][a-z0-9_]*)_addons?(?:_|$)',name)
    if character:
        ident=character.group(1)
        return 'killer', 'addons', ident, f'assets/killers/characters/{ident}/addons'
    raise ValueError(f"Unrecognized inventory source: {path.name}")


def extract_boxes(image: np.ndarray, hexagonal: bool = False) -> list[tuple[int, int, int, int]]:
    """Recover a grid from actual dark tile frames, then reject empty cells.

    Row 2 of offerings is staggered. Grid geometry comes from this image,
    rather than preset pixel coordinates. Quantity text is outside the useful
    polygon and does not participate in the empty-cell test.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    dark = (gray < 32).astype(np.uint8) * 255
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = [cv2.boundingRect(c) for c in contours]
    h, w = image.shape[:2]
    candidates = [b for b in candidates if .085*w < b[2] < .20*w
                  and .16*h < b[3] < .40*h and .7 < b[2]/b[3] < 1.3]
    if len(candidates) < 2:
        raise ValueError('Inventory grid not found; a manual crop is required.')
    top = min(b[1] for b in candidates)
    first = sorted([b for b in candidates if abs(b[1]-top) < .2*b[3]], key=lambda b: b[0])
    if len(first) < 2:
        raise ValueError('Cannot infer inventory column spacing.')
    tile_w = int(round(np.median([b[2] for b in first])))
    tile_h = int(round(np.median([b[3] for b in first])))
    step_x = float(np.median(np.diff([b[0]+b[2]/2 for b in first])))
    center_x = float(np.median([b[0]+b[2]/2-i*step_x for i,b in enumerate(first)]))
    top = float(np.median([b[1] for b in first]))
    next_tops = [b[1] for b in candidates if .75*tile_h < b[1]-top < 1.5*tile_h]
    step_y = float(np.median(next_tops)-top) if next_tops else tile_h*(1.04 if hexagonal else 1.14)
    columns = len(first)
    rows = max(1, int(round((h-top)/step_y)))
    result = []
    for row in range(rows):
        for col in range(columns):
            x = int(round(center_x+col*step_x-tile_w/2 + (step_x/2 if hexagonal and row%2 else 0)))
            y = int(round(top+row*step_y))
            if x < 0 or x+tile_w > w+3 or y+tile_h*.65 > h:
                continue
            box = x, y, min(tile_w, w-x), min(tile_h, h-y)
            tile = image[y:y+box[3],x:x+box[2]]
            mask = interior_mask(tile.shape[:2], hexagonal)
            hsv = cv2.cvtColor(tile, cv2.COLOR_BGR2HSV)
            white = (hsv[:,:,2]>170)&(hsv[:,:,1]<95)&(mask>0)
            if np.count_nonzero(white) > tile_w*tile_h*.016:
                result.append(box)
    return result


def interior_mask(shape: tuple[int, int], hexagonal: bool) -> np.ndarray:
    h,w = shape
    mask = np.zeros((h,w),np.uint8)
    if hexagonal:
        points = np.array([[.5,.08],[.89,.29],[.89,.71],[.5,.91],[.1,.71],[.1,.29]])
        cv2.fillConvexPoly(mask,(points*np.array([w,h])).astype(np.int32),255)
    else:
        cv2.rectangle(mask,(int(.08*w),int(.08*h)),(int(.92*w),int(.92*h)),255,-1)
    # Quantity digits sit in the lower right and must never become features.
    mask[int(h*.83):,int(w*.80):] = 0
    return mask


def rarity_of(image: np.ndarray, mask: np.ndarray | None = None) -> str:
    hsv = cv2.cvtColor(image,cv2.COLOR_BGR2HSV)
    valid = (hsv[:,:,1]>65)&(hsv[:,:,2]>25)
    if mask is not None:
        valid &= mask>0
    if np.count_nonzero(valid) < 20:
        return 'unknown'
    hue = float(np.median(hsv[:,:,0][valid]))
    return ('pink' if hue>155 or hue<8 else 'purple' if hue>125 else
            'blue' if hue>85 else 'green' if hue>32 else 'brown')


class IconLibrary:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.manifest_path = self.root/'assets/manifest.json'
        self.entries: list[IconEntry] = []
        self.warnings: list[str] = []
        self.rebuilt = False

    def build(self, force: bool = False) -> 'IconLibrary':
        sources = sorted((self.root/'references/inventory').glob('*'),key=lambda p:p.name.lstrip('- _'))
        sources = [p for p in sources if p.suffix.lower() in ('.png','.webp','.jpg','.jpeg')]
        hashes = {p.relative_to(self.root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
        previous = {}
        if self.manifest_path.is_file():
            try:
                previous = json.loads(self.manifest_path.read_text(encoding='utf-8'))
            except (ValueError,OSError):
                self.warnings.append('Manifest unreadable; rebuilding from references.')
        if not force and previous.get('extractor_version') == EXTRACTOR_VERSION and previous.get('sources') == hashes:
            entries = [IconEntry(**e) for e in previous.get('icons',[])]
            if entries and all((self.root/e.image).is_file() for e in entries):
                self.entries = entries
                self.warnings.extend(previous.get('warnings',[]))
                return self.load_wiki()
        if not sources:
            raise FileNotFoundError('references/inventory contains no images.')
        counters: dict[str,int] = {}
        old_ids = {e['fingerprint']: e['id'] for e in previous.get('icons',[])}
        for e in previous.get('icons',[]):
            prefix,number = e['id'].rsplit('_',1)
            counters[prefix] = max(counters.get(prefix,0),int(number))
        entries = []
        for source in sources:
            try:
                role,category,character,folder = source_group(source)
                image = read_image(source)
                boxes = extract_boxes(image,category=='offerings')
            except ValueError as exc:
                self.warnings.append(str(exc))
                continue
            for x,y,w,h in boxes:
                tile = image[y:y+h,x:x+w]
                mask = interior_mask(tile.shape[:2],category=='offerings')
                fingerprint = hashlib.sha256(tile.tobytes()).hexdigest()
                prefix = f"{role}_{character+'_' if character else ''}{category[:-1]}"
                ident = old_ids.get(fingerprint)
                if ident is None:
                    counters[prefix] = counters.get(prefix,0)+1
                    ident = f'{prefix}_{counters[prefix]:03d}'
                path = f'{folder}/{ident}.png'
                bgra = cv2.cvtColor(tile,cv2.COLOR_BGR2BGRA)
                bgra[:,:,3] = mask
                bgra = cv2.resize(bgra,(96,96),interpolation=cv2.INTER_AREA)
                write_image(self.root/path,bgra)
                entries.append(IconEntry(ident,role,category,character,path,
                                        source.relative_to(self.root).as_posix(),[x,y,w,h],rarity_of(tile,mask),fingerprint))
        if not entries:
            raise ValueError('No icons extracted from inventory references.')
        data = {'schema_version':SCHEMA_VERSION,'extractor_version':EXTRACTOR_VERSION,
                'sources':hashes,'warnings':self.warnings,'icons':[e.__dict__ for e in entries]}
        self.manifest_path.parent.mkdir(parents=True,exist_ok=True)
        temporary = self.manifest_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(self.manifest_path)
        self.entries, self.rebuilt = entries, True
        return self.load_wiki()

    def load_wiki(self):
        path=self.root/'assets/wiki_active.json'
        self.wiki_status={}; self.portraits={}; self.wiki_records_verified=0; self.wiki_references={}
        if not path.exists(): return self.load_ui_assets()
        try:
            data=json.loads(path.read_text(encoding='utf-8'))
            known={e.id for e in self.entries}
            for item in data.get('entries',[]):
                entry=IconEntry(**item)
                if entry.id not in known and (self.root/entry.image).is_file():
                    self.entries.append(entry); known.add(entry.id)
            self.wiki_status=data.get('character_status',{}); self.portraits=data.get('portraits',{})
            self.wiki_records_verified=data.get('records_verified',0); self.wiki_references=data.get('additional_references',{})
        except (ValueError,TypeError,OSError) as exc: self.warnings.append('Wiki active manifest unavailable: '+str(exc))
        return self.load_ui_assets()

    def load_ui_assets(self):
        # Individually reviewed missing icons use the existing Wiki files/IDs.
        # Never activate every downloaded general asset or duplicate a legacy alias.
        extension=self.root/'assets/additional_icons.json'
        if extension.is_file():
            try:
                extra=json.loads(extension.read_text(encoding='utf-8'))
                known={e.id for e in self.entries}
                aliases={ref.get('wiki_id') for refs in self.wiki_references.values() for ref in refs}
                for item in extra.get('entries',[]):
                    entry=IconEntry(**item)
                    if entry.id in known or entry.id in aliases: continue
                    if entry.role!='survivor' or entry.category!='addons' or entry.character is not None: continue
                    if not (self.root/entry.image).is_file():continue
                    if hashlib.sha256((self.root/entry.source).read_bytes()).hexdigest()!=entry.fingerprint: continue
                    self.entries.append(entry);known.add(entry.id)
            except (OSError,ValueError,TypeError) as exc:self.warnings.append('Additional icon manifest unavailable: '+str(exc))
        path=self.root/'assets/wiki_ui_manifest.json'; self.display_images={}
        if not path.exists():
            from src.core.event_library import load_events
            load_events(self);return self
        try:
            data=json.loads(path.read_text(encoding='utf-8'))
            # A missing real portrait is empty; never substitute a power symbol.
            self.portraits={key:value['path'] for key,value in data.get('portraits',{}).items()
                if (self.root/value['path']).is_file() and value.get('type') in ('official_K_series_portrait','role_icon')}
            icons=data.get('icons',{})
            for entry in self.entries:
                record=icons.get(entry.id)
                if record:
                    self.display_images[entry.id]=record['path']
                    # Native glyph templates stay intact; colour only supplies
                    # the same additional rarity feature used by the matcher.
                    entry.rarity=record['rarity']
                elif entry.id in getattr(self,'wiki_references',{}):
                    approved=self.wiki_references[entry.id]
                    reference=next((icons.get(r['wiki_id']) for r in approved if r['wiki_id'] in icons),None)
                    if reference: self.display_images[entry.id]=reference['path']
        except (OSError,ValueError,KeyError) as exc: self.warnings.append('Wiki UI manifest unavailable: '+str(exc))
        from src.core.event_library import load_events
        load_events(self)
        return self

    def filter(self, role: str, category: str | None = None, character: str = 'blight') -> list[IconEntry]:
        return [e for e in self.entries if e.role==role and (category is None or e.category==category or category=='event' and e.id in getattr(self,'event_metadata',{}))
                and (e.character is None or e.character==character)]
