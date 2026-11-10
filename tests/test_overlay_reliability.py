"""Sections 9-10: the overlay survives errors, starts with the app and matches by nickname when portraits are covered."""
import ctypes
import inspect
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import cv2
import pytest

from src.core import screen_manager
from src.core.match_reader import assign_scores, hud_portraits, load_layout, read_tab, score_rows
from src.core.nicknames import hud_text, name_scores, name_similarity, normalize
from src.core.perk_matcher import SlotResult
from src.gui.match_overlay import KeyWatcher
from tests.test_overlay_controller import SAMPLES, app, controller, layout, needs_samples  # noqa: F401

# Teammates per pair as {HUD slot (2-4): Tab row (1-4)}, checked by eye on the user's samples.
EXPECTED = {'pair02': {2: 4, 3: 2, 4: 3}, 'pair03': {2: 4, 3: 2, 4: 3}, 'pair04': {2: 4, 3: 2, 4: 3}, 'pair05': {2: 4, 3: 2, 4: 3}}


class AnyPerk:
    def identify(self, tile):
        return SlotResult('x', None, ('x',), .9, .1, 'accepted')


# ---- Crash: one Monitor type, bound once ----

def test_monitor_info_is_bound_once_not_per_call():
    source = inspect.getsource(screen_manager.WindowsBackend.info)
    assert 'class Monitor' not in source and 'argtypes' not in source.split('GetMonitorInfoW')[0][-200:]
    backend = screen_manager.WindowsBackend()
    assert backend.u.GetMonitorInfoW.argtypes[1]._type_ is screen_manager.MonitorInfo


