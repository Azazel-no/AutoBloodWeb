"""Overlay controller, key watcher and diamond cluster. Fakes only; no live match."""
import ctypes
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.core.match_reader import EMPTY, TabRow, load_layout, portrait_feature
from src.core.match_state import MatchState
from src.core.perk_matcher import SlotResult
from src.core.screen_manager import Rect, ScreenError, WindowInfo
from src.gui.match_overlay import WS_EX, KeyWatcher, MatchOverlayController, PerkOverlay, key_code

SAMPLES = Path('debug/match_samples')
FIXTURES = Path('tests/fixtures/overlay')
needs_samples = pytest.mark.skipif(not (SAMPLES / 'pair02_tab_1920x1080.png').is_file(), reason='user samples are git-ignored')


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope='module')
def layout(root):
    return load_layout(root)


def perk(name):
    return SlotResult('icon_' + name, 'x.png', (name,), .95, .1, 'accepted')


def feature(name, radius):
    image = cv2.imread(str(FIXTURES / name))
    c = image.shape[0] // 2
    return portrait_feature(image, (c, c), radius)


def rows_and_huds(layout, pair='pair02', perks=('A', 'B', 'C', 'D')):
    rows = [TabRow(r - 1, r == 1, tuple(perk(f'{p}{r}') for p in perks), (None,) * 4,
                   feature(f'{pair}_tab_portrait_{r}.png', layout['tab']['portrait_radius'])) for r in range(1, 5)]
    huds = [feature(f'{pair}_hud_portrait_{s}.png', layout['hud']['portrait_radius']) for s in range(1, 5)]
    return rows, huds


def test_key_names_resolve_and_tab_is_the_default():
    assert key_code(None) == 0x09 and key_code('tab') == 0x09 and key_code('F2') == 0x71 and key_code('Q') == ord('Q')
    with pytest.raises(ValueError):
        key_code('NOT A KEY')


def test_key_is_read_only_while_the_game_is_in_front():
    events, held, front = [], [True], [False]
    watcher = KeyWatcher(0x09, lambda: front[0], events.append, read=lambda code: held[0])
    watcher.step()
    assert events == []
    front[0] = True
    watcher.step(); watcher.step()
    assert events == ['key_down']
    held[0] = False
    watcher.step()
    assert events == ['key_down', 'key_up']


def test_state_locks_perks_to_slots_and_updates_only_changes(layout):
    state = MatchState()
    rows, huds = rows_and_huds(layout)
    changed, _ = state.apply(rows, huds)
    assert sorted(changed) == [1, 2, 3]
    assert state.slots[1].slots[0].names == ('A4',)  # Steve (Tab row 4) at HUD slot 2.
    changed, _ = state.apply(rows, huds)
    assert changed == []
    # Never downgrade or replace: a known perk stays even if a later read differs.
    rows[1] = replace(rows[1], slots=(perk('Z'),) + rows[1].slots[1:])
    changed, _ = state.apply(rows, huds)
    assert changed == [] and state.slots[2].slots[0].names == ('A2',)


def test_a_reread_only_fills_unknown_perks(layout):
    state = MatchState()
    rows, huds = rows_and_huds(layout)
    unknown = SlotResult(None, None, (), 0, 0, 'low')
    rows[1] = replace(rows[1], slots=(unknown,) + rows[1].slots[1:])
    state.apply(rows, huds)
    assert state.slots[2].slots[0].icon_id is None and not state.all_known()
    worse = replace(rows[2], slots=(unknown,) * 4)
    state.apply([rows[0], replace(rows[1], slots=(perk('F'),) + rows[1].slots[1:]), worse, rows[3]], huds)
    assert state.slots[2].slots[0].names == ('F',) and state.slots[3].slots[0].names == ('A3',)
    assert state.all_known()


def test_a_new_match_starts_over_only_after_the_lobby(layout):
    state = MatchState()
    rows, huds = rows_and_huds(layout, 'pair02')
    state.apply(rows, huds)
    other_rows, other_huds = rows_and_huds(layout, 'pair03', perks=('N', 'O', 'P', 'Q'))
    state.apply(other_rows, other_huds)  # No lobby seen: nothing is replaced.
    assert all(s.slots[0].names[0].startswith('A') for s in state.slots.values())
    state.clear()  # What the controller does in the lobby.
    state.apply(other_rows, other_huds)
    assert all(s.slots[0].names[0].startswith('N') for s in state.slots.values())


