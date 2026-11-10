"""Overlay logic with synthetic frames. Real Tab screenshots still have to be added;
none of this proves the overlay works in a match."""
import ctypes
import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.core.display_mode import exclusive_fullscreen
from src.core.perk_catalog import load_catalog, owned_by
from src.core.perk_matcher import PerkMatcher, SlotResult
from src.core.perk_wiki import parse_perks
from src.core.screen_manager import Rect, ScreenError, WindowInfo

HTML = """
<h2><span id="Survivor_Perks">Survivor Perks (2)</span></h2>
<table class="wikitable"><tbody><tr><th>Icon</th><th>Name</th><th>Description</th><th>Character</th></tr>
<tr><th><a><img alt="Bond" src="/images/thumb/IconPerks_bond.png/96px-IconPerks_bond.png?abc"/></a></th>
<th><a href="/wiki/Bond">Bond</a></th>
<td><i><span class="luaClr clr">"Old flavour."</span></i><p>See <i><b>Aura</b></i> of allies within <span class="luaClr">36</span> metres.</p></td>
<td><a>Dwight</a></td></tr>
<tr><th><img src="/images/IconPerks_slipperyMeat.png"/></th><th>Slippery Meat</th><td>Unhook yourself more.</td><td>.All</td></tr>
</tbody></table>
<h3>Killer Perks (1)</h3>
<table><tr><th><img src="/images/IconPerks_eruption.png?x"/></th><th>Eruption</th><td>When a generator explodes.</td><td>Nemesis</td></tr></table>
<h2>History</h2>
<table><tr><th><img src="/images/IconPerks_old.png"/></th><th>Old</th><td>Old text.</td><td>Nobody</td></tr></table>
"""


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope='module')
def matcher(root):
    return PerkMatcher(root)


def tile(root, perk, size=64, color=(40, 90, 40)):
    """A wiki icon drawn on a coloured diamond. Stand-in only, not a game crop."""
    icon = cv2.imdecode(np.fromfile(root / perk.original, np.uint8), cv2.IMREAD_UNCHANGED)
    canvas = np.full((256, 256, 3), 12, np.uint8)
    diamond = np.array([[128, 2], [254, 128], [128, 254], [2, 128]])
    cv2.fillConvexPoly(canvas, diamond, color)
    cv2.polylines(canvas, [diamond], True, (150, 200, 150), 6)
    shrunk = cv2.resize(icon, (150, 150), interpolation=cv2.INTER_AREA)
    alpha = shrunk[:, :, 3:] / 255
    canvas[53:203, 53:203] = (shrunk[:, :, :3] * alpha + canvas[53:203, 53:203] * (1 - alpha)).astype(np.uint8)
    return cv2.resize(canvas, (size, size), interpolation=cv2.INTER_AREA)


def test_table_parser_keeps_owner_and_effect_apart_from_flavour():
    perks = {p['name']: p for p in parse_perks(HTML, 'https://deadbydaylight.wiki.gg/wiki/Perks')}
    assert set(perks) == {'Bond', 'Slippery Meat', 'Eruption'}
    bond = perks['Bond']
    assert bond['role'] == 'survivor' and bond['owner'] == 'Dwight' and bond['flavor'] == '"Old flavour."'
    assert bond['description'] == 'See Aura of allies within 36 metres.'
    assert bond['url'] == 'https://deadbydaylight.wiki.gg/images/IconPerks_bond.png'
    assert perks['Slippery Meat']['owner'] is None
    assert perks['Eruption']['role'] == 'killer' and perks['Eruption']['owner'] == 'Nemesis'


def test_catalog_has_the_data_voice_notes_will_need(root):
    perks = load_catalog(root)
    assert sum(p.role == 'survivor' for p in perks) == 182 and sum(p.role == 'killer' for p in perks) == 154
    assert all(p.description and (root / p.icon).is_file() for p in perks)
    nemesis = owned_by(perks, 'nemesis')
    assert 'Eruption' in {p.name for p in nemesis} and all(p.role == 'killer' for p in nemesis)
    assert any('generator' in p.description.casefold() for p in nemesis)


def test_perks_stay_out_of_the_bloodweb_library(library):
    assert not [e for e in library.entries if e.category == 'perks' or 'perk_' in e.id]


def test_shared_icon_is_not_treated_as_ambiguous(root, matcher):
    perk = next(p for p in load_catalog(root, 'survivor') if p.name == 'Will to Live')
    result = matcher.identify(tile(root, perk))
    assert result.icon_id and set(result.names) == {'Decisive Strike', 'Will to Live'}


@pytest.mark.parametrize('size', [48, 64, 96])
def test_sampled_survivor_icons_are_recognised_on_a_diamond(root, matcher, size):
    perks = load_catalog(root, 'survivor')[::9]
    results = [(p, matcher.identify(tile(root, p, size))) for p in perks]
    assert not [p.name for p, r in results if r.icon_id and p.name not in r.names], 'a wrong icon was accepted'
    # Measured on all 180 rendered icons: 138-148 accepted, 0 wrong. Real crops decide the true rate.
    assert sum(bool(r.icon_id) for _, r in results) >= len(results) * .7


@pytest.mark.parametrize('kind', ['blank', 'noise', 'frame_only'])
def test_unreadable_slot_is_unknown_and_never_guessed(matcher, kind):
    rng = np.random.default_rng(1)
    image = {'blank': np.full((64, 64, 3), 20, np.uint8),
             'noise': rng.integers(0, 255, (64, 64, 3), dtype=np.uint8),
             'frame_only': cv2.polylines(np.full((64, 64, 3), 15, np.uint8), [np.array([[32, 1], [62, 32], [32, 62], [1, 32]])], True, (200, 200, 200), 2)}[kind]
    assert matcher.identify(image).icon_id is None










































def test_exclusive_fullscreen_reads_the_windows_state():
    def query(state):
        return lambda pointer: (setattr(pointer._obj, 'value', state), 0)[1]
    assert exclusive_fullscreen(query(3)) and not exclusive_fullscreen(query(5))
    assert not exclusive_fullscreen(lambda pointer: 1)








def test_capture_tool_saves_lossless_frame_only_when_the_game_is_active(tmp_path):
    from dataclasses import replace
    from scripts.capture_tab_screens import save_frame
    image = np.full((40, 60, 4), 77, np.uint8)
    window = WindowInfo(1, 'DBD', Rect(0, 0, 60, 40), Rect(0, 0, 60, 40), 96, True, 7)
    backend = SimpleNamespace(info=lambda: window, grab=lambda client: (image, 'mss'))
    path = save_frame(backend, tmp_path)
    assert cv2.imread(str(path)).shape == (40, 60, 3) and (cv2.imread(str(path)) == 77).all()
    assert json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))['width'] == 60
    window = replace(window, foreground=False)
    assert save_frame(backend, tmp_path) is None


def test_overlay_setting_is_off_by_default(app, library, tmp_path, monkeypatch):
    from src.gui.main_window import MainWindow
    class Hotkeys:
        ready = True
        def __init__(self, callback, bindings=None): pass
        def close(self): pass
    monkeypatch.setattr('src.gui.main_window.GlobalHotkeys', Hotkeys)
    window = MainWindow(library, tmp_path / 'preferences.json')
    assert window.prefs.settings['match_overlay'] is False and not window.match_overlay.isChecked()
    assert getattr(window, 'overlay_controller', None) is None
    window.close()
