"""F1: the Memes tab, its shared volume / mute controls, the mute hotkey and the meme pack folder."""
import json
import re

import pytest
from PySide6.QtWidgets import QApplication

from src.core import meme_pack
from src.core.hotkey_config import extra_keys_conflict
from src.core.localization import STRINGS
from src.core.meme_strings import TEXT
from src.core.memes import DEFAULT_MUTE_KEY, clean_settings, combo_down, default_settings, effective_volume, valid_mute_key
from src.gui.main_window import MainWindow
from src.gui.memes_page import ComboWatcher, MemeHub


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, library, monkeypatch, tmp_path):
    original = MainWindow.__init__
    monkeypatch.setattr(MainWindow, '__init__', lambda self, library: original(self, library, tmp_path / 'preferences.json'))
    class Hotkeys:
        ready = True
        def __init__(self, callback, bindings=None): pass
        def close(self): pass
    monkeypatch.setattr('src.gui.main_window.GlobalHotkeys', Hotkeys)
    w = MainWindow(library)
    yield w
    w.close()


def test_settings_defaults_and_cleaning():
    d = default_settings()
    assert d['volume'] == 60 and d['muted'] is False and d['chase']['enabled'] is False and d['sable']['enabled'] is False
    dirty = clean_settings(dict(volume=400, muted='yes', mute_key='???', section='x', chase=dict(enabled=1, size=9999), sable='no'))
    assert dirty == d
    kept = clean_settings(dict(volume=15, muted=True, mute_key='ctrl + shift+f4', section='sable', chase=dict(enabled=True, size=150)))
    assert (kept['volume'], kept['muted'], kept['mute_key'], kept['section']) == (15, True, 'CTRL+SHIFT+F4', 'sable')
    assert kept['chase']['enabled'] is True and kept['chase']['size'] == 150


def test_volume_and_mute():
    s = default_settings()
    assert effective_volume(s) == pytest.approx(.6) and effective_volume(s, .5) == pytest.approx(.3)
    s['muted'] = True
    assert effective_volume(s) == 0.


def test_mute_combo_needs_exactly_its_modifiers():
    held = set(); read = lambda code: code in held
    assert valid_mute_key(' ctrl+m ') == 'CTRL+M' and valid_mute_key('ctrl+') is None and valid_mute_key('MOUSE4') is None
    held.update({ord('M')}); assert not combo_down('CTRL+M', read)            # A bare M is not the combination.
    held.add(0x11); assert combo_down('CTRL+M', read)
    held.add(0x10); assert not combo_down('CTRL+M', read)                     # Ctrl+Shift+M is another one.
    assert not extra_keys_conflict({'toggle': 'F8', 'stop': 'F9'}, 'F10', 'TAB', ['F1', DEFAULT_MUTE_KEY])
    assert extra_keys_conflict({'toggle': 'F8', 'stop': 'F9'}, 'F10', 'TAB', ['F8', DEFAULT_MUTE_KEY])


def test_watcher_fires_once_per_press():
    held = set(); calls = []
    w = ComboWatcher('CTRL+M', lambda: calls.append(1), read=lambda code: code in held)
    w.step(); held.update({0x11, ord('M')}); w.step(); w.step(); held.clear(); w.step(); held.update({0x11, ord('M')}); w.step()
    assert len(calls) == 2


def test_hub_mutes_by_hotkey_and_watches_only_while_a_feature_is_on(app, tmp_path):
    settings = default_settings(); saved = []; changes = []
    hub = MemeHub(settings, lambda: saved.append(1), tmp_path / 'pack', key_reader=lambda code: False)
    hub.volume_changed.connect(lambda: changes.append(hub.volume()))
    assert hub.watcher is None
    hub.use('chase', True); assert hub.watcher is not None
    hub.use('sable', True); hub.use('chase', False); assert hub.watcher is not None
    hub.use('sable', False); assert hub.watcher is None
    hub.toggle_mute(); assert settings['muted'] is True and hub.volume() == 0.
    hub.toggle_mute(); hub.set_volume(30)
    assert changes == [0., pytest.approx(.6), pytest.approx(.3)] and saved
    hub.close()


def test_pack_folder_is_outside_the_repo_and_documented(tmp_path, monkeypatch):
    monkeypatch.delenv(meme_pack.ENV, raising=False); monkeypatch.setenv('APPDATA', str(tmp_path / 'Roaming'))
    folder = meme_pack.pack_dir()
    assert folder == tmp_path / 'Roaming' / 'AutoBloodWeb' / 'meme_pack'
    meme_pack.ensure(folder)
    assert 'voice/' in (folder / 'README.txt').read_text(encoding='utf-8') and (folder / 'sable/photos').is_dir()


