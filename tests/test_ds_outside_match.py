"""TASKS 2026-11-08b F: Decisive Strike timers on the user's two 'drawn over a menu' screenshots."""
from pathlib import Path
from types import SimpleNamespace

import cv2
import pytest

from src.core.killer_status import HOOKED, OTHER
from src.gui.ds_overlay import DsTimerController

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / 'debug/match_samples'
needs_samples = pytest.mark.skipif(not SAMPLES.is_dir(), reason='user samples are git-ignored')
HOOK = SAMPLES / 'killer_ds/killer_03_slot1_hooked_slot4_injured.png'


class Backend:
    def __init__(self, path):
        self.image = cv2.imread(str(path))

    def info(self):
        return SimpleNamespace(foreground=True, client=SimpleNamespace(x=0, y=0, width=1920, height=1080))

    def grab(self, rect):
        return self.image[rect.y:rect.y + rect.height, rect.x:rect.x + rect.width], None


class Overlay:
    visible = False

    def hide(self):
        self.visible = False

    def present_timers(self, *args):
        self.visible = True


def controller(path, killer=True):
    return DsTimerController(ROOT, lambda: killer, lambda: False, backend=Backend(path), overlay=Overlay(), clock=lambda: 100.)


def run(c, reads=2):
    for _ in range(reads * 6):  # The first read comes at tick 10, the next at tick 12 once the HUD is seen.
        c.step()


@needs_samples
@pytest.mark.parametrize('name', ['ds_over_endgame_scoreboard', 'ds_over_killer_menu'])
def test_nothing_is_drawn_over_menus_or_the_end_scoreboard(name):
    c = controller(SAMPLES / f'{name}.png')
    c.tracker.started[0] = 90.  # A timer was running when the match ended.
    run(c)
    assert not c.overlay.visible and not c.hud_shown


@needs_samples
def test_a_running_timer_is_drawn_in_a_killer_match():
    c = controller(HOOK)
    c.tracker.started[0] = 90.
    run(c)
    assert c.match_role == 'killer' and c.hud_shown and c.overlay.visible


@needs_samples
def test_killer_perks_win_over_the_role_selected_in_the_app():
    c = controller(HOOK, killer=False)
    run(c)
    assert c.match_role == 'killer' and c.tracker.stable[0] in (HOOKED, OTHER, None)


@pytest.fixture(autouse=True)
def application():
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])
