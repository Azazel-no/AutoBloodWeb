"""Section 8: one short Tab press assigns teammates (HUD cache, follow-up frames, elimination)."""
import json

import cv2
import numpy as np
import pytest

from src.core.match_reader import assign_scores, portrait_feature
from src.core.match_state import HudCache
from tests.test_overlay_controller import SAMPLES, app, controller, layout, needs_samples  # noqa: F401


def table(text):
    """Scores as logged live: 'rowR-slotS' with Tab row R (1-4) and HUD slot S (2-4)."""
    return {(int(k[3]) - 1, int(k[-1]) - 1): v for k, v in json.loads(text).items()}


# debug/overlay/20261107.jsonl, the user's first live match.
LIVE_14_22_50 = '{"row2-slot2": 0.77, "row2-slot3": 0.231, "row2-slot4": 0.491, "row3-slot2": 0.651, "row3-slot3": 0.47, "row3-slot4": 0.405, "row4-slot2": 0.482, "row4-slot3": 0.217, "row4-slot4": 0.677}'
LIVE_14_34_52 = '{"row2-slot2": 0.546, "row2-slot3": 0.381, "row2-slot4": 0.378, "row3-slot2": 0.332, "row3-slot3": 0.257, "row3-slot4": 0.706, "row4-slot2": 0.784, "row4-slot3": 0.435, "row4-slot4": 0.246}'
LIVE_14_43_04 = '{"row2-slot2": 0.512, "row2-slot3": 0.252, "row2-slot4": 0.353, "row3-slot2": 0.335, "row3-slot3": 0.206, "row3-slot4": 0.688, "row4-slot2": 0.772, "row4-slot3": 0.366, "row4-slot4": 0.227}'


def test_the_live_frame_that_was_rejected_now_assigns_all_three():
    """14:22:50: rows 2 and 4 were confident; the injured teammate (row 3) is the only one left."""
    pairs, methods = assign_scores(table(LIVE_14_22_50), [1, 2, 3])
    assert pairs == {1: 1, 3: 3, 2: 2}
    assert methods == {1: 'portrait', 3: 'portrait', 2: 'by_elimination'}


def test_a_pair_freed_by_an_earlier_pair_is_accepted_directly():
    pairs, methods = assign_scores(table(LIVE_14_34_52), [1, 2, 3])
    assert pairs == {3: 2, 1: 3, 2: 1} and methods[2] == 'by_elimination'


def test_elimination_below_the_floor_waits_for_another_frame():
    pairs, methods = assign_scores(table(LIVE_14_43_04), [1, 2, 3])
    assert pairs == {1: 3, 3: 2} and 2 not in methods


def test_nothing_is_assigned_by_elimination_alone():
    weak = {(r, s): .5 if r == s else .1 for r in (1, 2, 3) for s in (1, 2, 3)}
    assert assign_scores(weak, [1, 2, 3]) == ({}, {})


def face(seed, gain=1.):
    rng = np.random.default_rng(seed)
    image = cv2.GaussianBlur(rng.integers(0, 255, (90, 90, 3), dtype=np.uint8), (9, 9), 3)
    return portrait_feature(np.clip(image * gain, 0, 255).astype(np.uint8), (45, 45), 36)


def test_cache_keeps_only_still_frames_and_expires_them():
    now = [0.]
    cache = HudCache(clock=lambda: now[0])
    fading = [face(1, .3), face(1, .6), face(1, .9)]
    for f in fading:
        cache.add([None, f, None, None]); now[0] += .5
    assert cache.candidates()[1] == []  # Every frame differs from the one before: the HUD is fading in.
    cache.add([None, face(1), None, None]); now[0] += .5
    cache.add([None, face(1), None, None]); now[0] += .5
    cache.add([None, face(1), None, None])
    assert len(cache.candidates()[1]) == 1  # A still portrait is one refreshed entry.
    now[0] += HudCache.keep + .1
    assert cache.candidates()[1] == []


def frames(name):
    hud = cv2.imread(str(SAMPLES / f'{name}_hud_1920x1080.png'))
    tab = cv2.imread(str(SAMPLES / f'{name}_tab_1920x1080.png'))
    # The HUD still fading in after a previous Tab: mostly the scene behind it (live scores were ~0.1-0.2).
    faded = cv2.addWeighted(hud, .2, cv2.flip(hud, -1), .8, 0)
    return hud, tab, faded


def logged(x, event):
    lines = [json.loads(line) for f in x.log.glob('*.jsonl') for line in f.read_text(encoding='utf-8').splitlines()]
    return [line for line in lines if line['event'] == event]


@needs_samples
def test_one_short_press_assigns_everyone_from_the_hud_cache(controller):
    x = controller
    hud, tab, faded = frames('pair02')
    x.s.frames = [hud, hud]
    x.c.poll(); x.c.poll()  # Two still HUD frames while Tab is closed.
    x.s.frames = [faded, tab]
    x.c.handle_action('key_down')
    x.c.look_for_scoreboard(); x.c.look_for_scoreboard()  # Two agreeing frames: fully shown.
    assert sorted(x.o.slots) == [1, 2, 3]
    assert {s + 1: v.row + 1 for s, v in x.c.state.slots.items()} == {2: 4, 3: 2, 4: 3}
    done = logged(x, 'tab_done')
    assert done and done[-1]['assigned'] == {'2': {'row': 4, 'method': 'portrait'}, '3': {'row': 2, 'method': 'portrait'},
                                             '4': {'row': 3, 'method': 'portrait'}}


@needs_samples
def test_fresh_frames_after_tab_closes_fill_the_slots(controller):
    x = controller
    hud, tab, faded = frames('pair02')
    x.s.frames = [faded, tab]
    x.c.handle_action('key_down')
    x.c.look_for_scoreboard(); x.c.look_for_scoreboard()  # Two agreeing frames: fully shown.
    assert not x.c.state.slots and x.c.followup.isActive()  # Only a fading frame so far.
    x.s.frames = [tab, faded, hud]
    for _ in range(3):
        x.c.followup_tick()
    assert sorted(x.o.slots) == [1, 2, 3] and not x.c.followup.isActive()
    assert logged(x, 'tab_done')[-1]['followup_frames'] == 2


@needs_samples
def test_follow_up_gives_up_after_its_window(controller):
    x = controller
    hud, tab, faded = frames('pair02')
    x.s.frames = [faded, tab]
    x.c.handle_action('key_down')
    x.c.look_for_scoreboard(); x.c.look_for_scoreboard()  # Two agreeing frames: fully shown.
    x.c.session['deadline'] = 0.
    x.c.followup_tick()
    assert not x.c.followup.isActive() and x.c.session is None and not x.c.state.slots


def test_the_scoreboard_is_polled_fast_enough_for_a_short_press(controller):
    assert controller.c.scoreboard_wait.interval() <= 50 and controller.c.presence.interval() <= 500
