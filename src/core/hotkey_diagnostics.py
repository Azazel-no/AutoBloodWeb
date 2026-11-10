"""Read-only Win32 focus metadata for registered shortcut diagnostics."""
import ctypes
import os
from ctypes import wintypes as W
from pathlib import PureWindowsPath


def window_metadata(hwnd):
    if not hwnd: return dict(hwnd=0,pid=0,process='',title='',root=0,root_owner=0)
    u=ctypes.WinDLL('user32',use_last_error=True); k=ctypes.WinDLL('kernel32',use_last_error=True)
    u.GetWindowThreadProcessId.argtypes=[W.HWND,ctypes.POINTER(W.DWORD)]
    u.GetWindowTextW.argtypes=[W.HWND,W.LPWSTR,ctypes.c_int]
    u.GetAncestor.argtypes=[W.HWND,W.UINT]; u.GetAncestor.restype=W.HWND
    pid=W.DWORD(); u.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
    title=ctypes.create_unicode_buffer(512)
    # GetWindowText sends WM_GETTEXT to windows in our own process; a native
    # shortcut thread must never wait for the Qt thread to answer that message.
    if pid.value==os.getpid(): title.value='AutoBloodWeb'
    else: u.GetWindowTextW(hwnd,title,512)
    k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD]; k.OpenProcess.restype=W.HANDLE
    k.CloseHandle.argtypes=[W.HANDLE]
    k.QueryFullProcessImageNameW.argtypes=[W.HANDLE,W.DWORD,W.LPWSTR,ctypes.POINTER(W.DWORD)]
    ctypes.set_last_error(0); handle=k.OpenProcess(0x1000,False,pid.value)
    process=''; error=ctypes.get_last_error()
    if handle:
        try:
            value=ctypes.create_unicode_buffer(32768); length=W.DWORD(len(value))
            if k.QueryFullProcessImageNameW(handle,0,value,ctypes.byref(length)): process=PureWindowsPath(value.value).name
            else: error=ctypes.get_last_error()
        finally: k.CloseHandle(handle)
    return dict(hwnd=int(hwnd),pid=pid.value,process=process,title=title.value,
        root=int(u.GetAncestor(hwnd,2) or 0),root_owner=int(u.GetAncestor(hwnd,3) or 0),process_query_error=error)


def compare_game_focus(foreground,expected):
    if foreground['hwnd']==expected['hwnd'] and expected['hwnd']: return True,'exact_hwnd'
    if (expected.get('pid') and foreground.get('pid')==expected['pid'] and
            expected['hwnd'] in (foreground.get('root'),foreground.get('root_owner'))):
        return True,'game_owned_window'
    return False,'different_process' if foreground.get('pid')!=expected.get('pid') else 'different_window'
