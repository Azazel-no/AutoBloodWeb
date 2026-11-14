"""Tab scoreboard reading and Tab-to-HUD mapping from the user's real 1920x1080 frames.

Pinned crops (tests/fixtures/overlay) hold no nicknames. Full-frame tests use the
git-ignored samples in debug/match_samples and skip when they are absent. None of
this proves behaviour in a live match.
"""
import hashlib
import json
from pathlib import Path

import cv2
import pytest

from src.core.match_reader import (EMPTY, TabRow, assign, hud_portraits, is_scoreboard, load_layout, portrait_feature,
                                   portrait_similarity, read_tab)
from src.core.perk_matcher import PerkMatcher

FIXTURES = Path('tests/fixtures/overlay')
SAMPLES = Path('debug/match_samples')


@pytest.fixture(scope='module')
def layout(root):
    return load_layout(root)


@pytest.fixture(scope='module')
def matcher(root):
    return PerkMatcher(root)


def fixture(name):
    return cv2.imread(str(FIXTURES / name))


def tab_portrait(pair, row, layout):
    image = fixture(f'{pair}_tab_portrait_{row}.png')
    c = image.shape[0] // 2
    return portrait_feature(image, (c, c), layout['tab']['portrait_radius'])


def hud_portrait(name, layout):
    image = fixture(name)
    c = image.shape[0] // 2
    return portrait_feature(image, (c, c), layout['hud']['portrait_radius'])


def test_pinned_crops_are_unchanged():
    manifest = json.loads((FIXTURES / 'manifest.json').read_text(encoding='utf-8'))['sha256']
    assert len(manifest) == 52
    assert all(hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == sha for name, sha in manifest.items())


def test_layout_maps_tab_perk_slots_to_the_games_diamond_positions(layout):
    assert {k: v for k, v in layout['perk_positions'].items() if k != 'note'} == {'1': 'bottom', '2': 'right', '3': 'left', '4': 'top'}


@pytest.mark.parametrize('pair,expected', [('pair02', {1: 4, 2: 2, 3: 3}), ('pair03', {1: 4, 2: 2, 3: 3})])
def test_teammates_map_to_hud_slots_by_portrait_not_by_order(layout, pair, expected):
    """pair02: Steve (Tab row 4) at HUD slot 2, Vee (row 2) at slot 3, Mikaela (row 3) at slot 4."""
    rows = [TabRow(r - 1, r == 1, (), (), tab_portrait(pair, r, layout)) for r in range(1, 5)]
    portraits = [hud_portrait(f'{pair}_hud_portrait_{s}.png', layout) for s in range(1, 5)]
    mapping, _ = assign(rows, portraits)
    assert {slot: row.index + 1 for slot, row in mapping.items()} == expected


def test_similar_portraits_in_one_hud_are_not_confused(layout):
    """hud_04: two dark-haired men must score below a true Tab/HUD match."""
    slots = [hud_portrait(f'hud04_portrait_{s}.png', layout) for s in range(1, 5)]
    cross = portrait_similarity(slots[1], slots[2])
    true_matches = [portrait_similarity(tab_portrait('pair02', 4, layout), hud_portrait('pair02_hud_portrait_2.png', layout)),
                    portrait_similarity(tab_portrait('pair03', 4, layout), hud_portrait('pair03_hud_portrait_2.png', layout))]
    assert cross < min(true_matches) - .04


def test_an_uncertain_slot_stays_unassigned(layout):
    rows = [TabRow(r - 1, r == 1, (), (), tab_portrait('pair02', r, layout)) for r in range(1, 5)]
    portraits = [hud_portrait(f'pair02_hud_portrait_{s}.png', layout) for s in range(1, 5)]
    portraits[2] = portraits[1]  # Two identical portraits: the column margin fails.
    mapping, _ = assign(rows, portraits)
    assert 2 not in mapping and 1 not in mapping


def test_real_tab_tiles_are_recognised_or_left_unknown(matcher):
    recognised = unknown = 0
    for name in sorted(FIXTURES.glob('pair0*_tab_perk_*.png')):
        tile = cv2.imread(str(name))
        c = tile.shape[0] // 2
        result = matcher.identify(tile[c - 26:c + 26, c - 26:c + 26])
        recognised += bool(result.icon_id)
        unknown += not result.icon_id
    assert recognised >= 28 and recognised + unknown == 32


