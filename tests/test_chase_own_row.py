"""B (TASKS 2026-11-25): the chase meme reacts only to the player's own row."""
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.core import chase_meme as cm
from src.core.match_reader import portrait_feature
from src.core.own_row import OwnRowLock

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / 'debug/chase_events/2026-11-18'
needs_events = pytest.mark.skipif(not EVENTS.is_dir(), reason='local saved chase events')
PAIR = cm.HornSample(60, 60, True, True, .3, .05, .6, .6, (0., 0., 0., 0.), True, True, .3)


def run(d, rows, own=0, seconds=3., step=.1, start=0.):
    """Reads every `step` s: my horns animate (PAIR) and every row has the given scores."""
    events = []; t = start
    while t < start + seconds:
        d.rows(rows(t) if callable(rows) else rows, own, t); e = d.horns(PAIR, t)
        if e: events.append((round(t, 2), *e))
        t += step
    return events


def detector():
    d = cm.ChaseDetector(); d.require_rows = True; return d


def test_own_row_rule_needs_presence_and_a_clear_lead():
    assert cm.own_row_wins([1.5, .2, 0, 0])[0]
    assert cm.own_row_wins([.8, 0, 0, 0]) == (False, 'my row 1 below threshold (0.80)')
    assert cm.own_row_wins([1.4, 1.6, 0, 0])[1].startswith('row 2 at least as strong')
    assert cm.own_row_wins([1.4, 1.3, 0, 0])[1].startswith('margin')
    assert cm.own_row_wins([1.5, 0, 0, 0], own=None) == (False, 'own row not established')
    assert cm.own_row_wins([0, 1.5, 0, 0], own=1)[0]          # my row is wherever the identity says, not the top one


def test_a_teammates_stronger_row_blocks_the_start_and_says_why():
    events = run(detector(), [1.5, 1.9, 0, 0])
    assert events and all(e[1] == 'rejected' for e in events)
    assert 'row 2 at least as strong' in events[0][2]


def test_my_row_must_lead_for_the_hold_time_before_a_start():
    d = detector(); events = run(d, [1.5, .2, 0, 0])
    starts = [e for e in events if e[1] == 'start']
    assert len(starts) == 1 and starts[0][0] >= cm.START_HOLD - .05
    assert d.last_evidence['winner'] == 1 and d.last_evidence['margin'] == 1.3 and d.last_evidence['held'] >= cm.START_HOLD - .05


def test_a_short_lead_interrupted_by_a_teammate_never_starts():
    rows = lambda t: [1.5, .2, 0, 0] if int(t * 10) % 10 < 6 else [1.5, 1.8, 0, 0]
    assert not [e for e in run(detector(), rows) if e[1] == 'start']


def test_without_my_row_the_meme_stays_idle():
    events = run(detector(), [1.5, 0, 0, 0], own=None)
    assert events and all(e[1] == 'rejected' and 'own row not established' in e[2] for e in events)


def test_no_new_start_for_the_rearm_time_after_a_chase():
    d = detector(); assert [e for e in run(d, [1.5, 0, 0, 0]) if e[1] == 'start']
    d.stop(5., 'test'); assert d.ended_at == 5.
    early = run(d, [1.5, 0, 0, 0], start=5.1, seconds=3.)
    assert not [e for e in early if e[1] == 'start'] and any('re-arm' in e[2] for e in early)
    later = run(d, [1.5, 0, 0, 0], start=5. + cm.REARM_SECONDS, seconds=2.)
    assert [e for e in later if e[1] == 'start']


def test_timing_constants_are_in_the_asked_ranges():
    assert 1. <= cm.START_HOLD <= 1.5 and 5. <= cm.REARM_SECONDS <= 10.


# --- own row identity -----------------------------------------------------------------------------
def face(seed):
    rng = np.random.default_rng(seed); image = np.zeros((120, 120, 3), np.uint8)
    for _ in range(12):
        cv2.circle(image, tuple(int(v) for v in rng.integers(20, 100, 2)), int(rng.integers(6, 20)), tuple(int(v) for v in rng.integers(0, 255, 3)), -1)
    return portrait_feature(image, (60, 60), 36)


def test_own_row_from_the_gold_stripe_portrait_and_tracking_by_identity():
    lock = OwnRowLock(); faces = [face(i) for i in range(4)]
    lock.observe_tab(faces[2], [[f] for f in faces])
    assert lock.current()[:2] == (2, 'gold_stripe')
    # The rows change order (a teammate left): my portrait is now in row 1; the lock follows it.
    assert lock.track([faces[2], faces[0], faces[1], faces[3]]) == 0
    lock.reset('lobby'); assert lock.current()[0] is None


def test_own_row_from_the_nickname_and_a_contradiction_establishes_nothing():
    lock = OwnRowLock(nickname_source=lambda: 'Geralt'); faces = [face(i) for i in range(4)]
    lock.observe_names({0: ['Geralt'], 1: ['Nobody'], 2: ['shisui1x'], 3: ['Semphirr']})
    assert lock.current()[:2] == (0, 'nickname')
    lock.observe_tab(faces[3], [[f] for f in faces])
    row, method, why = lock.current()
    assert row is None and 'contradictory' in why


def test_no_nickname_and_no_tab_means_no_row():
    lock = OwnRowLock(nickname_source=lambda: '')
    lock.observe_names({0: ['Geralt']}); lock.observe_tab(None, [])
    assert lock.current()[0] is None and 'press Tab' in lock.current()[2]


# --- saved frames (local) ---------------------------------------------------------------------------
@needs_events
@pytest.mark.parametrize('name,allowed', [('224341_417_start', False), ('225326_083_start', False), ('221705_495_start', False),
                                          ('225855_135_start', True), ('222801_099_start', True)])
def test_saved_starts_teammate_chases_are_blocked_and_my_chases_pass(name, allowed):
    """11-18: the first three started on a teammate's brackets (my row shows no horns), the last two are my chases."""
    import json
    image = cv2.imdecode(np.fromfile(str(EVENTS / f'{name}.jpg'), np.uint8), cv2.IMREAD_COLOR)
    layout = json.loads((ROOT / 'assets/ui/match_layout.json').read_text(encoding='utf-8'))
    assert cm.own_row_wins(cm.row_scores(image, layout))[0] is allowed
