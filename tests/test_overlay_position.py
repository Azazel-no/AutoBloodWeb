"""Overlay placement: left of the portrait or right of the nickname."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.match_reader import load_layout,right_centre_x

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('height,width',[(1080,1920),(1440,2560),(2160,3840),(900,1600)])
@pytest.mark.parametrize('size',['normal','large'])
def test_right_cluster_starts_after_the_nicknames_and_clears_the_portrait(app,height,width,size):
    from src.gui.match_overlay import PerkOverlay
    layout=load_layout(ROOT);hud=layout['hud'];o=PerkOverlay(ROOT);o.layout_data=layout;o.size_name=size
    o.client=SimpleNamespace(x=0,y=0,width=width,height=height);o.position='right';scale=height/1080
    for slot,y in enumerate(hud['portrait_y']):
        (cx,cy),d,centres=o.cluster_geometry(slot)
        left=min(x for x,_ in centres.values())-d/2
        assert left>=(hud['right_x']-1)*scale and left>(hud['portrait_x']+hud['portrait_radius'])*scale
        assert cy==pytest.approx(y*scale,abs=1)
    o.position='left';(cx,_),_,_=o.cluster_geometry(0)
    assert cx<hud['portrait_x']*scale


def test_right_timer_uses_the_same_column_rule():
    layout=load_layout(ROOT)
    assert right_centre_x(layout,30)-30>layout['hud']['right_x']


def test_no_overlay_window_is_the_size_of_the_whole_game():
    """J4 (14.11): NVIDIA recording turned itself off while a monitor-sized topmost overlay was up."""
    from types import SimpleNamespace
    from src.gui.match_overlay import hud_overlay_rect
    for width, height, ratio in ((1920, 1080, 1.), (2560, 1440, 1.25), (3840, 2160, 1.5)):
        client = SimpleNamespace(x=0, y=0, width=width, height=height)
        x, y, w, h = hud_overlay_rect(client, ratio)
        assert w * ratio <= width * .5 + 1 and h * ratio == pytest.approx(height, abs=1)
    import inspect
    from src.gui import sable_overlay
    assert 'client.height - 2' in inspect.getsource(sable_overlay.SableWindow.present)


def test_every_overlay_window_has_its_styles_before_the_first_show(app):
    """J5 (14.11): the native window and its no-activate/transparent/tool/topmost styles exist from app start,
    so the first show at match start does not create a new top-level window or flash an activatable one."""
    import ctypes
    from src.gui.match_overlay import PerkOverlay, WS_EX
    from src.gui.map_overlay import MapWindow
    from src.gui.onevone_overlay import ScoreWindow
    from src.gui.chase_overlay import TimerWindow, DanceWindow
    from src.gui.crosshair_overlay import CrosshairWindow
    from src.gui.sable_overlay import SableWindow
    from src.gui.ds_overlay import DsOverlay
    u = ctypes.windll.user32; u.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]; u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    for cls in (PerkOverlay, MapWindow, ScoreWindow, TimerWindow, DanceWindow, CrosshairWindow, SableWindow, DsOverlay):
        window = cls(ROOT); assert not window.isVisible()
        ex = u.GetWindowLongPtrW(int(window.winId()), -20)
        assert ex & WS_EX == WS_EX, (cls.__name__, hex(ex))
        window.deleteLater()


def test_content_sized_overlay_boxes_stay_well_inside_the_game():
    from src.gui.map_overlay import map_box
    from src.gui.chase_overlay import timer_box, dance_box
    from src.gui.onevone_overlay import bar_rect
    for width, height in ((1920, 1080), (2560, 1440), (3840, 2160)):
        _, _, side = map_box(width, height, 1.5); assert side < height * .6
        _, _, w, h = timer_box(width, height, 2.); assert w < width / 2 and h < height / 4
        rect = bar_rect(width, height, 1.5); assert rect.width() < width and rect.height() < height / 3
        _, _, w, h = dance_box(width, height, dict(dance_size=60, dance_x=80, dance_y=50), 1.); assert w < width and h < height


def test_nvidia_overlay_in_front_still_counts_as_the_game_in_front(monkeypatch):
    """J6 (14.11): starting a recording brings NVIDIA's overlay to the front; our overlays hid, which changed the
    composition over the HDR game and NVIDIA stopped the recording 1.5 s later (its CaptureCore.log)."""
    from src.gui import match_overlay as mo
    names = {10: 'deadbydaylight-win64-shipping.exe', 20: 'nvidia overlay.exe', 30: 'explorer.exe'}
    monkeypatch.setattr(mo, 'window_process', lambda hwnd: names.get(hwnd, ''))
    assert mo.game_in_front(10, lambda: 10) and mo.game_in_front(10, lambda: 20)
    assert not mo.game_in_front(10, lambda: 30) and not mo.game_in_front(None, lambda: 20)


def test_recording_friendly_mode_blanks_instead_of_hiding(app, monkeypatch):
    from src.gui import match_overlay as mo
    from src.gui.map_overlay import MapWindow
    from PySide6.QtWidgets import QWidget
    monkeypatch.setattr(mo, 'dbd_or_overlay_in_front', lambda: True)
    busy = [False]; mo.set_recording_friendly(True, lambda: busy[0])
    try:
        window = MapWindow(ROOT); window.setGeometry(0, 0, 50, 50); window.show()
        assert window.isVisible() and not window.blank
        window.hide()                                   # in play: stays on screen, clear
        assert QWidget.isVisible(window) and window.blank and not window.isVisible()
        window.show(); assert window.isVisible() and not window.blank
        window.hide(); busy[0] = True; window.hide()     # automation: really hidden
        assert not QWidget.isVisible(window)
        busy[0] = False; window.show(); window.hide(); mo.hide_all_overlays()
        assert not QWidget.isVisible(window)
        window.show(); window.hide(); mo.set_recording_friendly(False)   # switching the mode off hides blank windows
        assert not QWidget.isVisible(window)
        assert getattr(MapWindow.paintEvent, 'blank_aware', False) and getattr(mo.PerkOverlay.paintEvent, 'blank_aware', False)
    finally:
        mo.set_recording_friendly(False, lambda: False)


def test_without_the_mode_hide_really_hides(app):
    from src.gui import match_overlay as mo
    from src.gui.crosshair_overlay import CrosshairWindow
    from PySide6.QtWidgets import QWidget
    window = CrosshairWindow(ROOT); window.setGeometry(0, 0, 20, 20); window.show(); window.hide()
    assert not QWidget.isVisible(window) and not window.blank
