"""Hotfix 1.0.1 A: always administrator, plain wording, no technical elevation errors."""
import re
from pathlib import Path
from src.core import autostart
from src.core.elevation import relaunch_as_admin
from src.core.localization import STRINGS, Localization

ROOT = Path(__file__).resolve().parents[1]
LANGS = ('ru', 'en', 'es', 'de', 'fr')
TECHNICAL = re.compile(r'integrit|mismatch|elevat|целостн|повышенн|уровень прав|уровни прав|Integrität|intégrité|integridad', re.I)


def test_no_user_facing_text_mentions_integrity_or_elevation():
    keys = ('admin_required', 'input_integrity_mismatch', 'input_access_unverified', 'elevation_cancelled', 'wizard_check_access', 'notice_needs')
    for language in LANGS:
        for key in keys:
            text = Localization(language).text(key)
            assert not TECHNICAL.search(text), (language, key, text)
    for language in LANGS:
        for key, text in STRINGS[language].items():
            if isinstance(text, str):
                assert not re.search(r'integrity|elevation|elevated', text, re.I), (language, key)


def test_admin_message_exists_in_all_five_languages():
    texts = {language: Localization(language).text('admin_required') for language in LANGS}
    assert texts['en'] == 'AutoBloodWeb needs administrator rights to work with Dead by Daylight. Click OK to restart it as administrator.'
    assert len(set(texts.values())) == 5 and all('AutoBloodWeb' in t and 'Dead by Daylight' in t for t in texts.values())


def test_relaunch_asks_once_and_restarts_the_same_command_elevated(tmp_path):
    calls = []
    assert relaunch_as_admin(tmp_path, ['--tray'], message_box=lambda t: 1, shell_execute=lambda *a: calls.append(a) or 42)
    assert calls and '--tray' in calls[0][1] and '--elevated' in calls[0][1]
    calls.clear()
    assert not relaunch_as_admin(tmp_path, [], message_box=lambda t: 2, shell_execute=lambda *a: calls.append(a) or 42)
    assert not calls  # Cancel: no UAC prompt, nothing else shown


class FakeRegistry:
    HKEY_CURRENT_USER = 1; KEY_SET_VALUE = 2

    def __init__(self, values): self.values = dict(values)
    def OpenKey(self, *a): return self
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def EnumValue(self, key, index):
        items = list(self.values.items())
        if index >= len(items): raise OSError
        return items[index][0], items[index][1], 1
    def DeleteValue(self, key, name): del self.values[name]


class Done:
    def __init__(self, code): self.returncode = code; self.stdout = self.stderr = ''


def test_old_run_key_moves_to_highest_privilege_logon_task(tmp_path):
    registry = FakeRegistry({'AutoBloodWeb': r'"C:\x\AutoBloodWeb.exe" --watcher', 'Other': 'other.exe'})
    seen = []
    def runner(args):
        seen.append(args)
        return Done(1 if args[0] == '/Query' else 0)
    assert autostart.migrate_run_key(tmp_path, registry, runner)
    assert registry.values == {'Other': 'other.exe'}
    create = next(a for a in seen if a[0] == '/Create')
    assert create[create.index('/RL') + 1] == 'HIGHEST' and create[create.index('/SC') + 1] == 'ONLOGON'
    assert not autostart.migrate_run_key(tmp_path, FakeRegistry({'Other': 'x'}), runner)


def test_installer_launch_and_shortcuts_go_through_uac():
    iss = (ROOT / 'installer/AutoBloodWeb.iss').read_text(encoding='utf-8')
    run = next(line for line in iss.splitlines() if line.startswith('Filename: "{app}\\AutoBloodWeb.exe"'))
    assert 'postinstall' in run and 'shellexec' in run and 'runasoriginaluser' not in run
    assert 'PrivilegesRequired=admin' in iss and 'schtasks.exe' in iss
    build = (ROOT / 'scripts/build_release.py').read_text(encoding='utf-8')
    assert "'--uac-admin'" in build
