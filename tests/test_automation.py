import copy
import time
import threading
import numpy as np
import pytest
from src.core.automation import Automation,Settings,State
from src.core.screen_manager import CapturedFrame,Rect,WindowInfo,ScreenError
from src.core.models import Analysis,Node,NodeState,NodeType,ScreenState


def web(state=NodeState.AVAILABLE):
    return Analysis(400,400,ScreenState.BLOODWEB,[
        Node(0,150,200,20,NodeState.CENTER,NodeType.CENTER,state_confidence=.95),
        Node(1,230,200,20,state,NodeType.ITEM,'target',state_confidence=.95,geometry_confidence=.95)],[(0,1)],confidence=.95)


class Device:
    def __init__(self,results):
        self.results=results; self.index=0; self.last=0; self.presses=[]; self.releases=0; self.focus=True
        self.window=WindowInfo(1,'DBD',Rect(-400,0,400,400),Rect(-400,0,400,400),144,True)
    def capture(self):
        self.last=min(self.index,len(self.results)-1); self.index+=1
        return CapturedFrame(np.full((400,400,3),self.last,np.uint8),self.window,Rect(0,0,400,400),time.monotonic())
    def analyze(self,image,source): return copy.deepcopy(self.results[self.last])
    def validate_click(self,frame,x,y):
        if not self.focus: raise ScreenError('game_focus_lost')
        return frame.to_screen(x,y)
    def purchase(self,point,duration,stop,check):
        check(); self.presses.append((point,duration))
    def release(self): self.releases+=1


def engine(device,**kwargs):
    settings=Settings(startup_delay=0,poll_interval=.01,purchase_timeout=1,refresh_timeout=2,**kwargs)
    return Automation(device,device,device,['target'],settings,approved=True,hotkeys_ready=True)


def test_dry_run_never_sends_input():
    device=Device([web()]); e=engine(device); e.run()
    assert e.state==State.DRY_RUN and not device.presses


@pytest.mark.parametrize('mode,duration',[('click',.05),('hold',.65)])
def test_calibration_requires_two_fresh_purchase_confirmations(mode,duration):
    device=Device([web(),web(),web(NodeState.PURCHASED),web(NodeState.PURCHASED)])
    e=engine(device,dry_run=False,purchase_mode=mode); e.run(calibrate=True)
    assert e.receipt['verified'] and not e.receipt['user_confirmed']
    assert device.presses==[((-170,200),duration)] and e.actions==1
    assert e.receipt['before']!=e.receipt['after']


def test_entity_absorption_is_not_purchase_and_no_blind_retry():
    device=Device([web(),web(),web(NodeState.ABSORBED)])
    e=engine(device,dry_run=False); e.run(calibrate=True)
    assert e.state==State.PAUSED and e.receipt is None and len(device.presses)==1


def test_full_cycle_gate_and_focus_loss():
    device=Device([web()]); e=engine(device,dry_run=False); e.approved=False; e.run()
    assert not device.presses and e.state==State.PAUSED
    e=engine(device,dry_run=False); device.focus=False; e.run(calibrate=True)
    assert not device.presses and e.state==State.PAUSED


def test_stop_releases_immediately_and_ends_loop():
    device=Device([web()]); e=engine(device,dry_run=False)
    e.halt(); e.run(calibrate=True)
    assert not device.presses and device.releases>=2 and e.state==State.STOPPED


def test_prestige_disabled_and_unknown_never_click():
    special=Analysis(400,400,ScreenState.PRESTIGE,confidence=.9)
    device=Device([special]); e=engine(device,dry_run=False); e.run()
    assert e.state==State.PAUSED and not device.presses


def test_legacy_prestige_without_native_price_never_clicks():
    prestige=Analysis(400,400,ScreenState.PRESTIGE,[Node(0,200,200,20,NodeState.CENTER,NodeType.PRESTIGE)],confidence=.9)
    reward=Analysis(400,400,ScreenState.REWARD,ok_box=(300,300,40,20),confidence=.94)
    device=Device([prestige,prestige,reward,reward,reward,reward,web(),web()])
    e=engine(device,dry_run=False,auto_prestige=True,max_actions=2); e.prestige_approved=True; e.run()
    assert not device.presses and e.actions==0 and e.state==State.PAUSED


def test_unknown_pauses_without_mouse_input():
    device=Device([Analysis(400,400,ScreenState.UNKNOWN)]); e=engine(device,dry_run=False)
    thread=threading.Thread(target=e.run); thread.start()
    time.sleep(.05); e.halt(); thread.join(2)
    assert not thread.is_alive() and not device.presses


def test_refresh_timeout_stops_without_click():
    device=Device([Analysis(400,400,ScreenState.REFRESH)]); e=engine(device,dry_run=False); e.run()
    assert e.state==State.PAUSED and not device.presses


def test_refresh_without_two_red_observations_cannot_confirm_purchase():
    refresh=Analysis(400,400,ScreenState.REFRESH)
    device=Device([web(),web(),refresh,refresh,web(),web()])
    e=engine(device,dry_run=False,max_actions=2); e.run()
    assert len(device.presses)==1 and e.actions==0


def test_calibration_does_not_accept_disappearing_node_as_purchase():
    refresh=Analysis(400,400,ScreenState.REFRESH)
    device=Device([web(),web(),refresh])
    e=engine(device,dry_run=False); e.run(calibrate=True)
    assert e.receipt is None and len(device.presses)==1
