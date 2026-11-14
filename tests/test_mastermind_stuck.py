"""Regressions from the live Mastermind (Wesker) run of 2026-11-13: three pauses and refused Starts.

1. Level 49: a click on a locked outer node never resolved (locked nodes read 0.78, the bar was 0.8);
   the run paused and the next Start refused with previous_start_unresolved.
2. Level 22 and level 50: a centre press bought nothing while every read took 2.7-3.1 s, so every
   frame was 'stale'; the press stayed pending and later Starts refused (window_changed) until the
   user bought six levels by hand.
3. The prestige emblem with a two-digit or three-digit number was not recognised when not hovered.
"""
import copy
import time
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.core import bloodweb_detector as bd
from src.core.automation import UNRESOLVED_RETIRE_SECONDS
from src.core.bloodweb_detector import find_center
from src.core.early_unlock import purchase_level
from src.core.models import Node, NodeState, NodeType
from src.core.prestige import ready_evidence
from src.core.purchase_outcome import untaken
from src.core.scene_memory import chain_state_signature, record_scene
from src.core.screen_manager import ScreenError
from tests.test_automation import Device, engine, web
from tests.test_center_and_levels import CentreScreen, make
from tests.test_zero_pauses import wide_web

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'references/bloodweb/bloodweb_level43_start.png'


# --- 1. locked nodes ------------------------------------------------------------------------------
def node(state, confidence, geometry=.9):
    return Node(5, 100, 100, 20, state, NodeType.ADDON, None, state_confidence=confidence, geometry_confidence=geometry)


def test_a_locked_node_at_its_real_confidence_counts_as_not_taken():
    assert untaken(node(NodeState.LOCKED, .78)) and untaken(node(NodeState.AVAILABLE, .9))
    assert not untaken(node(NodeState.AVAILABLE, .78)) and not untaken(node(NodeState.PURCHASED, .93)) and not untaken(None)


def test_a_missed_batch_click_on_a_locked_node_resolves():
    d = Device([web()]); f = d.capture(); result = web()
    locked = Node(2, 300, 200, 20, NodeState.LOCKED, NodeType.ADDON, None, state_confidence=.78, geometry_confidence=.8)
    result.nodes.append(locked)
    point = f.to_screen(locked.x, locked.y)
    memory = record_scene(f, result, [point], dict(batch={'physical_points': [point], 'deferred_verification': True,
                                                         'inputs_completed_at': f.captured_at - 2}))
    assert chain_state_signature(memory, f, result) == ('locked',)


# --- 2. unprovable clicks no longer stop the run ---------------------------------------------------
def pending_scene(level=49):
    """A saved single click whose node can no longer be found at its point (the detection moved 33 px)."""
    d = Device([web()]); f = d.capture(); old = web(); old.level = level; old.level_confidence = .98
    old.nodes.append(Node(2, 300, 200, 20, NodeState.AVAILABLE, NodeType.ADDON, 'target', state_confidence=.9, geometry_confidence=.9))
    point = f.to_screen(300, 200)
    memory = record_scene(f, old, [point], dict(point=list(point), radius=20, icon='target', operation_id='op1'))
    now = web(); now.level = level; now.level_confidence = .98   # The clicked node is not found any more.
    r = engine(d, dry_run=False); r.signature = f.window.signature
    r.persisted_scene = copy.deepcopy(memory); r.scene_record = copy.deepcopy(memory); r.attempted = [point]
    return d, f, now, r


def test_an_unprovable_click_is_retired_after_a_bounded_read_only_look_without_credit():
    d, f, now, r = pending_scene()
    seen = []
    for step in range(int(UNRESOLVED_RETIRE_SECONDS / .5) + 3):
        frame = replace(f, captured_at=f.captured_at + step * .5)
        seen.append(r.recheck_saved_scene(frame, now))          # Never raises: no pause, no refused Start.
        if seen[-1]: break
    assert seen[-1] is True and not any(seen[:-1])
    assert r.persisted_scene is None and not r.attempted and not r.pending_action
    assert r.actions == 0 and not d.presses                       # Nothing credited, nothing clicked again.
    retired = [e for e in r.report.entries if e.get('stage') == 'unresolved_click_retired']
    assert retired[0]['purchase_credit'] == 0 and retired[0]['observed_seconds'] >= UNRESOLVED_RETIRE_SECONDS


