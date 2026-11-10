import ctypes
from ctypes import wintypes as W
import threading
from src.core.input_control import GlobalHotkeys


def test_native_global_registration_and_stop_message_delivery():
    received=[]; delivered=threading.Event()
    def callback(action): received.append(action); delivered.set()
    # A real user instance may hold F8/F9 during this suite. This test verifies
    # registration and queue delivery, not physical F9 in the game.
    hotkeys=GlobalHotkeys(callback,{'toggle':'Ctrl+Alt+Shift+F18','stop':'Ctrl+Alt+Shift+F19'})
    try:
        assert hotkeys.ready,hotkeys.error
        # Deliver a shortcut notification to our message loop, without sending
        # keyboard input to DBD or any other app.
        u=ctypes.WinDLL('user32',use_last_error=True)
        u.PostThreadMessageW.argtypes=[W.DWORD,W.UINT,W.WPARAM,W.LPARAM]
        assert u.PostThreadMessageW(hotkeys.thread.native_id,0x312,1,0)
        assert delivered.wait(1) and received==['stop']
    finally: hotkeys.close()
