"""v1.1.1: release builds expose neither the Sable Detector nor the 'Match found' alert / queue probe.

The code stays in the repository (src.core.features gates it, scripts/build_release.py leaves it out).
"""
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ALERT_SETTINGS = dict(enabled=True, background_only=False, volume=90, push=True, push_target='my-topic', probe_minutes=5)


def build_script():
    import importlib.util
    spec = importlib.util.spec_from_file_location('build_release', ROOT / 'scripts/build_release.py')
    build = importlib.util.module_from_spec(spec); spec.loader.exec_module(build)
    return build


def test_source_runs_keep_the_match_alert(monkeypatch):
    from src.core import features
    monkeypatch.delenv('AUTOBLOODWEB_MATCH_ALERT', raising=False)
    assert features.match_alert()
    monkeypatch.setenv('AUTOBLOODWEB_MATCH_ALERT', '0'); assert not features.match_alert()
    monkeypatch.setenv('AUTOBLOODWEB_MATCH_ALERT', '1'); monkeypatch.setattr(features, 'frozen', lambda: True)
    assert not features.match_alert() and not features.sable_detector()


def test_release_build_leaves_out_the_match_alert_and_sable_modules():
    build = build_script()
    alert = {'src.gui.match_alert_page', 'src.core.match_alert', 'src.core.queue_probe', 'src.core.queue_signals',
             'src.core.alert_strings'}
    assert alert <= set(build.NOT_SHIPPED_MODULES) and alert == set(build.MATCH_ALERT_MODULES)
    assert {'src.gui.sable_overlay', 'src.core.sable_detector', 'src.core.sable_model'} <= set(build.NOT_SHIPPED_MODULES)


@pytest.fixture
def release_window(library, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from src.core import features
    from src.gui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(features, 'frozen', lambda: True)

    class Hotkeys:
        ready = True
        def __init__(self, callback, bindings=None): pass
        def close(self): pass
    monkeypatch.setattr('src.gui.main_window.GlobalHotkeys', Hotkeys)
    monkeypatch.setattr('src.gui.match_overlay.MatchOverlayController.enable', lambda self: None)
    monkeypatch.setattr('src.core.autostart.task_installed', lambda runner=None: False)
    path = tmp_path / 'preferences.json'
    # Saved by a development build with the alert switched on and the developer mode open.
    path.write_text(json.dumps(dict(settings=dict(match_alert=dict(ALERT_SETTINGS), developer_mode=True))), encoding='utf-8')
    w = MainWindow(library, path); w.show(); app.processEvents()
    yield w
    w.close()


def visible_texts(widget):
    from PySide6.QtWidgets import QWidget
    out = []
    for child in widget.findChildren(QWidget):
        text = getattr(child, 'text', None)
        if callable(text):
            try: value = text()
            except TypeError: continue
            if isinstance(value, str): out.append(value)
    return out


def test_release_window_has_no_alert_card_thread_or_marker_key_and_keeps_saved_settings_inert(release_window):
    w = release_window
    assert not hasattr(w, 'match_alert')
    assert not {'MatchAlert', 'QueueProbeMarker', 'MatchAlertPush'} & {t.name for t in threading.enumerate()}
    from src.core.alert_strings import TEXT
    shown = '\n'.join(visible_texts(w))
    for values in TEXT.values():
        for value in values:
            if len(value) > 12: assert value.split('{')[0] not in shown, value
    assert 'SHIFT+M' not in shown.upper()
    assert w.prefs.settings['match_alert'] == ALERT_SETTINGS          # left as saved, never read
    w.set_developer_mode(True); w.set_developer_mode(False)            # no card to show or hide
    assert not hasattr(w, 'match_alert')


def test_frozen_app_starts_without_the_alert_modules_and_shows_no_alert_strings(tmp_path):
    """In the built EXE the modules are absent: importing the app must not need them."""
    prefs = tmp_path / 'preferences.json'
    prefs.write_text(json.dumps(dict(settings=dict(match_alert=dict(ALERT_SETTINGS)))), encoding='utf-8')
    from src.core.alert_strings import TEXT
    alert_keys = set(TEXT)
    code = f'''
import sys
sys.frozen = True
for name in {sorted(build_script().NOT_SHIPPED_MODULES)!r}:
    sys.modules[name] = None          # ImportError if anything tries to load them
from pathlib import Path
from src.core import localization
from src.core.preferences import Preferences
import src.gui.main_window
assert not set({sorted(alert_keys)!r}) & {{k for lang in localization.STRINGS.values() for k in lang}}, 'alert strings'
p = Preferences(Path({str(prefs)!r}), [])
assert p.settings['match_alert']['enabled'] is True
print('ok')
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True, timeout=120,
                            env={**__import__('os').environ, 'QT_QPA_PLATFORM': 'offscreen'})
    assert result.returncode == 0 and result.stdout.strip().endswith('ok'), result.stderr[-2000:]
