"""J8 (15.11): capture test modes and the WGC overlay capture. Fakes only; NVIDIA itself is checked by the user."""
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from src.core import overlay_capture as oc
from src.core.screen_manager import Rect, ScreenError, WindowInfo, WindowsBackend

ROOT = Path(__file__).resolve().parents[1]
WINDOW = WindowInfo(5, 'DBD', Rect(100, 50, 400, 300), Rect(0, 0, 1920, 1080), 96, True, 9)


@pytest.fixture(autouse=True)
def policy():
    yield
    oc.configure('normal', 'gdi')


@pytest.fixture(scope='module')
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_no_capture_mode_refuses_every_overlay_grab_but_not_other_backends(monkeypatch):
    calls = []
    monkeypatch.setattr(WindowsBackend, 'grab', lambda self, rect: (calls.append(rect), (np.zeros((2, 2, 3), np.uint8), 'mss'))[1])
    oc.configure('no_capture')
    with pytest.raises(ScreenError):
        oc.OverlayBackend().grab(Rect(0, 0, 2, 2))
    assert calls == []
    WindowsBackend().grab(Rect(0, 0, 2, 2))           # Bloodweb automation's backend is untouched
    oc.configure('normal')
    oc.OverlayBackend().grab(Rect(0, 0, 2, 2))
    assert len(calls) == 2


def test_wgc_method_crops_the_shared_window_frame(monkeypatch):
    frame = np.arange(300 * 400 * 3, dtype=np.uint32).astype(np.uint8).reshape(300, 400, 3)
    made = []

    class Probe:
        def __init__(self, window, factory=None, bounds=None): made.append(window); self.grabs = 0
        def grab(self, window, timeout=1.5): self.grabs += 1; return frame, 1.
        def close(self): pass

    monkeypatch.setattr('src.core.window_probe_capture.WindowProbeCapture', Probe)
    monkeypatch.setattr(oc.OverlayBackend, 'info', lambda self: WINDOW)
    monkeypatch.setattr(WindowsBackend, 'grab', lambda self, rect: pytest.fail('no desktop BitBlt with WGC'))
    oc.configure('normal', 'wgc')
    image, provider = oc.OverlayBackend().grab(Rect(110, 70, 30, 20))
    assert provider == 'wgc' and image.shape == (20, 30, 3)
    assert np.array_equal(image, frame[20:40, 10:40])
    oc.OverlayBackend().grab(Rect(100, 50, 400, 300))   # a second overlay reuses the one session
    assert len(made) == 1
    with pytest.raises(ScreenError):
        oc.OverlayBackend().grab(Rect(450, 50, 100, 10))
    oc.configure(method='gdi'); assert oc._WGC['capture'] is None


def test_no_windows_mode_never_maps_an_overlay(app):
    from PySide6.QtWidgets import QWidget
    from src.gui.map_overlay import MapWindow
    oc.configure('no_windows')
    window = MapWindow(ROOT); window.setGeometry(0, 0, 40, 40)
    window.show(); window.map_blank('test')
    assert not QWidget.isVisible(window)
    oc.configure('normal'); window.show(); assert QWidget.isVisible(window)
    window.retire(); window.deleteLater()


def test_no_capture_mode_shows_a_test_cluster_after_tab_without_reading(app, root, tmp_path, monkeypatch):
    from src.gui.match_overlay import MatchOverlayController
    shown = []
    overlay = SimpleNamespace(present=lambda client, layout, slots: shown.append(slots), hide=lambda: None,
                              clear=lambda: None, isVisible=lambda: False)
    backend = SimpleNamespace(info=lambda: WINDOW, grab=lambda rect: pytest.fail('no capture in this mode'))
    ocr = SimpleNamespace(ready=False, failed=None, start=lambda: None, close=lambda: None)
    c = MatchOverlayController(root, lambda: False, backend, None, overlay, key_reader=lambda code: False, log_dir=tmp_path,
                               ocr=ocr, foreground_window=lambda: 5)
    c.mode = 'on'
    oc.configure('no_capture')
    c.handle_action('key_down'); c.handle_action('key_up')
    assert len(shown) == 1 and sorted(shown[0]) == [1, 2, 3]
    assert all(r.icon_id is None for perks in shown[0].values() for r in perks.slots)
    c.test_hide.stop()
