"""TASKS 2026-11-08 E: the Decisive Strike trigger, on synthetic sequences until a real unhook sample exists."""
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.core.killer_status import (ACTIVE_SECONDS, DEAD, EXPIRED_SECONDS, HOOKED, OTHER, PATCH_HALF, STABLE_READS, DsTracker,
                                    classify, column_rect, load_templates, read_column)
from src.core.match_reader import load_layout

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / 'debug/match_samples/killer_ds'
needs_samples = pytest.mark.skipif(not SAMPLES.is_dir(), reason='user samples are git-ignored')
ORDER = ('killer_01_all_healthy', 'killer_02_slot4_dying', 'killer_03_slot1_hooked_slot4_injured', 'killer_04_slot1_hooked_entity_phase',
         'killer_05_slot1_dead_slot2_dying', 'killer_06_endgame_all_dead_bot')
EXPECTED = {
    'killer_01_all_healthy': [OTHER, OTHER, OTHER, OTHER],
    'killer_02_slot4_dying': [OTHER, OTHER, OTHER, OTHER],
    'killer_03_slot1_hooked_slot4_injured': [HOOKED, OTHER, OTHER, OTHER],
    'killer_04_slot1_hooked_entity_phase': [HOOKED, OTHER, OTHER, OTHER],
    'killer_05_slot1_dead_slot2_dying': [DEAD, OTHER, OTHER, OTHER],
    # Slot 3 is a bot's skull under a red splash: not recognised, harmless unless it was hooked just before.
    'killer_06_endgame_all_dead_bot': [DEAD, DEAD, OTHER, DEAD],
}


@pytest.fixture(scope='module')
def templates():
    return load_templates(ROOT)


def patch_with(template):
    patch = np.full((PATCH_HALF * 2, PATCH_HALF * 2, 3), 20, np.uint8)
    h, w = template.shape
    top, left = PATCH_HALF - h // 2, PATCH_HALF - w // 2
    patch[top:top + h, left:left + w][template > 0] = 255
    return patch


def test_templates_classify_themselves_and_a_dark_portrait_is_other(templates):
    assert classify(patch_with(templates[HOOKED]), templates) == HOOKED
    assert classify(patch_with(templates[DEAD]), templates) == DEAD
    assert classify(np.full((88, 88, 3), 30, np.uint8), templates) == OTHER


@needs_samples
@pytest.mark.parametrize('name', ORDER)
def test_real_frames_are_read_slot_by_slot(name, templates):
    layout = load_layout(ROOT)
    image = cv2.imread(str(SAMPLES / f'{name}.png'))
    x, y, w, h = column_rect(layout, 1920, 1080)
    statuses, visible = read_column(image[y:y + h, x:x + w], layout, 1920, 1080, templates)
    assert statuses == EXPECTED[name]
    assert visible or name == 'killer_06_endgame_all_dead_bot'  # The dead players' names are dim there.


def feed(tracker, statuses, now, reads=STABLE_READS):
    for i in range(reads):
        tracker.update(statuses, now + i * .3)
    return now + reads * .3


def slot0(status):
    return [status, OTHER, OTHER, OTHER]


def test_unhook_starts_the_timer_and_it_runs_clockwise_to_expired_then_disappears():
    t = DsTracker()
    now = feed(t, slot0(OTHER), 0)
    now = feed(t, slot0(HOOKED), now)
    assert not t.display(now)
    now = feed(t, slot0(OTHER), now)
    started = t.started[0]
    assert started is not None
    assert t.display(started + 15)[0] == ('running', pytest.approx(.25))
    assert t.display(started + 30)[0] == ('running', pytest.approx(.5))
    assert t.display(started + ACTIVE_SECONDS + 1)[0][0] == 'expired'
    assert 0 not in t.display(started + ACTIVE_SECONDS + EXPIRED_SECONDS + 1)


def test_hooked_to_dead_starts_nothing():
    t = DsTracker()
    now = feed(t, slot0(HOOKED), 0)
    now = feed(t, slot0(DEAD), now)
    assert t.started[0] is None and not t.display(now)


def test_hooked_again_cancels_and_the_next_unhook_starts_again():
    t = DsTracker()
    now = feed(t, slot0(HOOKED), 0)
    now = feed(t, slot0(OTHER), now)
    assert t.started[0] is not None
    now = feed(t, slot0(HOOKED), now)
    assert t.started[0] is None
    now = feed(t, slot0(OTHER), now)
    assert t.started[0] is not None


def test_a_flicker_shorter_than_the_stable_reads_changes_nothing():
    t = DsTracker()
    now = feed(t, slot0(HOOKED), 0)
    now = feed(t, slot0(OTHER), now, reads=STABLE_READS - 1)
    now = feed(t, slot0(HOOKED), now)
    assert t.started[0] is None and t.stable[0] == HOOKED


def test_never_hooked_starts_nothing():
    t = DsTracker()
    now = feed(t, slot0(OTHER), 0)
    assert not t.display(now)


def test_expired_duration_is_configurable():
    t = DsTracker(expired_seconds=10)
    now = feed(t, slot0(HOOKED), 0)
    feed(t, slot0(OTHER), now)
    assert 0 not in t.display(t.started[0] + ACTIVE_SECONDS + 10.5)
