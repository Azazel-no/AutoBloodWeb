"""Start with DBD: task commands, watcher, single instance and tray mode. No real task is created."""
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core import autostart
from src.core.autostart import (GAME_EXE, TASK_NAME, SingleInstance, install_task, instance_exists, task_command,
                                task_installed, uninstall_task, watch_step)


class Runner:
    def __init__(self, codes=None):
        self.calls = []
        self.codes = codes or {}
    def __call__(self, args):
        self.calls.append(args)
        return SimpleNamespace(returncode=self.codes.get(args[0], 0), stdout='', stderr='')


def test_task_runs_the_watcher_at_logon_with_highest_privileges(root):
    runner = Runner()
    install_task(root, runner)
    args = runner.calls[0]
    assert args[:3] == ['/Create', '/TN', TASK_NAME]
    assert args[args.index('/SC') + 1] == 'ONLOGON' and args[args.index('/RL') + 1] == 'HIGHEST' and '/F' in args
    command = args[args.index('/TR') + 1]
    assert 'pythonw' in command.lower() and 'autostart_watcher.pyw' in command and command.count('"') == 4


def test_task_removal_and_query(root):
    runner = Runner()
    uninstall_task(runner)
    assert runner.calls[0][:3] == ['/Delete', '/TN', TASK_NAME]
    assert task_installed(Runner()) and not task_installed(Runner({'/Query': 1}))


def test_failed_install_is_reported(root):
    with pytest.raises(OSError):
        install_task(root, Runner({'/Create': 1}))


def test_watcher_launches_once_when_the_game_appears_and_only_if_no_app_runs(root):
    launches = []
    state = {}
    watch_step(root, state, names={'explorer.exe'}, launch=lambda: launches.append(1), app_running=lambda: False)
    assert launches == []
    watch_step(root, state, names={GAME_EXE}, launch=lambda: launches.append(1), app_running=lambda: False)
    watch_step(root, state, names={GAME_EXE}, launch=lambda: launches.append(1), app_running=lambda: False)
    assert launches == [1]
    watch_step(root, state, names=set(), launch=lambda: launches.append(1), app_running=lambda: False)
    watch_step(root, state, names={GAME_EXE}, launch=lambda: launches.append(1), app_running=lambda: True)
    assert launches == [1]


def test_tray_app_is_launched_without_purchases_or_console(root):
    calls = []
    autostart.launch_tray_app(root, popen=lambda args, **kw: calls.append((args, kw)))
    args, kw = calls[0]
    assert args[1:] == ['-m', 'src.app', '--tray'] and 'pythonw' in args[0].lower() and kw['cwd'] == str(root)


def test_only_one_instance_holds_the_mutex():
    name = 'Local\\AutoBloodWeb.Test.' + uuid.uuid4().hex
    first = SingleInstance(name)
    second = SingleInstance(name)
    assert first.first and not second.first and instance_exists(name)
    second.release(); first.release()
    assert not instance_exists(name)


def test_game_process_is_found_by_name_only():
    assert autostart.game_running({GAME_EXE}) and not autostart.game_running({'notdbd.exe'})


def test_watcher_code_never_touches_the_game_process():
    text = Path('src/core/autostart.py').read_text(encoding='utf-8') + Path('scripts/autostart_watcher.pyw').read_text(encoding='utf-8')
    assert not [w for w in ('OpenProcess', 'ReadProcessMemory', 'WriteProcessMemory', 'start_automation', 'SendInput') if w in text]


@pytest.fixture
def window(library, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from src.gui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    class Hotkeys:
        ready = True
        def __init__(self, callback, bindings=None): pass
        def close(self): pass
    monkeypatch.setattr('src.gui.main_window.GlobalHotkeys', Hotkeys)
    monkeypatch.setattr('src.gui.match_overlay.MatchOverlayController.enable', lambda self: None)
    started = []
    monkeypatch.setattr('src.gui.main_window.MainWindow.start_automation', lambda self, *a, **k: started.append(1))
    w = MainWindow(library, tmp_path / 'preferences.json')
    w.started = started
    yield w
    w.close()


def test_tray_mode_hides_the_window_turns_the_overlay_on_and_buys_nothing(window):
    window.enter_tray_mode()
    assert not window.isVisible() and window.tray.isVisible() and window.match_overlay.isChecked()
    assert window.started == [] and window.auto_worker is None
    labels = [a.text() for a in window.tray_menu.actions()]
    assert len(labels) == 5


def test_tray_app_exits_when_the_game_closes(window):
    closed = []
    window.close = lambda: closed.append(1)
    window.exit_when_game_closes(running=True)
    assert closed == []
    window.exit_when_game_closes(running=False)
    assert closed == [1]


def test_packaged_exe_runs_its_own_watcher_and_tray(monkeypatch, tmp_path):
    exe = str(tmp_path / 'AutoBloodWeb.exe')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', exe)
    assert task_command(tmp_path) == f'"{exe}" --watcher'
    calls = []
    autostart.launch_tray_app(tmp_path, popen=lambda args, **kw: calls.append((args, kw)))
    assert calls[0][0] == [exe, '--tray'] and calls[0][1]['cwd'] == str(tmp_path)
