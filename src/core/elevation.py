"""Request normal Windows consent only when the game outranks this process."""
import ctypes
import subprocess
import sys
from pathlib import Path


def needs_elevation(backend):
    from src.core.screen_manager import ScreenError
    try:
        window=backend.info();status=backend.access_status(window)
    except ScreenError:return False
    return status.get('reason')=='input_integrity_mismatch'


def request_restart(root,autostart=False,live_session=None,preferences=None):
    args=([] if getattr(sys,'frozen',False) else ['-m','src.app'])+['--elevated']+(['--start'] if autostart else [])
    if live_session:args+=['--live-session',str(live_session)]
    if preferences:args+=['--preferences',str(preferences)]
    shell=ctypes.WinDLL('shell32',use_last_error=True)
    shell.ShellExecuteW.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_int]
    shell.ShellExecuteW.restype=ctypes.c_void_p
    # This is the user's interactive GUI. SW_HIDE can leave its first Qt
    # window/startup modal invisible while still owning the global hotkeys.
    # Normal UAC consent is retained; the game and its token are untouched.
    result=shell.ShellExecuteW(None,'runas',sys.executable,subprocess.list2cmdline(args),str(Path(root)),1)
    return int(result or 0)>32
