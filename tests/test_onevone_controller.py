"""1v1 controller on the killer-POV HUD samples: arm on match start, start on the role's input, stop on a down."""
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from src.core.hotkey_config import extra_keys_conflict
from src.core.onevone import ARMED, DEFAULT_KEYS, IDLE, RUNNING, clean_settings, default_settings
from src.gui.onevone_overlay import KeysWatcher, OneVOneController, READ_EVERY, bar_rect

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / 'debug/match_samples/hud_states_2026-11-11'
needs = pytest.mark.skipif(not SAMPLES.is_dir(), reason='local samples')
BLACK = np.zeros((1080, 1920, 3), np.uint8)   # Loading screen: no HUD.


def frame(key):
    return cv2.imread(str(next(SAMPLES.glob(key + '_*.png'))))


class Backend:
    def __init__(self): self.image = BLACK

    def info(self):
        return SimpleNamespace(hwnd=1, foreground=True, client=SimpleNamespace(x=0, y=0, width=1920, height=1080))

    def grab(self, rect):
        return self.image[rect.y:rect.y + rect.height, rect.x:rect.x + rect.width], None


class Window:
    visible = False
    def hide(self): self.visible = False
    def present(self, client, view): self.visible = True; self.view = view


class Clock:
    now = 100.
    def __call__(self): return self.now


@pytest.fixture(autouse=True)
def application():
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


def controller(killer=False):
    clock = Clock()
    c = OneVOneController(ROOT, default_settings(), lambda: killer, lambda: False, backend=Backend(), window=Window(), clock=clock)
    return c, clock


def reads(c, count=1):
    for _ in range(count * READ_EVERY): c.step()


@needs
def test_killer_round_arms_starts_on_m1_and_stops_on_dying():
    c, clock = controller()
    reads(c); assert c.model.state == IDLE                 # loading screen: nothing armed
    c.backend.image = frame('02'); reads(c)
    assert c.model.state == ARMED and c.role == 'killer'   # own killer perks beat the app's survivor selector
    c.on_key('ctrl'); assert c.model.state == ARMED        # the killer's clock does not start on Ctrl
    c.on_key('m1'); assert c.model.state == RUNNING
    clock.now = 160.; c.backend.image = frame('03'); reads(c)
    assert c.model.state == RUNNING                        # one read of a down is not enough
    clock.now = 160.3; reads(c)
    assert c.model.state == IDLE and c.model.rounds[0].seconds == 60.   # timed to the first read that saw the down and c.model.survivor == 1


@needs
def test_carried_also_stops_and_a_new_match_arms_again():
    c, clock = controller(killer=True)
    c.backend.image = frame('07'); reads(c)                 # Kate already dying at match start: arm, but nothing runs
    c.on_key('m1'); clock.now = 120.; c.backend.image = frame('04'); reads(c, 2)
    assert c.model.state == IDLE and len(c.model.rounds) == 1
    c.backend.image = frame('04'); reads(c)
    assert c.model.state == IDLE                           # same match: no re-arm
    c.backend.image = BLACK; clock.now = 200.; reads(c)     # the HUD is gone long enough: the match is over
    c.backend.image = frame('01'); clock.now = 230.; reads(c)
    assert c.model.state == ARMED


def test_hotkeys_drive_the_model():
    c, clock = controller()
    c.model.arm(); c.on_key('ctrl'); assert c.model.state == RUNNING
    clock.now = 130.; c.on_key('stop'); assert c.model.rounds[0].seconds == 30. and c.model.stop_reason == 'manual'
    c.on_key('swap'); assert c.model.survivor == 0
    c.on_key('undo'); assert c.model.rounds == []
    c.on_key('reset_round'); assert c.model.state == ARMED
    c.on_key('reset_match'); assert c.model.state == IDLE
    c.on_key('show'); assert not c.shown


def test_scoreboard_shows_only_while_the_game_is_in_front():
    c, _ = controller(); c.client = Backend().info().client
    c.game_foreground = lambda: False; c.paint(); assert not c.window.visible
    c.game_foreground = lambda: True; c.paint(); assert c.window.visible and c.window.view[0] == ['Player 1', 'Player 2']
    c.on_key('show'); assert not c.window.visible


def test_scoreboard_is_top_centre_and_scales_with_the_window():
    for w, h in ((1920, 1080), (2560, 1440), (1280, 720)):
        r = bar_rect(w, h)
        assert abs(r.center().x() - w / 2) < 1 and r.top() < h * .02 and abs(r.width() / h - 620 / 1080) < 1e-6
    assert abs(bar_rect(1920, 1080, 1.5).width() - 930) < 1e-6


def test_key_watcher_reports_each_press_once_and_only_in_game():
    state = {'down': False, 'front': True}; got = []
    w = KeysWatcher({'m1': 1}, lambda: state['front'], got.append, read=lambda vk: state['down'])
    state['down'] = True; w.step(); w.step(); state['down'] = False; w.step(); assert got == ['m1']
    state['front'] = False; state['down'] = True; w.step(); assert got == ['m1']


def test_default_keys_do_not_clash_and_settings_are_cleaned():
    assert not extra_keys_conflict({'toggle': 'F8', 'stop': 'F9'}, 'F10', 'TAB', list(DEFAULT_KEYS.values()))
    assert extra_keys_conflict({'toggle': 'F8', 'stop': 'F9'}, 'F10', 'TAB', ['F8'])
    s = clean_settings({'enabled': 'yes', 'names': ['A', 'B'], 'opacity': 5, 'keys': {'show': ' f12 '}})
    assert s['enabled'] is False and s['names'] == ['A', 'B'] and s['opacity'] == 90 and s['keys']['show'] == 'F12'
    assert clean_settings(None)['enabled'] is False
