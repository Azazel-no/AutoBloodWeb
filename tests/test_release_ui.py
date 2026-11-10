"""Release main window: Start, Stop and three toggles are visible; engineering controls only in developer mode."""
import json

import pytest


@pytest.fixture
def make_window(library, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from src.gui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    class Hotkeys:
        ready = True
        def __init__(self, callback, bindings=None): pass
        def close(self): pass
    monkeypatch.setattr('src.gui.main_window.GlobalHotkeys', Hotkeys)
    monkeypatch.setattr('src.gui.match_overlay.MatchOverlayController.enable', lambda self: None)
    monkeypatch.setattr('src.core.autostart.task_installed', lambda runner=None: False)
    windows = []
    def make(prefs=None):
        path = tmp_path / 'preferences.json'
        if prefs is not None:
            path.write_text(json.dumps(prefs), encoding='utf-8')
        w = MainWindow(library, path)
        w.show(); app.processEvents()
        windows.append(w)
        return w
    yield make
    for w in windows:
        w.close()


def essentials(w):
    return (w.buttons['start'], w.buttons['stop'], w.auto_prestige)


def test_start_stop_and_prestige_are_visible_and_the_rest_lives_in_settings(make_window):
    w = make_window()
    inside = w.centralWidget().rect()
    for widget in essentials(w):
        assert widget.isVisible(), widget
        top_left = widget.mapTo(w.centralWidget(), widget.rect().topLeft())
        bottom_right = widget.mapTo(w.centralWidget(), widget.rect().bottomRight())
        assert inside.contains(top_left) and inside.contains(bottom_right), widget
        assert not w.body_scroll.widget().isAncestorOf(widget)
    settings = w.pages.widget(1)
    assert all(settings.isAncestorOf(x) for x in (w.match_overlay, w.start_with_dbd, w.ds_timer, w.language))
    assert w.hotkey_hint.isVisible() and 'F9' in w.hotkey_hint.text() and 'F7' not in w.hotkey_hint.text()


def test_engineering_controls_appear_only_in_developer_mode(make_window):
    w = make_window()
    hidden = (w.buttons['pause'], w.scan_button, w.test_button, w.run_mode, w.dry_run, w.turbo_burst,
              w.native_chain, w.counters_card, w.preview_card, w.debug_card, w.logs_button)
    assert not any(x.isVisible() for x in hidden)
    assert not w.recalibrate_action.isVisible() and w.logs_action.isVisible() and w.developer_action.isVisible()
    w.developer_action.trigger()
    assert w.prefs.settings['developer_mode'] is True
    assert w.buttons['pause'].isVisible() and w.run_mode.isVisible() and w.dry_run.isVisible() and w.debug_card.isVisible()
    w.developer_action.trigger()
    assert not w.run_mode.isVisible() and w.prefs.settings['developer_mode'] is False


def test_fresh_install_runs_continuously_buys_and_keeps_optional_features_off(make_window):
    w = make_window()
    assert w.run_mode.currentData() == 'continuous' and not w.dry_run.isChecked()
    assert not w.auto_prestige.isChecked() and not w.match_overlay.isChecked() and not w.start_with_dbd.isChecked()
    assert w.turbo_burst.isChecked() and w.fast_opening.isChecked() and w.experimental_persistent.isChecked()
    assert not w.native_chain.isChecked()


def test_hidden_test_mode_or_dry_run_cannot_silently_change_a_normal_start(make_window):
    w = make_window({'settings': {'dry_run': True, 'run_mode': 'test', 'turbo_burst': False, 'auto_prestige': True}})
    assert w.run_mode.currentData() == 'continuous' and not w.dry_run.isChecked()
    assert not w.turbo_burst.isChecked() and w.auto_prestige.isChecked()


def test_developer_mode_keeps_saved_engineering_choices(make_window):
    w = make_window({'settings': {'developer_mode': True, 'dry_run': True, 'run_mode': 'test'}})
    assert w.run_mode.isVisible() and w.dry_run.isChecked() and w.run_mode.currentData() == 'test'


def test_start_with_dbd_checkbox_installs_and_removes_the_task(make_window, monkeypatch):
    calls = []
    monkeypatch.setattr('src.core.autostart.install_task', lambda root, runner=None: calls.append('install'))
    monkeypatch.setattr('src.core.autostart.uninstall_task', lambda runner=None: calls.append('remove'))
    w = make_window()
    w.start_with_dbd.click()
    w.start_with_dbd.click()
    assert calls == ['install', 'remove']


def test_overlay_size_is_large_by_default_and_a_developer_option(make_window):
    w = make_window()
    assert w.prefs.settings['overlay_size'] == 'large' and w.overlay_size.currentData() == 'large'
    assert not w.overlay_size.isVisible()
    w.overlay_size.setCurrentIndex(w.overlay_size.findData('normal'))
    assert w.prefs.settings['overlay_size'] == 'normal'


def test_stop_after_n_levels_is_visible_saved_and_passed_to_start(make_window, monkeypatch):
    w = make_window()
    assert w.level_limit.isVisible() and w.level_limit.value() == 0  # 0 = no limit.
    inside = w.centralWidget().rect()
    corner = w.level_limit.mapTo(w.centralWidget(), w.level_limit.rect().bottomRight())
    assert inside.contains(corner) and not w.body_scroll.widget().isAncestorOf(w.level_limit)
    w.level_limit.setValue(10)
    assert w.prefs.settings['max_levels'] == 10
    captured = []
    monkeypatch.setattr('src.gui.main_window.make_engine', lambda lib, role, prio, settings, approved, hotkeys: captured.append(settings) or (_ for _ in ()).throw(RuntimeError('stop here')))
    w.hotkeys.native_health = lambda: dict(ready=True)
    with pytest.raises(RuntimeError):
        w.start_automation()
    assert captured[0].max_levels == 10 and captured[0].bloodpoint_checks is False


def test_report_bug_opens_the_issue_form_and_the_log_folder(make_window, monkeypatch, tmp_path):
    from src.gui import main_window
    w = make_window()
    w.root = tmp_path
    opened = []
    monkeypatch.setattr(main_window.QDesktopServices, 'openUrl', lambda url: opened.append(url.toString()))
    w.page_buttons['page_report_bug'].click()
    assert opened[0] == main_window.ISSUES_URL
    assert opened[1].endswith('/debug') and (tmp_path / 'debug').is_dir()
    for code in ('ru', 'en', 'es', 'de', 'fr'):
        w.locale.language = code; w.apply_language()
        assert w.page_buttons['page_report_bug'].text() and w.page_buttons['page_report_bug'].text() != 'page_report_bug'