def test_slots_clear_after_the_portraits_are_gone_for_several_checks(layout):
    state = MatchState()
    rows, huds = rows_and_huds(layout)
    state.apply(rows, huds)
    blank = [portrait_feature(np.zeros((90, 90, 3), np.uint8), (45, 45), 36)] * 4
    for _ in range(MatchState.absent_checks - 1):
        assert state.check_presence(blank)
    assert not state.check_presence(blank) and not state.slots
    state.apply(rows, huds)
    assert state.check_presence(huds)


class FakeOverlay:
    def __init__(self): self.visible = False; self.slots = None
    def isVisible(self): return self.visible
    def hide(self): self.visible = False
    def clear(self): self.visible = False; self.slots = None
    def present(self, client, layout, slots): self.visible = True; self.slots = dict(slots)


class FakeMatcher:
    def identify(self, tile):
        return perk('seen')


@pytest.fixture
def controller(app, root, tmp_path, monkeypatch):
    monkeypatch.setattr('src.gui.match_overlay.exclusive_fullscreen', lambda: False)
    state = SimpleNamespace(active=False, frames=[], window=WindowInfo(1, 'DBD', Rect(0, 0, 1920, 1080), Rect(0, 0, 1920, 1080), 96, True, 7))
    def grab(client):
        return state.frames.pop(0) if len(state.frames) > 1 else state.frames[0], 'mss'
    backend = SimpleNamespace(info=lambda: state.window, grab=grab)
    overlay = FakeOverlay()
    ocr = SimpleNamespace(ready=False, failed=None, start=lambda: None, close=lambda: None)
    c = MatchOverlayController(root, lambda: state.active, backend, FakeMatcher, overlay, key_reader=lambda code: False, log_dir=tmp_path,
                               ocr=ocr, foreground_window=lambda: 1)
    import threading
    real_thread = threading.Thread
    def thread(target=None, args=(), name=None, **kw):
        # Read the scoreboard inline; any other thread is real.
        if name == 'OverlayRead':
            return SimpleNamespace(start=lambda: target(*args))
        return real_thread(target=target, args=args, name=name, **kw)
    monkeypatch.setattr('src.gui.match_overlay.threading.Thread', thread)
    messages = []
    c.status.connect(messages.append)
    c.enable()
    yield SimpleNamespace(c=c, o=overlay, s=state, m=messages, log=tmp_path)
    c.disable()


@needs_samples
def test_tab_press_reads_the_scoreboard_and_draws_three_teammates(controller):
    x = controller
    tab = cv2.imread(str(SAMPLES / 'pair02_tab_1920x1080.png'))
    hud = cv2.imread(str(SAMPLES / 'pair02_hud_1920x1080.png'))
    x.s.frames = [hud, tab, tab]
    x.c.handle_action('key_down')
    x.c.look_for_scoreboard(); x.c.look_for_scoreboard()  # Two agreeing frames: fully shown.
    assert x.o.visible and sorted(x.o.slots) == [1, 2, 3]
    assert list(x.log.glob('*.jsonl'))


@needs_samples
def test_a_frame_without_the_scoreboard_is_never_read(controller):
    x = controller
    hud = cv2.imread(str(SAMPLES / 'pair02_hud_1920x1080.png'))
    x.s.frames = [hud]
    x.c.handle_action('key_down')
    x.c.look_for_scoreboard(); x.c.look_for_scoreboard()  # Two agreeing frames: fully shown.
    assert not x.o.visible and not x.c.state.slots


def test_nothing_is_captured_during_bloodweb_automation(controller):
    x = controller
    x.s.active = True
    x.s.frames = [np.zeros((1080, 1920, 3), np.uint8)]
    x.c.handle_action('key_down')
    assert x.m[-1] == 'overlay_blocked_automation' and not x.c.scoreboard_wait.isActive()


def test_exclusive_fullscreen_warns_and_reads_nothing(controller, monkeypatch):
    monkeypatch.setattr('src.gui.match_overlay.exclusive_fullscreen', lambda: True)
    controller.c.handle_action('key_down')
    assert controller.m[-1] == 'overlay_exclusive_fullscreen' and not controller.c.scoreboard_wait.isActive()


