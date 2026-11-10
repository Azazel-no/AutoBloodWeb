"""TASKS 2026-11-08b F, G: the player's own perks prove the HUD and give the role."""
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.core.own_perks import crop_own, decide, load_matchers, own_rect, own_tiles, read_own

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / 'debug/match_samples'
needs_samples = pytest.mark.skipif(not SAMPLES.is_dir(), reason='user samples are git-ignored')
KILLER = [f'killer_ds/killer_0{i}_{n}' for i, n in ((1, 'all_healthy'), (2, 'slot4_dying'), (3, 'slot1_hooked_slot4_injured'),
                                                    (4, 'slot1_hooked_entity_phase'), (5, 'slot1_dead_slot2_dying'))]
SURVIVOR = ['hud_survivor_1920x1080_01', 'hud_survivor_1920x1080_02_injured', 'hud_survivor_1920x1080_03_carried',
            'hud_survivor_1920x1080_04', 'hud_survivor_1920x1080_05_busy_bg', 'pair02_hud_1920x1080', 'pair03_hud_1920x1080',
            'pair04_hud_1920x1080', 'pair05_hud_1920x1080', 'pair06_hud_1920x1080']
OUTSIDE = ['ds_over_endgame_scoreboard', 'ds_over_killer_menu', 'endgame_scoreboard_clusters_1920x1080', 'hud_escaped_clusters_1920x1080',
           'lobby_cancel_01', 'lobby_cancel_loadout_01', 'lobby_ready_01', 'lobby_ready_loadout_01', 'tab_scoreboard_1920x1080_01',
           'pair06_tab_1920x1080']


@pytest.fixture(scope='module')
def matchers():
    return load_matchers(ROOT)


def read(name, matchers):
    return read_own(*crop_own(cv2.imread(str(SAMPLES / f'{name}.png'))), matchers)


@needs_samples
@pytest.mark.parametrize('name', KILLER)
def test_killer_huds(name, matchers):
    assert read(name, matchers) == (True, 'killer')


@needs_samples
@pytest.mark.parametrize('name', SURVIVOR)
def test_survivor_huds(name, matchers):
    assert read(name, matchers) == (True, 'survivor')


@needs_samples
@pytest.mark.parametrize('name', OUTSIDE)
def test_nothing_is_shown_outside_a_match(name, matchers):
    assert read(name, matchers) == (False, None)


def test_a_role_needs_two_perks_and_a_majority():
    assert decide({'survivor': 4, 'killer': 0}) == (True, 'survivor')
    assert decide({'survivor': 0, 'killer': 2}) == (True, 'killer')
    assert decide({'survivor': 1, 'killer': 1}) == (False, None)
    assert decide({'survivor': 2, 'killer': 2}) == (True, None)
    assert decide({'killer': 3}) == (True, 'killer')


def test_tiles_fit_inside_the_rect_at_other_resolutions():
    for width, height in ((1920, 1080), (2560, 1440), (1280, 720)):
        x, y, w, h = own_rect(width, height)
        assert x >= 0 and y >= 0 and x + w <= width and y + h <= height
        tiles = own_tiles(np.zeros((h, w, 3), np.uint8), width, height)
        assert all(t is not None for t in tiles)