def test_an_unreadable_screen_still_refuses_instead_of_retiring():
    d, f, now, r = pending_scene(); now.level_confidence = .5      # A header that cannot be read proves nothing.
    with pytest.raises(ScreenError):
        for step in range(20): r.recheck_saved_scene(replace(f, captured_at=f.captured_at + step * .5), now)
    assert r.persisted_scene and r.persisted_scene['pending']


def test_a_centre_press_that_buys_nothing_survives_slow_reads(monkeypatch):
    """Live 13.11 19:28: every read after the centre press took ~3 s and came back 'stale'."""
    clock = [100.]
    monkeypatch.setattr(time, 'monotonic', lambda: clock[0])
    level = wide_web(level=20); device = CentreScreen([level], clock)
    runner = make(device, priorities=['nothing_here'])
    runner.wait = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    original = runner.recover_read; calls = []

    def slow(*args, **kwargs):
        calls.append(1)
        if len(calls) in (1, 2, 4):
            clock[0] += 3.; raise ScreenError('stale_frame')
        return original(*args, **kwargs)
    runner.recover_read = slow
    fresh, after, plan = purchase_level(runner, device.capture(), level)
    assert len(device.presses) == 1 and runner.center_auto_unavailable == 20 and not runner.pending_action
    assert [e for e in runner.report.entries if e['stage'] == 'automatic_unlock_stale_read']
    assert [e for e in runner.report.entries if e['stage'] == 'center_auto_unavailable']


# --- 2b. the centre search is fast enough for the 2 s freshness rule --------------------------------
@pytest.fixture
def match_calls(monkeypatch):
    calls = []
    original = cv2.matchTemplate
    monkeypatch.setattr(bd.cv2, 'matchTemplate', lambda *a, **k: (calls.append(a[0].shape), original(*a, **k))[1])
    return calls


def test_a_dim_centre_is_found_with_a_focused_search(match_calls):
    image = cv2.imread(str(WEB)); x, y, r = find_center(image)
    dim = image.copy(); patch = dim[y - 30:y + 31, x - 30:x + 31]
    dim[y - 30:y + 31, x - 30:x + 31] = cv2.GaussianBlur(patch, (0, 0), 1.0)    # Scores 0.895: below the 0.94 early exit, above 0.86.
    match_calls.clear()
    found = find_center(dim)
    assert found is not None and abs(found[0] - x) <= 2 and abs(found[1] - y) <= 2
    full = [s for s in match_calls if s[0] > 400 and s[1] > 400]
    assert len(full) <= 2 * len(bd._center_templates(str(ROOT)))   # Native scales only; the old sweep matched ~100 pairs over the web.


def test_without_a_confident_native_match_the_full_search_still_runs(match_calls):
    """No shortcut when the native scales found nothing acceptable: an odd client size still gets every scale."""
    image = cv2.imread(str(WEB)); smaller = cv2.resize(image, None, fx=.8, fy=.8, interpolation=cv2.INTER_AREA)
    match_calls.clear()
    assert find_center(smaller) is not None and len(match_calls) > 2 * len(bd._center_templates(str(ROOT)))


# --- 3. prestige numbers ----------------------------------------------------------------------------
EMBLEMS = (('references/bloodweb/v56_dark_lord_prestige/native_ready.png', (684, 563), 19, 27),   # hovered
           ('references/bloodweb/v54_onryo/prestige_ready_1305.png', (683, 559), 12, 15),        # not hovered
           ('references/bloodweb/v54_onryo/prestige_clean_1308.png', (683, 559), 12, 15))