def test_monitor_info_from_two_threads_at_once():
    """The live failure: the key watcher thread and the GUI thread calling info() together."""
    backend = screen_manager.WindowsBackend()
    user = ctypes.WinDLL('user32'); user.GetDesktopWindow.restype = ctypes.c_void_p
    desktop = user.GetDesktopWindow()
    errors = []
    def work():
        for _ in range(300):
            try:
                monitor = screen_manager.MonitorInfo(); monitor.size = ctypes.sizeof(monitor)
                assert backend.u.GetMonitorInfoW(backend.u.MonitorFromWindow(desktop, 2), ctypes.byref(monitor))
                try: backend.info()
                except screen_manager.ScreenError: pass
            except Exception as exc:
                errors.append(exc)
    threads = [threading.Thread(target=work) for _ in range(3)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert errors == []


# ---- The watcher never dies ----

def test_key_watcher_keeps_running_after_errors():
    calls, events = [0], []
    def front():
        calls[0] += 1
        if calls[0] <= 3: raise ctypes.ArgumentError('expected LP_Monitor instance instead of pointer to Monitor')
        return True
    watcher = KeyWatcher(0x09, front, events.append, interval=.005, read=lambda code: True)
    watcher.backoff = .005
    watcher.start()
    deadline = time.monotonic() + 3
    while not events and time.monotonic() < deadline: time.sleep(.01)
    watcher.close()
    assert events[:1] == ['key_down'] and watcher.failures == 3


def test_a_stopped_watcher_is_restarted_and_logged(controller):
    x = controller
    x.c.watcher.close()
    assert not x.c.watcher.thread.is_alive()
    x.s.frames = [cv2.imread(str(Path('tests/fixtures/overlay') / 'pair02_hud_portrait_2.png'))]
    x.c.keep_watcher_alive()
    assert x.c.watcher.thread.is_alive() and x.c.restarts == 1
    lines = [json.loads(l) for f in x.log.glob('*.jsonl') for l in f.read_text(encoding='utf-8').splitlines()]
    assert [l['event'] for l in lines].count('watcher_restarted') == 1


def test_an_error_inside_a_tab_press_is_reported_not_raised(controller):
    x = controller
    def broken():
        raise ctypes.ArgumentError('argument 2: TypeError')
    x.s.window = None
    x.c.backend = SimpleNamespace(info=broken, grab=lambda client: (None, 'x'))
    x.c.handle_action('key_down')  # Must not raise.
    x.c.poll()
    assert x.m[-1] == 'overlay_error' and x.c.summary()[0] == 'error'


def test_the_key_watcher_never_reads_the_screen(controller):
    controller.c.backend = SimpleNamespace(info=lambda: pytest.fail('screen read from the key thread'))
    controller.c.game_hwnd = 1
    assert controller.c.game_foreground()
    controller.c.game_hwnd = None
    assert not controller.c.game_foreground()


# ---- Nickname matching ----

def test_nickname_normalisation():
    assert name_similarity('КаКсахар', 'kakcaxap') == 1.  # Cyrillic look-alikes.
    assert name_similarity('[TTV] Thragg', 'Thragg') == 1.
    assert name_similarity('Rebecca Chambers_01', 'Rebecca Chambers') == 1.
    assert name_similarity('LThragg', 'Thragg') >= .75  # OCR junk next to the name.
    assert name_similarity('Sileht', 'Silent') >= .75
    assert name_similarity('dino', 'Harald') < .5
    assert hud_text({'text': 'Rebecca Chambers_01 BOT'}) == 'Rebecca Chambers_01' and normalize('BOT') == 'bot'


def test_nickname_and_portrait_are_equal_methods():
    portraits = {(r, s): -1. for r in (1, 2, 3) for s in (1, 2, 3)}
    names = {(r, s): (.95 if (r, s) in ((3, 1), (1, 2), (2, 3)) else .1) for r in (1, 2, 3) for s in (1, 2, 3)}
    pairs, methods = assign_scores(portraits, [1, 2, 3], names=names)
    assert pairs == {1: 3, 2: 1, 3: 2} and set(methods.values()) == {'nickname'}


def test_a_contradiction_between_portrait_and_nickname_assigns_neither():
    portraits = {(r, s): (.9 if (r, s) == (1, 1) else 0.) for r in (1, 2, 3) for s in (1, 2, 3)}
    names = {(r, s): (.95 if (r, s) == (1, 2) else 0.) for r in (1, 2, 3) for s in (1, 2, 3)}
    pairs, _ = assign_scores(portraits, [1, 2, 3], names=names)
    assert 1 not in pairs.values() and 1 not in pairs and 2 not in pairs


@needs_samples
def test_pair05_own_row_is_row_1_despite_the_bot_badge(layout):
    rows = read_tab(cv2.imread(str(SAMPLES / 'pair05_tab_1920x1080.png')), AnyPerk(), layout)
    assert [r.index + 1 for r in rows if r.own] == [1]


@needs_samples
def test_pair05_portraits_alone_cannot_assign(layout):
    rows = read_tab(cv2.imread(str(SAMPLES / 'pair05_tab_1920x1080.png')), AnyPerk(), layout)
    scores, _ = score_rows(rows, [[p] for p in hud_portraits(cv2.imread(str(SAMPLES / 'pair05_hud_1920x1080.png')), layout)])
    assert assign_scores(scores, [1, 2, 3])[0] == {}


@pytest.fixture(scope='module')
def ocr(root):
    from src.core.nicknames import NicknameOcr
    client = NicknameOcr(root)
    client._start()
    if not client.ready:
        pytest.skip(f'Windows OCR not available: {client.failed}')
    yield client
    client.close()


@needs_samples
@pytest.mark.parametrize('pair', sorted(EXPECTED))
def test_every_pair_maps_with_portraits_and_nicknames(layout, ocr, pair):
    from src.core.nicknames import hud_strips, read_hud_names, read_tab_names
    tab = cv2.imread(str(SAMPLES / f'{pair}_tab_1920x1080.png'))
    hud = cv2.imread(str(SAMPLES / f'{pair}_hud_1920x1080.png'))
    rows = read_tab(tab, AnyPerk(), layout)
    scores, _ = score_rows(rows, [[p] for p in hud_portraits(hud, layout)])
    started = time.perf_counter()
    names = name_scores(read_tab_names(ocr, tab, layout, [r.index for r in rows if not r.own]),
                        read_hud_names(ocr, hud_strips(hud, layout)))
    assert time.perf_counter() - started < 1.5
    pairs, methods = assign_scores(scores, [r.index for r in rows if not r.own], names=names)
    assert {s + 1: r + 1 for s, r in pairs.items()} == EXPECTED[pair], methods


@needs_samples
def test_pair05_is_assigned_by_nickname(layout, ocr):
    from src.core.nicknames import hud_strips, read_hud_names, read_tab_names
    tab = cv2.imread(str(SAMPLES / 'pair05_tab_1920x1080.png'))
    hud = cv2.imread(str(SAMPLES / 'pair05_hud_1920x1080.png'))
    rows = read_tab(tab, AnyPerk(), layout)
    names = name_scores(read_tab_names(ocr, tab, layout, [1, 2, 3]), read_hud_names(ocr, hud_strips(hud, layout)))
    pairs, methods = assign_scores({k: -1. for k in names}, [1, 2, 3], names=names)
    assert {s + 1: r + 1 for s, r in pairs.items()} == EXPECTED['pair05'] and set(methods.values()) == {'nickname'}


# ---- Starts with the app; status line ----

@pytest.fixture
def window_with(library, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from src.gui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    class Hotkeys:
        ready = True
        def __init__(self, callback, bindings=None): pass
        def close(self): pass
    monkeypatch.setattr('src.gui.main_window.GlobalHotkeys', Hotkeys)
    monkeypatch.setattr('src.core.autostart.task_installed', lambda runner=None: False)
    enabled = []
    monkeypatch.setattr('src.gui.match_overlay.MatchOverlayController.enable', lambda self: (enabled.append(1), setattr(self, 'mode', 'ready')))
    monkeypatch.setattr('src.gui.match_overlay.MatchOverlayController.disable', lambda self: setattr(self, 'mode', 'off'))
    windows = []
    def make(overlay):
        path = tmp_path / 'preferences.json'
        path.write_text(json.dumps({'settings': {'match_overlay': overlay}}), encoding='utf-8')
        w = MainWindow(library, path); w.enabled = enabled; windows.append(w)
        return w
    yield make
    for w in windows: w.close()


def test_a_saved_overlay_setting_starts_with_the_app_without_toggling(window_with):
    w = window_with(True)
    assert w.enabled == [1] and w.overlay_controller.mode == 'ready'
    assert w.overlay_status.text().startswith('Overlay') or w.overlay_status.text().startswith('Оверлей')
    assert not w.overlay_status.isHidden()


def test_overlay_off_starts_nothing_and_hides_the_status(window_with):
    w = window_with(False)
    assert w.enabled == [] and getattr(w, 'overlay_controller', None) is None and w.overlay_status.isHidden()


def test_tray_start_with_the_setting_already_on_still_runs_the_overlay(window_with):
    w = window_with(True)
    w.enter_tray_mode()
    assert w.overlay_controller.mode == 'ready' and 'Overlay' in w.tray.toolTip() or 'Оверлей' in w.tray.toolTip()


def test_status_line_says_what_the_overlay_is_doing(window_with):
    w = window_with(True)
    w.locale.language = 'en'
    c = w.overlay_controller
    c.ocr = SimpleNamespace(ready=True, failed=None)
    w.refresh_overlay_status()
    assert w.overlay_status.text() == 'Overlay: waiting for Tab in a match'
    c.ocr = SimpleNamespace(ready=False, failed='no_ocr_language')
    w.refresh_overlay_status()
    assert 'portraits only' in w.overlay_status.text()
    c.last_error_at = time.monotonic()
    w.refresh_overlay_status()
    assert w.overlay_status.text().startswith('Overlay: error')


@needs_samples
def test_one_tab_press_with_covered_portraits_shows_everyone_within_a_second(controller, ocr):
    """pair05 end to end: HUD cached while Tab is closed, one press, assignment by nickname."""
    x = controller
    x.c.ocr = ocr
    tab = cv2.imread(str(SAMPLES / 'pair05_tab_1920x1080.png'))
    hud = cv2.imread(str(SAMPLES / 'pair05_hud_1920x1080.png'))
    x.s.frames = [hud, hud]
    x.c.poll(); x.c.poll()
    x.s.frames = [hud, tab]
    started = time.perf_counter()
    x.c.handle_action('key_down')
    x.c.look_for_scoreboard(); x.c.look_for_scoreboard()  # Two agreeing frames: fully shown.
    elapsed = time.perf_counter() - started
    assert {s + 1: v.row + 1 for s, v in x.c.state.slots.items()} == EXPECTED['pair05'] and sorted(x.o.slots) == [1, 2, 3]
    assert {v.method for v in x.c.state.slots.values()} <= {'nickname', 'portrait+nickname'}
    assert elapsed < 1.0, elapsed
    done = [json.loads(l) for f in x.log.glob('*.jsonl') for l in f.read_text(encoding='utf-8').splitlines() if '"tab_done"' in l]
    assert done[-1]['nicknames']['hud']['slot2'] == 'Thragg'
