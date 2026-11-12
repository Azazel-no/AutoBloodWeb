"""v1.1.0: the 1v1 and Map tabs open in the main window, start off and are listed in the sidebar."""
import pytest
from PySide6.QtWidgets import QApplication

from src.gui.main_window import MainWindow


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolate_preferences_and_hotkeys(monkeypatch, tmp_path):
    original = MainWindow.__init__
    monkeypatch.setattr(MainWindow, '__init__', lambda self, library: original(self, library, tmp_path / 'preferences.json'))
    class Hotkeys:
        ready = True
        def __init__(self, callback, bindings=None): pass
        def close(self): pass
    monkeypatch.setattr('src.gui.main_window.GlobalHotkeys', Hotkeys)
    yield
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, MainWindow): widget.close()


def test_new_tabs_open_full_height_and_everything_starts_off(app, library):
    w = MainWindow(library)
    assert w.prefs.settings['onevone']['enabled'] is False and w.prefs.settings['map_overlay']['enabled'] is False
    assert {'onevone', 'maps'} <= set(w.nav_buttons) and w.nav_buttons['maps'].toolTip() == w.locale.text('nav_maps')
    for index, page in ((4, 'onevone_page'), (5, 'map_page')):
        w.open_page(index)
        assert getattr(w, page) is not None and w.pages.currentIndex() == index and w.action_panel.isHidden()
    assert getattr(w, 'onevone_controller', None) is None and getattr(w, 'map_controller', None) is None
    assert w.onevone_page.state.text() == w.locale.text('vs_off') and w.map_page.state.text() == w.locale.text('map_off')
    w.open_page(0); assert not w.action_panel.isHidden()
