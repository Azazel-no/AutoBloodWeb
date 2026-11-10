"""Terrifier chapter (PTB data): library, catalogs and recognition from the wiki icons."""
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.core.perk_catalog import load_catalog
from src.core.perk_matcher import PerkMatcher

ROOT=Path(__file__).resolve().parents[1]
PERKS=dict(killer=('Funny Business','Kill Spree','Laughingstock'),survivor=("Valkyrie's Rage",'Shared Trauma','Best Gift Ever'))


def on_black(path):
    image=cv2.imdecode(np.fromfile(path,np.uint8),cv2.IMREAD_UNCHANGED)
    if image.shape[2]==4:
        alpha=image[:,:,3:]/255;image=(image[:,:,:3]*alpha).astype(np.uint8)
    return image


@pytest.mark.parametrize('role',['killer','survivor'])
def test_new_perks_are_recognised_from_their_wiki_icons(role):
    matcher=PerkMatcher(ROOT,role);chapter=[p for p in load_catalog(ROOT,role) if p.name in PERKS[role]]
    assert sorted(p.name for p in chapter)==sorted(PERKS[role])
    for perk in chapter:
        # The flat wiki art is harder than a real game tile: some catalog perks stay below the
        # acceptance score here, so the check is that the perk is the best candidate.
        result=matcher.identify(on_black(ROOT/perk.icon))
        assert perk.name in (result.names or result.candidates[0][:1])


def test_library_has_all_twenty_add_ons_with_descriptions(library):
    from src.core.item_info import load_item_info
    info=load_item_info(ROOT);entries=[e for e in library.filter('killer','addons','terrifier') if e.character=='terrifier']
    assert len(entries)==20 and all(e.id in info and info[e.id].description for e in entries)
    assert library.portraits.get('terrifier') and (ROOT/library.portraits['terrifier']).is_file()


def test_art_gets_perks_only_until_he_is_on_the_tier_list(monkeypatch):
    monkeypatch.undo()  # conftest empties the recommendations for other tests.
    from src.core import tierlist
    assert tierlist.recommended(ROOT,['terrifier','nurse'])['terrifier']==['any_perk']
    assert len(tierlist.recommended(ROOT,['nurse'])['nurse'])>1
