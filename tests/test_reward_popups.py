"""TASKS 2026-11-08 D: a notice with OK is pressed after two agreeing captures, never paused on."""
import json
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from src.core.reward_popups import MAX_DISMISSALS, dismiss_if_present, find_ok_button
from src.utils.images import read_image

ROOT = Path(__file__).resolve().parents[1]
RECORD = json.loads((ROOT / 'assets/ui/video_v54/manifest.json').read_text(encoding='utf-8'))['reward']


def reward_frame():
    return read_image(ROOT / RECORD['source'])


def titleless():
    """A notice whose title has no reviewed template: only the OK word remains."""
    image = reward_frame().copy()
    x, y, w, h = RECORD['regions']['title']['box']
    image[max(0, y - 8):y + h + 8, max(0, x - 8):x + w + 8] = 0
    return image


def test_ok_word_is_found_without_a_reviewed_title():
    box = find_ok_button(titleless(), ROOT)
    bx, by, bw, bh = RECORD['regions']['button']['box']
    assert box is not None and abs(box[0] - bx) <= 2 and abs(box[1] - by) <= 2


def test_a_screen_without_the_word_has_no_button():
    assert find_ok_button(np.zeros((1080, 1920, 3), np.uint8), ROOT) is None


class Mouse:
    target_region = None

    def __init__(self):
        self.pressed = []

    def release(self):
        pass

    def purchase(self, point, hold, stop, check):
        self.pressed.append(point)


def engine_with(box):
    screen = SimpleNamespace(popup_scan=None)
    traces = []
    engine = SimpleNamespace(
        settings=SimpleNamespace(dry_run=False), approved=True, burst_inputs_active=False, screen=screen, mouse=Mouse(),
        attempts=0, stop=None, trace=lambda name, **k: traces.append(name), emit=lambda *a: None,
        check_input_budget=lambda: None, check=lambda frame, x, y: (round(x), round(y)), check_held=lambda *a: None,
        performance=SimpleNamespace(levels=SimpleNamespace(input=lambda: None)), popup_samples={'*'}, traces=traces)
    engine.popup_samples = set(f'{i}' for i in range(40))  # Sample store full: nothing is written from tests.
    engine.box = box
    return engine


def observe(engine, box=...):
    image = np.zeros((1080, 1920, 3), np.uint8)
    frame = SimpleNamespace(image=image, captured_at=time.monotonic())
    engine.screen.popup_scan = (frame, engine.box if box is ... else box)
    return dismiss_if_present(engine)


def test_one_capture_is_not_enough_two_press_once():
    engine = engine_with((900, 800, 40, 20))
    assert not observe(engine) and engine.mouse.pressed == []
    time.sleep(.01)
    assert observe(engine) and engine.mouse.pressed == [(920, 810)]


def test_a_moving_box_is_not_pressed():
    engine = engine_with((900, 800, 40, 20))
    observe(engine)
    time.sleep(.01)
    assert not observe(engine, (1200, 800, 40, 20)) and engine.mouse.pressed == []


def test_presses_are_bounded():
    engine = engine_with((900, 800, 40, 20))
    for _ in range(MAX_DISMISSALS * 3):
        observe(engine)
        time.sleep(.002)
    assert len(engine.mouse.pressed) == MAX_DISMISSALS and 'popup_dismiss_limit' in engine.traces


def test_never_during_a_burst_or_dry_run():
    engine = engine_with((900, 800, 40, 20))
    engine.burst_inputs_active = True
    observe(engine); time.sleep(.01); observe(engine)
    engine.burst_inputs_active = False; engine.settings.dry_run = True
    observe(engine); time.sleep(.01); observe(engine)
    assert engine.mouse.pressed == []