def with_number(path, centre, clear, height, label):
    """The real emblem with its '1' replaced by another number, sized to fit the inner disc like the game's."""
    image = cv2.imread(str(ROOT / path)); cx, cy = centre
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV); yy, xx = np.mgrid[:image.shape[0], :image.shape[1]]
    inner = (xx - cx) ** 2 + (yy - cy) ** 2 <= clear ** 2; white = inner & (hsv[..., 1] < 100) & (hsv[..., 2] > 130)
    image[white] = np.median(image[inner & ~white], axis=0)
    scale = cv2.getFontScaleFromHeight(cv2.FONT_HERSHEY_SIMPLEX, height, 2)
    width = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)[0][0]
    if width > 2 * (clear + 1): scale *= 2 * (clear + 1) / width
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
    cv2.putText(image, label, (cx - tw // 2, cy + th // 2), cv2.FONT_HERSHEY_SIMPLEX, scale, (235, 235, 235), 2, cv2.LINE_AA)
    return image


@pytest.mark.parametrize('label', ['1', '2', '3', '4', '9', '10', '44', '99', '100'])
@pytest.mark.parametrize('emblem', EMBLEMS, ids=['hovered', 'not_hovered', 'not_hovered_clean'])
def test_the_prestige_emblem_is_recognised_for_every_prestige_number(emblem, label):
    path, centre, clear, height = emblem
    image = with_number(path, centre, clear, height, label)
    found = find_center(image)
    assert found and abs(found[0] - centre[0]) <= 4 and abs(found[1] - centre[1]) <= 4
    assert ready_evidence(image, ROOT, found)


def test_an_ordinary_level_50_web_is_not_taken_for_a_prestige_emblem():
    image = cv2.imread(str(ROOT / 'references/bloodweb/v54_onryo/prestige_animation_negative_1410.png'))
    found = find_center(image)
    assert found is None or ready_evidence(image, ROOT, found) is None


def test_next_web_with_an_unread_header_is_not_a_contradiction(monkeypatch):
    """Live 14.11 12:19 (level 35): the centre bought the level, the next web appeared before its header
    could be read, and its buyable first ring was taken for bought nodes buyable again (pause)."""
    clock = [100.]
    monkeypatch.setattr(time, 'monotonic', lambda: clock[0])
    level = wide_web(level=20); level.nodes[1].state = NodeState.PURCHASED
    following = wide_web(); following.level = None; following.level_confidence = .2   # all buyable, header unreadable
    device = CentreScreen([level, following, following, following, following], clock)
    runner = make(device, priorities=['nothing_here'])
    runner.wait = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    try: purchase_level(runner, device.capture(), level)
    except ScreenError as exc: assert exc.code != 'automatic_unlock_state_contradiction', exc.code
    assert [e for e in runner.report.entries if e['stage'] == 'automatic_unlock_level_unread']
    same = wide_web(level=20); same.nodes[1].state = NodeState.AVAILABLE           # the SAME level read clearly: still a contradiction
    device = CentreScreen([level, same, same], clock); runner = make(device, priorities=['nothing_here'])
    runner.wait = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    with pytest.raises(ScreenError, match='automatic_unlock_state_contradiction'): purchase_level(runner, device.capture(), level)


def test_native_only_search_answers_a_glyphless_frame_fast_and_keeps_full_results(match_calls):
    image = cv2.imread(str(WEB)); found = find_center(image)
    assert bd.last_center_native                                     # found at the client's own scale
    empty = image.copy(); x, y, r = found; empty[y - 60:y + 61, x - 60:x + 61] = 40
    match_calls.clear(); assert find_center(empty, native_only=True) is None
    assert len(match_calls) <= 2 * len(bd._center_templates(str(ROOT)))   # no sweep of every scale
    assert find_center(image, native_only=True) == found              # with the glyph: the same answer


def test_screen_manager_runs_a_full_search_every_few_native_misses():
    from src.core.screen_manager import NATIVE_MISSES
    assert 2 <= NATIVE_MISSES <= 6
