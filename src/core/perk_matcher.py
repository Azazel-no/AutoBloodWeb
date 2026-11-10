"""Identify a perk slot with the existing IconMatcher and its unchanged thresholds.

Matching is by icon image. A few perks share one icon, and the overlay only has
to draw that icon, so sharing is not treated as ambiguity. Anything not accepted
stays None and is drawn as "?", never guessed.
"""
from dataclasses import dataclass
import cv2
import numpy as np
from src.core.models import IconEntry
from src.core.node_classifier import IconMatcher,glyph
from src.core.perk_catalog import load_catalog


class PerkLibrary:
    """The small IconLibrary surface IconMatcher reads, for perks only."""
    def __init__(self,root,role):
        self.root=root;self.perks_by_icon={}
        for perk in load_catalog(root,role):self.perks_by_icon.setdefault('perk_icon_'+perk.sha256[:16],[]).append(perk)
        self.entries=[IconEntry(ident,role,'perks',None,perks[0].icon,perks[0].original,[],'unknown',perks[0].sha256)
                      for ident,perks in sorted(self.perks_by_icon.items())]

    def filter(self,role,character=None):
        return [e for e in self.entries if e.role==role]


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
        size=max(tile.shape[:2])
        resized=cv2.resize(tile[:,:,:3],(128,128),interpolation=cv2.INTER_AREA if size>128 else cv2.INTER_CUBIC)
        feature=glyph(resized,diamond_mask(128))
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