def test_own_row_perks_read_the_same_in_both_matches(matcher):
    names = []
    for pair in ('pair02', 'pair03'):
        row = []
        for slot in range(1, 5):
            tile = fixture(f'{pair}_tab_perk_1_{slot}.png')
            c = tile.shape[0] // 2
            row.append(matcher.identify(tile[c - 26:c + 26, c - 26:c + 26]).names)
        names.append(row)
    assert names[0] == names[1] and all(names[0])


needs_samples = pytest.mark.skipif(not (SAMPLES / 'pair02_tab_1920x1080.png').is_file(), reason='user samples are git-ignored')


@needs_samples
def test_scoreboard_is_told_apart_from_every_hud_frame(layout):
    for path in sorted(SAMPLES.glob('*.png')):
        expected = 'tab' in path.name
        assert is_scoreboard(cv2.imread(str(path)), layout) == expected, path.name


@needs_samples
def test_end_to_end_pair02(layout, matcher):
    rows = read_tab(cv2.imread(str(SAMPLES / 'pair02_tab_1920x1080.png')), matcher, layout)
    assert [r.own for r in rows] == [True, False, False, False]
    mapping, _ = assign(rows, hud_portraits(cv2.imread(str(SAMPLES / 'pair02_hud_1920x1080.png')), layout))
    assert {slot: row.index for slot, row in mapping.items()} == {1: 3, 2: 1, 3: 2}
    assert all(len(r.slots) == 4 and len(r.tiles) == 4 for r in rows)


@needs_samples
def test_end_to_end_pair06_reads_all_twelve_teammate_perks_including_boons(layout, matcher):
    rows = read_tab(cv2.imread(str(SAMPLES / 'pair06_tab_1920x1080.png')), matcher, layout)
    teammates = [r for r in rows if not r.own]
    assert all(s.icon_id for r in teammates for s in r.slots)
    jake = [s.names[0] for s in teammates[-1].slots]
    assert jake == ['Boon: Circle of Healing', 'Boon: Shadow Step', 'Boon: Exponential', 'Boon: Dark Theory']


@needs_samples
def test_empty_perk_slot_is_empty_not_unknown(layout, matcher):
    rows = read_tab(cv2.imread(str(SAMPLES / 'pair03_tab_1920x1080.png')), matcher, layout)
    assert rows[1].slots[3].icon_id == EMPTY


@needs_samples
@pytest.mark.parametrize('width,height', [(2560, 1440), (1280, 720)])
def test_layout_scales_with_client_height(layout, matcher, width, height):
    image = cv2.resize(cv2.imread(str(SAMPLES / 'pair02_tab_1920x1080.png')), (width, height), interpolation=cv2.INTER_AREA)
    assert is_scoreboard(image, layout)
    assert [r.own for r in read_tab(image, matcher, layout)] == [True, False, False, False]


# --- 14.11 regression: a Path error in the near-miss tile branch broke every Tab read ------------------
@needs_samples
@pytest.mark.parametrize('pair', ['pair02', 'pair03', 'pair05'])
def test_full_tab_read_with_the_close_call_branch_on_every_tile(layout, matcher, pair, tmp_path, monkeypatch):
    import src.core.match_reader as mr
    from src.utils.images import read_image
    monkeypatch.setattr(mr, 'LOW_MARGIN', 2.)                 # every accepted perk takes the close-call branch
    monkeypatch.setattr(matcher, 'unknown_dir', tmp_path, raising=False)
    rows = read_tab(read_image(SAMPLES / f'{pair}_tab_1920x1080.png'), matcher, layout)
    assert len(rows) == 4 and any(s.icon_id for r in rows for s in r.slots)
    assert list((tmp_path / 'close_calls').glob('*.png')) and list((tmp_path / 'close_calls').glob('*.json'))


@needs_samples
def test_a_failing_diagnostic_save_never_breaks_the_read(layout, matcher, tmp_path, monkeypatch):
    import src.core.match_reader as mr
    from src.utils.images import read_image
    monkeypatch.setattr(mr, 'LOW_MARGIN', 2.); monkeypatch.setattr(matcher, 'unknown_dir', tmp_path, raising=False)
    monkeypatch.setattr('src.utils.images.write_image', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('disk gone')))
    rows = read_tab(read_image(SAMPLES / 'pair02_tab_1920x1080.png'), matcher, layout)
    assert len(rows) == 4 and any(s.icon_id for r in rows for s in r.slots)
