"""Passive signals around DBD's matchmaking (TASKS 2026-11-25, C).

Everything here only reads what Windows already exposes about the game: the size of the log files
the game writes, the game process's TCP/UDP table rows (iphlpapi, like `netstat -ano`), the peak
level of the game's audio session (WASAPI, like the Windows volume mixer) and the game window's
state. The game process is never opened, read, injected or sent input; focus is never changed.

Findings on this machine (2026-11-25): the log is %LOCALAPPDATA%/DeadByDaylight/Saved/Logs/
DeadByDaylight.log (rotated to DeadByDaylight-backup-<date>.log on start); its content is encrypted,
so only its growth (bytes appended per second) can be used, never its text.
"""
import ctypes
import os
import socket
import struct
import threading
import time
from ctypes import wintypes as W
from pathlib import Path

GAME_EXE = 'deadbydaylight-win64-shipping.exe'


def log_folder():
    base = os.environ.get('LOCALAPPDATA')
    return Path(base) / 'DeadByDaylight/Saved/Logs' if base else None


class LogGrowth:
    """Bytes appended to DeadByDaylight.log since the last poll; survives rotation and a game restart.
    Only os.stat is used: the file is never opened, so it is never locked."""

    def __init__(self, folder=None):
        self.folder = Path(folder) if folder else log_folder(); self.size = None; self.identity = None

    def poll(self):
        path = self.folder / 'DeadByDaylight.log' if self.folder else None
        try: stat = path.stat()
        except (OSError, AttributeError):
            self.size = None; return dict(found=False, grew=0, rotated=False)
        identity = (stat.st_ctime_ns, getattr(stat, 'st_ino', 0))
        rotated = self.identity is not None and (identity != self.identity or stat.st_size < (self.size or 0))
        grew = 0 if self.size is None or rotated else max(0, stat.st_size - self.size)
        if rotated: grew = stat.st_size
        self.size, self.identity = stat.st_size, identity
        return dict(found=True, size=stat.st_size, grew=grew, rotated=rotated)


