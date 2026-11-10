"""OVERLAY_FIXES section 11: perks lock for the match and reset only in the lobby (READY/CANCEL button)."""
import glob
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.core.match_reader import is_lobby
from tests.test_overlay_controller import SAMPLES, FakeMatcher, app, controller, layout, needs_samples  # noqa: F401
from src.core.perk_matcher import SlotResult


@needs_samples
def test_only_the_lobby_button_counts_as_lobby(layout):
    files = sorted(glob.glob(str(SAMPLES / '*.png'))) + sorted(glob.glob('references/bloodweb/**/*.png', recursive=True))
    lobby = [f for f in files if Path(f).name.startswith('lobby_')]
    assert len(lobby) == 4
    for name in files:
        image = cv2.imread(name)
        if image is None or image.shape[0] < 700: continue
        assert is_lobby(image, layout) == (name in lobby), name


def logged(x, event):
    return [json.loads(l) for f in x.log.glob('*.jsonl') for l in f.read_text(encoding='utf-8').splitlines() if f'"{event}"' in l]


def frames(name):
    return cv2.imread(str(SAMPLES / f'{name}_hud_1920x1080.png')), cv2.imread(str(SAMPLES / f'{name}_tab_1920x1080.png'))


def read_once(x, hud, tab):
    x.s.frames = [hud, tab, tab]
    x.c.handle_action('key_down')
    x.c.look_for_scoreboard(); x.c.look_for_scoreboard()


@needs_samples
def test_complete_perks_lock_and_further_tab_presses_capture_nothing(controller):
    x = controller
    hud, tab = frames('pair02')
    x.s.frames = [hud]; x.c.poll()
    assert x.c.phase == 'match' and x.c.summary()[0] == 'reading'
    read_once(x, hud, tab)
    assert sorted(x.o.slots) == [1, 2, 3] and x.c.phase == 'locked' and x.c.summary()[0] == 'locked'
    assert logged(x, 'perks_locked')
    grabs = []
    x.c.backend.grab = lambda client: grabs.append(1) or (tab, 'mss')
    x.c.handle_action('key_down')
    assert grabs == [] and not x.c.scoreboard_wait.isActive()  # A quick Tab tap to check ping does nothing.


@needs_samples
def test_the_lobby_clears_everything_and_the_next_match_reads_again(controller):
    x = controller
    hud, tab = frames('pair02')
    x.s.frames = [hud]; x.c.poll()
    read_once(x, hud, tab)
    assert x.c.phase == 'locked'
    x.s.frames = [cv2.imread(str(SAMPLES / 'lobby_ready_01.png'))]
    x.c.poll()
    assert x.c.phase == 'lobby' and not x.c.state.slots and not x.o.visible and x.c.summary()[0] == 'lobby'
    x.c.handle_action('key_down')
    assert not x.c.scoreboard_wait.isActive()  # Nothing is read in the lobby.
    hud3, tab3 = frames('pair03')
    x.s.frames = [hud3]
    for _ in range(3): x.c.poll()  # The lobby is checked every third tick.
    assert x.c.phase == 'match'
    read_once(x, hud3, tab3)
    assert sorted(x.o.slots) == [1, 2, 3]
    assert [l['event'] for l in [*logged(x, 'lobby_seen'), *logged(x, 'match_detected')]]


@needs_samples
def test_an_unknown_perk_keeps_reading_and_is_filled_without_downgrades(controller):
    x = controller
    hud, tab = frames('pair02')
    state = {'first': True}
    class Flaky(FakeMatcher):
        def identify(self, tile):  # The first read sees '?' everywhere (a scoreboard still fading in).
            return SlotResult(None, None, (), 0, 0, 'low') if state['first'] else super().identify(tile)
    x.c.matcher = Flaky()
    x.s.frames = [hud]; x.c.poll()
    read_once(x, hud, tab)
    assert x.c.phase == 'match' and not x.c.state.all_known()  # A '?' remains: not locked.
    known_before = {s: [r.icon_id for r in v.slots] for s, v in x.c.state.slots.items()}
    state['first'] = False
    read_once(x, hud, tab)
    assert x.c.phase == 'locked' and x.c.state.all_known()
    for slot, ids in known_before.items():
        for old, new in zip(ids, (r.icon_id for r in x.c.state.slots[slot].slots)):
            assert old is None or old == new  # Known perks never changed.


@needs_samples
def test_a_fading_scoreboard_is_not_read_until_two_frames_agree(controller):
    x = controller
    hud, tab = frames('pair02')
    fading = tab.copy()
    fading[230:280, 718:770] = 20  # One tile not drawn yet.
    x.s.frames = [hud]; x.c.poll()
    x.s.frames = [hud, fading, tab, tab]
    x.c.handle_action('key_down')
    x.c.look_for_scoreboard(); x.c.look_for_scoreboard()
    assert not x.c.state.slots  # The two frames disagreed.
    x.c.look_for_scoreboard()
    assert sorted(x.o.slots) == [1, 2, 3]


def test_no_global_overlay_hotkeys_remain(controller):
    assert not hasattr(controller.c, 'hotkeys')


@needs_samples
def test_escaped_and_end_game_screens_show_no_clusters_but_keep_the_perks(controller):
    x = controller
    from src.core.match_reader import hud_visible, is_end_scoreboard, load_layout
    frames = {name: cv2.imread(str(SAMPLES / name)) for name in ('hud_escaped_clusters_1920x1080.png', 'endgame_scoreboard_clusters_1920x1080.png', 'pair02_hud_1920x1080.png')}
    x.c.state.slots = {1: object()}
    x.c.overlay.hide = lambda: x.c.__dict__.__setitem__('hidden', getattr(x.c, 'hidden', 0) + 1)
    assert is_end_scoreboard(frames['endgame_scoreboard_clusters_1920x1080.png'])
    layout_ = load_layout(Path('.').resolve())
    assert not hud_visible(frames['hud_escaped_clusters_1920x1080.png'], layout_)
    assert hud_visible(frames['pair02_hud_1920x1080.png'], layout_)
    x.s.frames = [frames['endgame_scoreboard_clusters_1920x1080.png']]
    x.c.poll()
    assert x.c.hidden == 1 and x.c.state.slots
