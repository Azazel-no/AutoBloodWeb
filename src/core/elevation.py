"""Administrator rights: the exe always asks (requireAdministrator manifest); this is the fallback."""
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


def is_admin():
    try:return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError,OSError):return True  # Not Windows: nothing to elevate.


def user_language(root):
    """The saved interface language, English before the first save (as the first start)."""
    import json
    try:language=json.loads((Path(root)/'data/preferences.json').read_text(encoding='utf-8')).get('language')
    except (OSError,ValueError):language=None
    return language if language in ('ru','en','es','de','fr') else 'en'


def relaunch_as_admin(root,argv,message_box=None,shell_execute=None):
    """Friendly fallback when started without rights (old shortcut, portable zip run oddly).

    One plain message in the user's language, then the normal UAC prompt for the same
    command line. Returns True when the elevated copy was started; this copy then exits.
    """
    from src.core.localization import Localization
    text=Localization(user_language(root)).text('admin_required')
    box=message_box or (lambda t:ctypes.windll.user32.MessageBoxW(None,t,'AutoBloodWeb',0x1|0x40|0x10000))
    if box(text)!=1:return False  # 1 = OK; Cancel or closed: quietly do nothing.
    args=([] if getattr(sys,'frozen',False) else ['-m','src.app'])+[a for a in argv if a!='--elevated']+['--elevated']
    if shell_execute is None:
        shell=ctypes.WinDLL('shell32',use_last_error=True)
        shell.ShellExecuteW.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_int]
        shell.ShellExecuteW.restype=ctypes.c_void_p
        shell_execute=lambda exe,params,cwd:int(shell.ShellExecuteW(None,'runas',exe,params,cwd,1) or 0)
    return shell_execute(sys.executable,subprocess.list2cmdline(args),str(Path(root)))>32
