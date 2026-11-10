"""Randomizer rules. Every roll uses the real catalogs."""
import random
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from src.core.randomizer import ADDON_SLOTS,PERK_SLOTS,Options,Pool,item_type
from src.gui.randomizer_page import MAX_MS,card_rects,reel_rects,stop_times

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def pool(library):
    return Pool(ROOT,library)


def test_killer_add_ons_belong_to_the_rolled_killer(pool):
    rng=random.Random(1)
    for _ in range(300):
        r=pool.roll(Options(),rng);own=pool.addons_by_killer[r['killer'].id]
        assert r['addon1'] in own and r['addon2'] in own and r['addon1']!=r['addon2']


def test_survivor_add_ons_match_the_item_type(pool):
    rng=random.Random(2)
    for _ in range(300):
        r=pool.roll(Options(role='survivor'),rng);own=pool.addons_by_type[item_type(r['item'].name)]
        assert r['addon1'] in own and r['addon2'] in own and r['addon1']!=r['addon2'] and len({r[s] for s in PERK_SLOTS})==4


def test_every_killer_and_item_type_can_roll_two(pool):
    assert all(len(v)>=2 for v in pool.addons_by_killer.values())
    types={item_type(i.name) for i in pool.items if i.id not in pool.event_ids}
    assert types>={'flashlight','medkit','toolbox','map','key'}
    assert all(len(pool.allowed(pool.addons_by_type[t],Options()))>=2 for t in types)


def test_exclusions_ptb_and_events_are_respected(pool):
    rng=random.Random(3);banned=pool.killers[0].id
    options=Options(excluded=frozenset([banned]),perks=True)
    for _ in range(200):
        r=pool.roll(options,rng);assert r['killer'].id!=banned and r['killer'].id!='terrifier'
    assert any(pool.roll(Options(ptb=True),rng)['killer'].id=='terrifier' for _ in range(600))
    items=pool.item_candidates(Options());assert items and not {i.id for i in items}&pool.event_ids


def test_reroll_keeps_the_rules_and_the_other_slots(pool):
    rng=random.Random(4);options=Options(role='survivor')
    for _ in range(100):
        r=pool.roll(options,rng);n=pool.reroll(r,'addon1',options,rng)
        assert n['addon2']==r['addon2'] and n['addon1']!=n['addon2'] and n['item']==r['item']
        n=pool.reroll(r,'item',options,rng);own=pool.addons_by_type[item_type(n['item'].name)]
        assert n['addon1'] in own and n['addon2'] in own


def test_timing_and_layout():
    app=QApplication.instance() or QApplication([])
    assert max(stop_times(10))<=MAX_MS and stop_times(5)==sorted(stop_times(5))
    slots=['survivor','item','addon1','addon2','perk1','perk2','perk3','perk4','offering']
    rects=card_rects(slots,540,620)
    assert all(r.left()>=0 and r.right()<540 and r.bottom()<620 for r in rects.values()) and rects['item'].width()>rects['addon1'].width()
    assert len(reel_rects(slots,540))==9


def test_page_rolls_skips_and_copies(library,pool):
    from src.gui.randomizer_page import RandomizerPage
    app=QApplication.instance() or QApplication([]);saved=[]
    page=RandomizerPage(ROOT,pool,{},lambda:saved.append(1),lambda k,**v:k,random.Random(5));page.resize(540,700);page.show();app.processEvents()
    page.roll();assert page.running and page.reels
    page.skip();assert not page.running and set(page.cards)==set(page.result)
    page.copy();assert page.result['killer'].name in QApplication.clipboard().text()
    page.reroll_slot('addon1');assert page.cards['addon1'].choice==page.result['addon1'];page.hide()