def test_overlay_hides_when_the_game_loses_focus_and_suspends_for_automation(controller, layout):
    x = controller
    rows, huds = rows_and_huds(layout)
    x.c.state.apply(rows, huds)
    x.c.window = x.s.window
    x.c.show()
    assert x.o.visible
    x.s.window = replace(x.s.window, foreground=False)
    x.c.poll()
    assert not x.o.visible and x.c.state.slots
    x.s.active = True
    x.c.poll()
    x.c.suspend()
    assert not x.c.state.slots


def test_window_is_click_through_and_never_takes_focus(app, root):
    overlay = PerkOverlay(root)
    flags = overlay.windowFlags()
    assert flags & Qt.WindowTransparentForInput and flags & Qt.WindowDoesNotAcceptFocus
    assert flags & Qt.WindowStaysOnTopHint and flags & Qt.Tool and flags & Qt.FramelessWindowHint
    overlay.setGeometry(0, 0, 200, 200)
    overlay.show()
    app.processEvents()
    if app.platformName() == 'windows':
        u = ctypes.windll.user32
        u.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        assert u.GetWindowLongPtrW(int(overlay.winId()), -20) & WS_EX == WS_EX
    assert not overlay.isActiveWindow()
    overlay.close()


@pytest.mark.parametrize('size_name,diamond', [('normal', 25), ('large', 42)])
@pytest.mark.parametrize('width,height', [(1920, 1080), (2560, 1440), (1280, 720)])
def test_cluster_is_a_diamond_left_of_the_portrait_within_the_slot_pitch(app, root, layout, width, height, size_name, diamond):
    overlay = PerkOverlay(root)
    overlay.size_name = size_name
    overlay.layout_data = layout
    overlay.client = Rect(0, 0, width, height)
    overlay.ratio = 1.
    scale = height / 1080
    (cx, cy), size, centres = overlay.cluster_geometry(1)
    assert centres[4][1] < cy < centres[1][1]  # Slot 4 top, slot 1 bottom.
    assert centres[3][0] < cx < centres[2][0]  # Slot 3 left, slot 2 right.
    extent = max(abs(y - cy) for _, y in centres.values()) + size / 2
    assert 2 * extent <= 88 * scale  # Fits the HUD slot pitch.
    assert max(x for x, _ in centres.values()) + size / 2 <= (128 - 36) * scale  # Left of the portrait.
    assert abs(size - diamond * scale) <= 1
    assert min(x for x, _ in centres.values()) - size / 2 >= 0  # Not off the screen edge.
    assert overlay.backdrop_limit() * 2 <= 88 * scale


def test_painting_known_unknown_and_empty_diamonds_does_not_fail(app, root, layout):
    from src.core.match_state import SlotPerks
    overlay = PerkOverlay(root)
    tile = np.full((52, 52, 3), (120, 40, 110), np.uint8)
    slots = {1: SlotPerks((perk('a'), SlotResult(None, None, (), 0, 0, 'low'), SlotResult(EMPTY, None, (), 0, 0, 'empty_slot'), perk('b')),
                          (tile, tile, None, tile), None)}
    overlay.present(Rect(0, 0, 1280, 720), layout, slots)
    assert not overlay.grab().isNull()
    overlay.close()


def test_overlay_code_has_no_input_memory_or_network_calls():
    banned = ('SendInput', 'mouse_event', 'keybd_event', 'SetCursorPos', 'SetPhysicalCursorPos', 'ReadProcessMemory',
              'WriteProcessMemory', 'OpenProcess', 'urllib', 'socket', 'requests', 'http.client', 'public_fetch', 'perk_wiki',
              'input_control', 'SetForegroundWindow', 'SetWindowsHookEx')
    for name in ('src/gui/match_overlay.py', 'src/core/display_mode.py', 'scripts/capture_tab_screens.py',
                 'src/core/match_reader.py', 'src/core/match_state.py', 'src/core/perk_matcher.py', 'src/core/perk_catalog.py'):
        text = Path(name).read_text(encoding='utf-8')
        assert not [word for word in banned if word in text], name


def test_tab_does_nothing_in_a_killer_match(controller):
    x = controller
    x.c.match_role = 'killer'
    x.c.handle_action('key_down')
    assert not x.c.scoreboard_wait.isActive()
