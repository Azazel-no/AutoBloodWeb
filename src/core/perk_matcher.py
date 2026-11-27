"""Identify a perk slot with the existing IconMatcher and its unchanged thresholds.

Matching is by icon image. A few perks share one icon, and the overlay only has
to draw that icon, so sharing is not treated as ambiguity. Anything not accepted
stays None and is drawn as "?", never guessed.
"""
from dataclasses import dataclass
from hashlib import sha256
import logging
from pathlib import Path
import cv2
import numpy as np
from src.core.models import IconEntry
from src.core.node_classifier import IconMatcher,glyph
from src.core.perk_catalog import TAB_REFERENCES, load_catalog

log = logging.getLogger(__name__)


# L1 (16.11): how a perk icon looks on the Tab scoreboard. The icon covers 48.5 of the 52 px
# tile at 100 % UI scale and is drawn brighter than the Wiki art (gain fitted on real tiles).
TAB_TILES=(52,47,112)    # Tab tiles at 100 % and 90 % UI scale, and the own HUD perks
ICON_SHARE=48.5/52
ICON_GAIN=1.6
RENDER_BACKGROUND=(70,45,70)  # A flat dark diamond; the rarity colour itself is not modelled.


def render_tab_tile(icon,tile,background=None):
    """The perk icon (BGRA Wiki art) drawn on a background the size of a Tab tile."""
    if background is None:
        base=np.empty((tile,tile,3),np.float32);base[:]=RENDER_BACKGROUND
    else:base=cv2.resize(background[:,:,:3],(tile,tile),interpolation=cv2.INTER_AREA).astype(np.float32)
    size=round(tile*ICON_SHARE);art=cv2.resize(icon,(size,size),interpolation=cv2.INTER_AREA).astype(np.float32)
    alpha=art[:,:,3:4]/255 if art.shape[2]==4 else np.ones((size,size,1),np.float32);at=(tile-size)//2+1
    base[at:at+size,at:at+size]=np.minimum(255,art[:,:,:3]*ICON_GAIN)*alpha+base[at:at+size,at:at+size]*(1-alpha)
    return np.clip(base,0,255).astype(np.uint8)


def tile_feature(tile):
    """The glyph of a square perk tile crop, normalised to 128 px the way identify reads it."""
    size=max(tile.shape[:2])
    resized=cv2.resize(tile[:,:,:3],(128,128),interpolation=cv2.INTER_AREA if size>128 else cv2.INTER_CUBIC)
    return resized,glyph(resized,diamond_mask(128))


class PerkLibrary:
    """The small IconLibrary surface IconMatcher reads, for perks only."""
    # Perk tiles are never rotated; the Wiki art keeps small scale variants, the renders need none.
    variant_transforms=((0,.94),(0,1.06))

    def __init__(self,root,role):
        self.root=Path(root);self.perks_by_icon={}
        for perk in load_catalog(root,role):self.perks_by_icon.setdefault('perk_icon_'+perk.sha256[:16],[]).append(perk)
        self.entries=[IconEntry(ident,role,'perks',None,perks[0].icon,perks[0].original,[],'unknown',perks[0].sha256)
                      for ident,perks in sorted(self.perks_by_icon.items())]
        self.rendered_features={}
        for entry in self.entries:
            path=self.root/entry.source
            icon=cv2.imdecode(np.fromfile(path,np.uint8),cv2.IMREAD_UNCHANGED) if path.is_file() else None
            features = []
            if icon is not None and icon.ndim == 3:
                features.extend(tile_feature(render_tab_tile(icon, size))[1] for size in TAB_TILES)
            for perk in self.perks_by_icon[entry.id]:
                if perk.id in TAB_REFERENCES:
                    features.append(native_tab_feature(self.root, *TAB_REFERENCES[perk.id]))
            self.rendered_features[entry.id]=[f for f in features if f is not None]

    def filter(self,role,character=None):
        return [e for e in self.entries if e.role==role]


def native_tab_feature(root, path, expected_sha):
    try:
        data = (root / path).read_bytes()
        if sha256(data).hexdigest() == expected_sha:
            image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if image is not None and image.shape[:2] == (52, 52):
                return tile_feature(image)[1]
    except OSError:
        pass
    log.warning('Perk Tab reference unavailable or invalid: %s', path)
    return None


def diamond_mask(size,margin=.2):
    """The perk tile interior, leaving out the diamond's coloured frame."""
    yy,xx=np.ogrid[:size,:size];center=(size-1)/2
    return ((np.abs(xx-center)+np.abs(yy-center))<=center*(1-margin)).astype(np.uint8)*255


@dataclass(frozen=True)
class SlotResult:
    icon_id: str | None
    image: str | None
    names: tuple[str,...]
    score: float
    margin: float
    reason: str
    candidates: tuple = ()  # Top (name, score) pairs of a rejected tile, for diagnostics.


# Thin-outline icons (Boon totem rings, Wake Up!) lose overlap on the small Tab tile: true
# matches scored 0.80-0.85 against the shared 0.86. They are accepted only with a runner-up
# far behind. With the true perk removed, 101 real tiles left a best gap of 0.102, so an
# icon missing from the catalog still stays "?". The Bloodweb matcher is not affected.
THIN_ICON_SCORE=.78
THIN_ICON_MARGIN=.12
THIN_ICON_CONTOUR=.8


class PerkMatcher:
    def __init__(self,root,role='survivor'):
        self.library=PerkLibrary(root,role)
        if not self.library.entries:raise ValueError('perk_catalog_missing')
        self.matcher=IconMatcher(self.library,role,character=None)

    def identify(self,tile):
        """tile: square BGR crop of one perk diamond, at any scale."""
        resized,feature=tile_feature(tile)
        if feature is None:return SlotResult(None,None,(),0.,0.,'no_glyph')
        match=self.matcher.match(resized,64,use_cache=False,prepared_feature=feature,prepared_rarity=('unknown',0.))
        icon_id,reason=match.icon_id,'accepted'
        if (icon_id is None and match.reason=='low_glyph_score' and match.top10 and match.confidence>=THIN_ICON_SCORE
                and match.margin>=THIN_ICON_MARGIN and match.contour_score>=THIN_ICON_CONTOUR):
            icon_id,reason=match.top10[0][0],'accepted_thin_icon'
        if icon_id is None:
            names=tuple((self.library.perks_by_icon[i][0].name,round(float(s),3)) for i,s in match.top10[:3] if i in self.library.perks_by_icon)
            return SlotResult(None,None,(),match.confidence,match.margin,match.reason,names)
        perks=self.library.perks_by_icon[icon_id]
        return SlotResult(icon_id,perks[0].icon,tuple(p.name for p in perks),match.confidence,match.margin,reason)
