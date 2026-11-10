"""Start with DBD: a logon task, a light process watcher and single-instance checks.

Only the process list is read (by executable name); the game process is never
opened, injected into or read. Bloodweb purchases are never started from here.
"""
import ctypes
from ctypes import wintypes as W
import subprocess
import sys
from pathlib import Path

GAME_EXE='deadbydaylight-win64-shipping.exe'
TASK_NAME='AutoBloodWeb Watcher'
APP_MUTEX='Local\\AutoBloodWeb.App'
WATCHER_MUTEX='Local\\AutoBloodWeb.Watcher'
CREATE_NO_WINDOW=0x08000000
DETACHED_PROCESS=0x00000008


class PROCESSENTRY32W(ctypes.Structure):
    _fields_=[('dwSize',W.DWORD),('cntUsage',W.DWORD),('th32ProcessID',W.DWORD),('th32DefaultHeapID',ctypes.c_size_t),
              ('th32ModuleID',W.DWORD),('cntThreads',W.DWORD),('th32ParentProcessID',W.DWORD),('pcPriClassBase',ctypes.c_long),
              ('dwFlags',W.DWORD),('szExeFile',ctypes.c_wchar*260)]


def process_names():
    """Executable names of running processes, from a Toolhelp snapshot."""
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype=W.HANDLE
    snapshot=kernel.CreateToolhelp32Snapshot(0x2,0)
    if snapshot in (None,W.HANDLE(-1).value):return set()
    names=set();entry=PROCESSENTRY32W();entry.dwSize=ctypes.sizeof(entry)
    try:
        ok=kernel.Process32FirstW(snapshot,ctypes.byref(entry))
        while ok:
            names.add(entry.szExeFile.casefold());ok=kernel.Process32NextW(snapshot,ctypes.byref(entry))
    finally:kernel.CloseHandle(snapshot)
    return names


def game_running(names=None):
    return GAME_EXE in (process_names() if names is None else names)


class SingleInstance:
    """A named mutex; `first` is False when another holder already exists."""
    def __init__(self,name):
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateMutexW.restype=W.HANDLE
        self.handle=kernel.CreateMutexW(None,False,name)
        self.first=ctypes.get_last_error()!=183  # ERROR_ALREADY_EXISTS
        self.kernel=kernel

    def release(self):
        if self.handle:self.kernel.CloseHandle(self.handle);self.handle=None


def instance_exists(name):
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenMutexW.restype=W.HANDLE
    handle=kernel.OpenMutexW(0x00100000,False,name)  # SYNCHRONIZE
    if handle:kernel.CloseHandle(handle);return True
    return False


def pythonw(root):
    candidate=Path(root)/'.venv/Scripts/pythonw.exe'
    return candidate if candidate.is_file() else Path(sys.executable).with_name('pythonw.exe')


def frozen():
    return bool(getattr(sys,'frozen',False))


def task_command(root):
    """What the logon task runs. pythonw: no console window; the packaged exe has none either."""
    if frozen():return f'"{sys.executable}" --watcher'
    return f'"{pythonw(root)}" "{Path(root)/"scripts/autostart_watcher.pyw"}"'


def run_schtasks(args,runner=None):
    runner=runner or (lambda a:subprocess.run(['schtasks',*a],capture_output=True,text=True,creationflags=CREATE_NO_WINDOW))
    return runner(args)


def task_installed(runner=None):
    return run_schtasks(['/Query','/TN',TASK_NAME],runner).returncode==0


def install_task(root,runner=None):
    """Runs at logon with highest privileges, since DBD runs elevated: no UAC prompt each time."""
    result=run_schtasks(['/Create','/TN',TASK_NAME,'/TR',task_command(root),'/SC','ONLOGON','/RL','HIGHEST','/F'],runner)
    if result.returncode!=0:raise OSError(result.stderr or result.stdout or 'schtasks_failed')


def uninstall_task(runner=None):
    result=run_schtasks(['/Delete','/TN',TASK_NAME,'/F'],runner)
    if result.returncode!=0 and task_installed(runner):raise OSError(result.stderr or result.stdout or 'schtasks_failed')


def launch_tray_app(root,popen=None):
    popen=popen or subprocess.Popen
    command=[sys.executable,'--tray'] if frozen() else [str(pythonw(root)),'-m','src.app','--tray']
    return popen(command,cwd=str(root),creationflags=DETACHED_PROCESS|CREATE_NO_WINDOW)


def watch_step(root,state,names=None,launch=None,app_running=None):
    """One check: start the tray app when DBD appears and no app is running."""
    running=game_running(names)
    if running and not state.get('game') and not (app_running or (lambda:instance_exists(APP_MUTEX)))():
        (launch or (lambda:launch_tray_app(root)))()
        state['launched']=state.get('launched',0)+1
    state['game']=running
    return state


def run_watcher(root,sleep=None):
    """Logon watcher loop: checks the process list every 3 seconds, never starts purchases."""
    import time
    guard=SingleInstance(WATCHER_MUTEX)
    if not guard.first:return 0
    state={}
    while True:
        try:watch_step(root,state)
        except Exception as exc:
            try:(Path(root)/'debug').mkdir(exist_ok=True);(Path(root)/'debug/watcher.log').open('a',encoding='utf-8').write(f'{time.ctime()} {exc!r}\n')
            except OSError:pass
        (sleep or time.sleep)(3)
