"""Read-only Windows integrity comparison, never elevation or token changes."""
import ctypes,os
from ctypes import wintypes as W


def process_integrity(pid):
    k=ctypes.WinDLL('kernel32',use_last_error=True); a=ctypes.WinDLL('advapi32',use_last_error=True)
    k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD]; k.OpenProcess.restype=W.HANDLE
    k.CloseHandle.argtypes=[W.HANDLE]
    a.OpenProcessToken.argtypes=[W.HANDLE,W.DWORD,ctypes.POINTER(W.HANDLE)]
    a.GetTokenInformation.argtypes=[W.HANDLE,ctypes.c_int,ctypes.c_void_p,W.DWORD,ctypes.POINTER(W.DWORD)]
    a.GetSidSubAuthorityCount.argtypes=[ctypes.c_void_p]; a.GetSidSubAuthorityCount.restype=ctypes.POINTER(ctypes.c_byte)
    a.GetSidSubAuthority.argtypes=[ctypes.c_void_p,W.DWORD]; a.GetSidSubAuthority.restype=ctypes.POINTER(W.DWORD)
    process=k.OpenProcess(0x1000,False,pid)
    if not process: return dict(pid=pid,error=ctypes.get_last_error())
    handle=W.HANDLE()
    try:
        if not a.OpenProcessToken(process,8,ctypes.byref(handle)): return dict(pid=pid,error=ctypes.get_last_error())
        size=W.DWORD(); a.GetTokenInformation(handle,25,None,0,ctypes.byref(size))
        if not size.value: return dict(pid=pid,error=ctypes.get_last_error())
        buffer=ctypes.create_string_buffer(size.value)
        if not a.GetTokenInformation(handle,25,buffer,size,ctypes.byref(size)): return dict(pid=pid,error=ctypes.get_last_error())
        sid=ctypes.cast(buffer,ctypes.POINTER(ctypes.c_void_p))[0]
        count=a.GetSidSubAuthorityCount(sid)[0]; rid=a.GetSidSubAuthority(sid,count-1)[0]
        return dict(pid=pid,rid=rid,level={0x1000:'Low',0x2000:'Medium',0x3000:'High',0x4000:'System'}.get(rid,str(rid)))
    finally:
        if handle: k.CloseHandle(handle)
        k.CloseHandle(process)


def compare_integrity(app,game):
    if 'rid' not in app or 'rid' not in game: return dict(allowed=False,reason='input_access_unverified',app=app,game=game)
    return dict(allowed=app['rid']>=game['rid'],reason='ok' if app['rid']>=game['rid'] else 'input_integrity_mismatch',app=app,game=game)


def input_access(game_pid):
    return compare_integrity(process_integrity(os.getpid()),process_integrity(game_pid))
