import ctypes
import threading
import time
import pytest
from src.core.input_control import Mouse
from src.core.screen_manager import ScreenError


class InputStub:
    def __init__(self): self.flags=[]; self.positions=[]
    def SetPhysicalCursorPos(self,x,y): self.positions.append((x,y)); return 1
    def GetPhysicalCursorPos(self,pointer):
        pointer._obj.x,pointer._obj.y=self.positions[-1]; return 1
    def SendInput(self,count,pointer,size):
        self.flags.append(pointer._obj.payload.mouse.flags); return 1


def test_native_input_layout_and_negative_position_without_os_input():
    mouse=Mouse(); stub=InputStub(); mouse.u=stub
    assert ctypes.sizeof(mouse.Input)==(40 if ctypes.sizeof(ctypes.c_void_p)==8 else 28)
    mouse.purchase((-1800,400),.05,threading.Event(),lambda:None)
    assert stub.positions==[(-1800,400)] and stub.flags==[2,4] and not mouse.held


def test_emergency_release_during_hold_and_focus_failure():
    mouse=Mouse(); stub=InputStub(); mouse.u=stub; stop=threading.Event()
    thread=threading.Thread(target=lambda:mouse.purchase((1,1),2,stop,lambda:None)); thread.start()
    deadline=time.monotonic()+1
    while not mouse.held and time.monotonic()<deadline: time.sleep(.005)
    stop.set(); mouse.release(); thread.join(1)
    assert not thread.is_alive() and stub.flags==[2,4] and not mouse.held
    count=0
    def lose_focus():
        nonlocal count
        count+=1
        if count>2: raise ScreenError('game_focus_lost')
    with pytest.raises(ScreenError,match='game_focus_lost'):
        mouse.purchase((1,1),.3,threading.Event(),lose_focus)
    assert stub.flags==[2,4,2,4] and not mouse.held


def test_cursor_clamping_prevents_mouse_down():
    mouse=Mouse(); stub=InputStub(); mouse.u=stub
    def clipped(pointer):
        pointer._obj.x,pointer._obj.y=0,0; return 1
    stub.GetPhysicalCursorPos=clipped
    with pytest.raises(ScreenError,match='cursor_position_mismatch'):
        mouse.purchase((-1800,400),.05,threading.Event(),lambda:None)
    assert not stub.flags and not mouse.held


def test_parking_cursor_never_sends_a_button_event_and_honors_stop():
    mouse=Mouse();stub=InputStub();mouse.u=stub;stop=threading.Event()
    mouse.park((-1800,400),stop,lambda:None)
    assert stub.positions==[(-1800,400)] and not stub.flags and not mouse.held
    stop.set()
    with pytest.raises(InterruptedError):mouse.park((1,1),stop,lambda:None)
    assert stub.positions==[(-1800,400)] and not stub.flags