def test_empty_and_filled_pack(tmp_path):
    assert meme_pack.load(tmp_path / 'missing').empty
    folder = meme_pack.ensure(tmp_path / 'pack'); pack = meme_pack.load(folder)
    assert pack.empty and not pack.chase_ready and not pack.sable_ready
    for name in ('voice/whatcanisay3.mp3', 'voice/mambaout2.mp3', 'voice/dontpassit1.mp3', 'voice/plain.mp3', 'voice_original/whatcanisay3.mp3',
                 'music/chase.mp3', 'dance/dance-1/0001.webp', 'dance/dance-1/0002.webp', 'dance/dance-1.mp3', 'dance/dance-2/0001.png', 'dance/empty/notes.txt', 'sable/photos/a.png', 'sable/siren.mp3'):
        (folder / name).parent.mkdir(parents=True, exist_ok=True); (folder / name).write_bytes(b'x')
    (folder / 'dance/dance-1/info.json').write_text('{"fps": 25}', encoding='utf-8')
    pack = meme_pack.load(folder)
    assert {v.name: v.weight for v in pack.voices} == {'whatcanisay3': 3, 'mambaout2': 2, 'dontpassit1': 1, 'plain': 1}
    assert pack.music.name == 'chase.mp3' and len(pack.photos) == 1 and pack.siren.name == 'siren.mp3'
    assert [(d.name, len(d.frames), d.fps, bool(d.audio)) for d in pack.dances] == [('dance-1', 2, 25., True), ('dance-2', 1, 30., False)]
    assert pack.dances[0].duration == pytest.approx(.08)
    assert pack.chase_ready and pack.sable_ready and not pack.empty
    (folder / 'pack.json').write_text(json.dumps(dict(voices=[dict(file='voice/plain.mp3', weight=2, duration=1.5), dict(file='../outside.mp3', weight=3)],
                                                      music=dict(file='music/chase.mp3', duration=180.))), encoding='utf-8')
    pack = meme_pack.load(folder)
    assert next(v for v in pack.voices if v.name == 'plain').weight == 2 and pack.music_duration == 180.
    (folder / 'pack.json').write_text('{broken', encoding='utf-8')
    assert len(meme_pack.load(folder).voices) == 4
    assert meme_pack.weight_of('chuckles3-1') == 3 and meme_pack.weight_of('teamate2') == 2 and meme_pack.weight_of('nodigit') == 1


def test_meme_strings_exist_in_all_five_languages():
    fields = lambda text: set(re.findall(r'\{(\w+)', text))
    for key, values in TEXT.items():
        assert len(values) == 5 and all(values), key
        for code in ('ru', 'en', 'es', 'de', 'fr'):
            assert STRINGS[code][key] and fields(STRINGS[code][key]) == fields(STRINGS['en'][key]), (code, key)


def test_memes_tab_holds_the_randomizer_and_the_shared_controls(window):
    w = window
    assert 'memes' in w.nav_buttons and 'randomizer' not in w.nav_buttons and w.nav_buttons['memes'].toolTip() == w.locale.text('nav_memes')
    assert w.prefs.settings['memes']['chase']['enabled'] is False and w.prefs.settings['memes']['sable']['enabled'] is False
    w.open_page(2); page = w.memes_page
    assert page is not None and w.pages.currentIndex() == 2 and w.action_panel.isHidden()
    assert page.stack.currentWidget() is w.randomizer_page and page.stack.indexOf(page.randomizer) == 0   # The Randomizer is the first section.
    assert page.volume.value() == 60 and not page.mute.isChecked() and 'CTRL+M' in page.mute.text()
    assert w.meme_hub.folder == w.preferences_path.parent / 'meme_pack' and (w.meme_hub.folder / 'README.txt').is_file()
    page.volume.setValue(35); assert w.prefs.settings['memes']['volume'] == 35 and w.meme_hub.volume() == pytest.approx(.35)
    w.meme_hub.toggle_mute(); assert page.mute.isChecked() and w.meme_hub.volume() == 0.
    page.mute.setChecked(False); assert w.prefs.settings['memes']['muted'] is False
    page.open_section(1); assert page.stack.currentWidget() is page.chase and w.prefs.settings['memes']['section'] == 'chase'
    for section in (page.chase, page.sable):   # Off by default, the ban-risk warning on top, an empty pack is a message.
        assert not section.enabled.isChecked() and not section.pack_note.isHidden() and section.pack_note.text() == w.locale.text('meme_pack_empty')
    page.open_section(0); w.randomizer_page.roll(); assert w.randomizer_page.result   # The Randomizer still rolls inside the tab.
    w.randomizer_page.skip()