# --- processes ----------------------------------------------------------------------------------
class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [('dwSize', W.DWORD), ('cntUsage', W.DWORD), ('th32ProcessID', W.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                ('th32ModuleID', W.DWORD), ('cntThreads', W.DWORD), ('th32ParentProcessID', W.DWORD), ('pcPriClassBase', ctypes.c_long),
                ('dwFlags', W.DWORD), ('szExeFile', ctypes.c_wchar * 260)]


def game_pid(name=GAME_EXE):
    """Process id of the running game from a Toolhelp snapshot (names only), or None."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True); kernel.CreateToolhelp32Snapshot.restype = W.HANDLE
    snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)
    if snapshot in (None, W.HANDLE(-1).value): return None
    entry = PROCESSENTRY32W(); entry.dwSize = ctypes.sizeof(entry)
    try:
        ok = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.casefold() == name: return int(entry.th32ProcessID)
            ok = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally: kernel.CloseHandle(snapshot)
    return None


# --- network table rows (iphlpapi) ----------------------------------------------------------------
TCP_STATES = {1: 'CLOSED', 2: 'LISTEN', 3: 'SYN_SENT', 4: 'SYN_RCVD', 5: 'ESTABLISHED', 6: 'FIN_WAIT1', 7: 'FIN_WAIT2',
              8: 'CLOSE_WAIT', 9: 'CLOSING', 10: 'LAST_ACK', 11: 'TIME_WAIT', 12: 'DELETE_TCB'}


def _table(function, family, table_class):
    iphlp = ctypes.WinDLL('iphlpapi'); size = W.DWORD(0)
    getter = getattr(iphlp, function)
    getter(None, ctypes.byref(size), False, family, table_class, 0)
    for _ in range(3):
        buffer = ctypes.create_string_buffer(size.value + 4096); size = W.DWORD(len(buffer))
        if getter(buffer, ctypes.byref(size), False, family, table_class, 0) == 0: return buffer.raw[:size.value]
    return b''


def _port(raw):
    return socket.ntohs(raw & 0xFFFF)


def tcp_rows(pid):
    """{(local, remote, state)} of the process's IPv4 TCP rows (TCP_TABLE_OWNER_PID_ALL)."""
    data = _table('GetExtendedTcpTable', socket.AF_INET, 5); rows = set()
    if len(data) < 4: return rows
    count = struct.unpack_from('<I', data)[0]
    for i in range(count):
        state, lip, lport, rip, rport, owner = struct.unpack_from('<IIIIII', data, 4 + i * 24)
        if owner != pid: continue
        rows.add((f'{socket.inet_ntoa(struct.pack("<I", lip))}:{_port(lport)}',
                  f'{socket.inet_ntoa(struct.pack("<I", rip))}:{_port(rport)}', TCP_STATES.get(state, str(state))))
    return rows


def udp_rows(pid):
    """{local} UDP endpoints of the process (UDP_TABLE_OWNER_PID). UDP has no remote address in this table."""
    data = _table('GetExtendedUdpTable', socket.AF_INET, 1); rows = set()
    if len(data) < 4: return rows
    count = struct.unpack_from('<I', data)[0]
    for i in range(count):
        lip, lport, owner = struct.unpack_from('<III', data, 4 + i * 12)
        if owner == pid: rows.add(f'{socket.inet_ntoa(struct.pack("<I", lip))}:{_port(lport)}')
    return rows


# --- audio session peak (WASAPI through raw COM vtables) -------------------------------------------
class GUID(ctypes.Structure):
    _fields_ = [('Data1', W.DWORD), ('Data2', W.WORD), ('Data3', W.WORD), ('Data4', ctypes.c_ubyte * 8)]

    def __init__(self, text):
        super().__init__(); import uuid; u = uuid.UUID(text)
        self.Data1, self.Data2, self.Data3 = u.fields[0], u.fields[1], u.fields[2]
        self.Data4[:] = u.bytes[8:]


CLSID_MMDeviceEnumerator = GUID('BCDE0395-E52F-467C-8E3D-C4579291692E')
IID_IMMDeviceEnumerator = GUID('A95664D2-9614-4F35-A746-DE8DB63617E6')
IID_IAudioSessionManager2 = GUID('77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F')
IID_IAudioSessionControl2 = GUID('BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D')
IID_IAudioMeterInformation = GUID('C02216F6-8C67-4B5B-9D00-D008E73E0064')
SESSION_STATES = {0: 'inactive', 1: 'active', 2: 'expired'}


def _call(obj, index, *args, argtypes=()):
    vtable = ctypes.cast(ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    prototype = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    return prototype(vtable[index])(obj, *args)


def _release(obj):
    if obj: _call(obj, 2)


class AudioMeter:
    """Peak level (0..1) and state of the game's audio sessions on the default render device.
    Build and use it on one thread (COM apartment)."""

    def __init__(self):
        self.ole = ctypes.WinDLL('ole32'); self.ole.CoInitializeEx(None, 0)
        self.enumerator = ctypes.c_void_p()
        hr = self.ole.CoCreateInstance(ctypes.byref(CLSID_MMDeviceEnumerator), None, 0x17, ctypes.byref(IID_IMMDeviceEnumerator), ctypes.byref(self.enumerator))
        if hr != 0: raise OSError(f'MMDeviceEnumerator unavailable (0x{hr & 0xFFFFFFFF:08X})')

    def read(self, pid):
        device = manager = sessions = None; result = dict(found=False, peak=0., state=None, sessions=0)
        try:
            device = ctypes.c_void_p()
            if _call(self.enumerator, 4, 0, 0, ctypes.byref(device), argtypes=(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))) != 0: return result
            manager = ctypes.c_void_p()
            if _call(device, 3, ctypes.byref(IID_IAudioSessionManager2), 0x17, None, ctypes.byref(manager),
                     argtypes=(ctypes.POINTER(GUID), W.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))) != 0: return result
            sessions = ctypes.c_void_p()
            if _call(manager, 5, ctypes.byref(sessions), argtypes=(ctypes.POINTER(ctypes.c_void_p),)) != 0: return result
            count = ctypes.c_int()
            _call(sessions, 3, ctypes.byref(count), argtypes=(ctypes.POINTER(ctypes.c_int),))
            for i in range(count.value):
                control = ctypes.c_void_p(); control2 = ctypes.c_void_p(); meter = ctypes.c_void_p()
                try:
                    if _call(sessions, 4, i, ctypes.byref(control), argtypes=(ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))) != 0: continue
                    if _call(control, 0, ctypes.byref(IID_IAudioSessionControl2), ctypes.byref(control2), argtypes=(ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))) != 0: continue
                    owner = W.DWORD()
                    _call(control2, 14, ctypes.byref(owner), argtypes=(ctypes.POINTER(W.DWORD),))
                    if owner.value != pid: continue
                    state = ctypes.c_int(); _call(control, 3, ctypes.byref(state), argtypes=(ctypes.POINTER(ctypes.c_int),))
                    peak = ctypes.c_float()
                    if _call(control, 0, ctypes.byref(IID_IAudioMeterInformation), ctypes.byref(meter), argtypes=(ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))) == 0:
                        _call(meter, 3, ctypes.byref(peak), argtypes=(ctypes.POINTER(ctypes.c_float),))
                    result.update(found=True, sessions=result['sessions'] + 1, peak=max(result['peak'], round(float(peak.value), 4)),
                                  state=SESSION_STATES.get(state.value, str(state.value)) if result['state'] != 'active' else 'active')
                finally:
                    for obj in (meter, control2, control): _release(obj)
            return result
        finally:
            for obj in (sessions, manager, device): _release(obj)

    def close(self):
        _release(self.enumerator); self.enumerator = None


# --- the game window -----------------------------------------------------------------------------
def window_state(pid):
    """(found, foreground, minimised, title) of the game's top-level window. Read only."""
    user = ctypes.WinDLL('user32'); found = []
    PROC = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

    def visit(hwnd, _):
        owner = W.DWORD(); user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user.IsWindowVisible(hwnd) and not user.GetWindow(hwnd, 4): found.append(hwnd)
        return True
    user.EnumWindows(PROC(visit), 0)
    if not found: return dict(found=False)
    hwnd = found[0]; buffer = ctypes.create_unicode_buffer(256); user.GetWindowTextW(hwnd, buffer, 256)
    return dict(found=True, foreground=user.GetForegroundWindow() == hwnd, minimised=bool(user.IsIconic(hwnd)), title=buffer.value.strip())


def foreground_snapshot():
    """The current foreground window handle and process id: logged to prove we never change it."""
    user = ctypes.WinDLL('user32'); hwnd = user.GetForegroundWindow(); owner = W.DWORD()
    user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    return int(hwnd or 0), int(owner.value)
