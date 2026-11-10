"""Input delivery regressions; fake devices and mathematical arrays, no game IO."""
import copy,threading,time
from dataclasses import replace
import cv2
import numpy as np
import pytest
from src.core.input_control import Mouse
from src.core.screen_manager import ScreenError
from src.core.models import NodeState
from tests.test_input_control import InputStub
from tests.test_automation import Device,engine,web
from tests.test_v46 import Clock


def test_pointer_settles_100ms_before_one_down_and_is_rechecked():
    mouse=Mouse();stub=InputStub();mouse.u=stub;calls=[]
    class Stop:
        def is_set(self):return False
        def wait(self,seconds):
            calls.append((seconds,list(stub.flags)))
            return seconds!=.1
    mouse.purchase((100,200),.05,Stop(),lambda:None)
    assert calls[0]==(.1,[])
    assert stub.flags==[2,4] and not mouse.held


@pytest.mark.parametrize('cause',['transient','permanent','focus','stop'])
def test_hover_park_retries_once_after_game_restores_pointer_without_buttons(cause):
    mouse=Mouse();stub=InputStub();mouse.u=stub;reads=[];waits=[]
    original=stub.GetPhysicalCursorPos
    def position(pointer):
        reads.append(True)
        if len(reads)==1 or cause=='permanent':pointer._obj.x,pointer._obj.y=(500,390);return 1
        return original(pointer)
    stub.GetPhysicalCursorPos=position
    class Stop:
        def is_set(self):return False
        def wait(self,seconds):waits.append(seconds);return cause=='stop'
    def check():
        if waits and cause=='focus':raise ScreenError('game_focus_lost')
    if cause=='transient':mouse.park((8,540),Stop(),check)
    else:
        with pytest.raises(InterruptedError if cause=='stop' else ScreenError):mouse.park((8,540),Stop(),check)
    assert not stub.flags and not mouse.held and len(stub.positions)<=2
    assert len(stub.positions)==(2 if cause in ('transient','permanent') else 1)


@pytest.mark.parametrize('cause',['stop','drift','focus'])
def test_change_during_settle_never_sends_down(cause):
    mouse=Mouse();stub=InputStub();mouse.u=stub;settled=False
    class Stop:
        def is_set(self):return False
        def wait(self,seconds):
            nonlocal settled
            assert seconds==.1
            settled=True
            if cause=='drift':stub.positions[-1]=(999,999)
            return cause=='stop'
    def guard():
        if settled and cause=='focus':raise ScreenError('game_focus_lost')
    if cause=='stop':mouse.purchase((100,200),.05,Stop(),guard)
    else:
        with pytest.raises(ScreenError,match='cursor_position_mismatch' if cause=='drift' else 'game_focus_lost'):
            mouse.purchase((100,200),.05,Stop(),guard)
    assert not stub.flags and not mouse.held


def test_post_probe_discards_stale_capture_and_reads_new_pixels(monkeypatch):
    clock=Clock();monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    d=Device([web()]);runner=engine(d,dry_run=False);runner.wait=clock.advance
    original=d.capture;captures=[];resets=[]
    def capture():
        frame=original();captures.append(frame.captured_at)
        if len(captures)==1:clock.advance(3.31)
        return frame
    d.capture=capture;d.reset_region=lambda:resets.append(True)
    frame,result,_=runner.recover_read(clock.now+10,retry_stale=True)
    assert len(captures)==2 and frame.captured_at==captures[1]>captures[0]
    assert not resets and not d.presses
    assert any(e['stage']=='stale_frame_discarded' for e in runner.report.entries)


def test_repeated_stale_capture_is_bounded_and_sends_no_input(monkeypatch):
    clock=Clock();monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    d=Device([web()]);runner=engine(d,dry_run=False);runner.wait=clock.advance
    original=d.capture
    def capture():
        frame=original();clock.advance(3.31);return frame
    d.capture=capture
    with pytest.raises(ScreenError,match='stale_frame'):
        runner.recover_read(clock.now+15,retry_stale=True)
    assert d.index==3 and not d.presses




@pytest.mark.parametrize('point',[(20,30),(250,145)])
def test_center_search_crop_keeps_same_allowed_template_origins(monkeypatch,point):
    import src.core.bloodweb_detector as detector
    # These are arbitrary numerical arrays, not generated game artwork.
    rng=np.random.default_rng(11);gray=rng.integers(0,255,(200,400),dtype=np.uint8)
    tile=rng.integers(0,255,(49,49),dtype=np.uint8);x,y=point
    gray[y:y+49,x:x+49]=tile
    monkeypatch.setattr(detector,'_center_templates',lambda root:((tile,40,'test'),))
    full=cv2.matchTemplate(gray,tile,cv2.TM_CCOEFF_NORMED)
    full[:28]=0;full[182:]=0;full[:,256:]=0
    expected=cv2.minMaxLoc(full)[3]
    assert expected==point
    assert detector.find_center(cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR))[:2]==(x+24,y+24)


@pytest.mark.parametrize('cap,expected',[(None,1),(2,1),(1,1)])
def test_legacy_repeat_request_sends_only_one_command_before_verification(monkeypatch,cap,expected):
    clock=Clock();monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    d=Device([web()]);runner=engine(d,dry_run=False,purchase_mode='repeat',max_input_commands=cap)
    runner.wait=clock.advance;frame=d.capture();times=[];original=d.purchase
    def purchase(*args):times.append(clock.now);original(*args)
    d.purchase=purchase
    runner.purchase_input((-170,200),.05,lambda:None,frame,230,200,allow_repeats=True)
    assert runner.attempts==len(d.presses)==expected
    assert len(times)==1
    assert all(p==(-170,200) and duration==.05 for p,duration in d.presses)
    assert runner.actions==0 and not getattr(d,'held',False)


@pytest.mark.parametrize('cause',['stop','focus','stale'])
def test_repeat_stops_before_next_down_on_stop_or_invalid_scene(monkeypatch,cause):
    clock=Clock();monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    d=Device([web()]);runner=engine(d,dry_run=False,purchase_mode='repeat');runner.wait=clock.advance
    frame=d.capture();original=d.purchase
    def purchase(*args):
        original(*args)
        if cause=='stop':runner.halt()
        elif cause=='focus':d.focus=False
    d.purchase=purchase
    original_validate=d.validate_click
    def validate(*args):
        if d.presses and cause=='stale':raise ScreenError('stale_frame')
        return original_validate(*args)
    d.validate_click=validate
    if cause=='stop':
        with pytest.raises(InterruptedError):runner.purchase_input((-170,200),.05,lambda:None,frame,230,200,allow_repeats=True)
    else:runner.purchase_input((-170,200),.05,lambda:None,frame,230,200,allow_repeats=True)
    assert len(d.presses)==runner.attempts==1 and d.releases


def test_repeat_setting_does_not_repeat_unpaid_hover_or_modal_clicks():
    d=Device([web()]);runner=engine(d,dry_run=False,purchase_mode='repeat');frame=d.capture()
    runner.purchase_input((-170,200),.05,lambda:None,frame,230,200)
    assert len(d.presses)==runner.attempts==1


def test_repeat_preference_requires_its_own_calibration(tmp_path):
    import json
    from src.core.preferences import Preferences
    p=tmp_path/'preferences.json'
    p.write_text(json.dumps(dict(settings=dict(purchase_mode='repeat',hold_seconds=.25),
        calibration=dict(mode='hold',hold_seconds=.25,verified=True,user_confirmed=True))),encoding='utf-8')
    prefs=Preferences(p,[])
    assert prefs.settings['purchase_mode']=='repeat'
    assert prefs.saved_calibration() is None


def test_repeat_buy_still_needs_two_red_observations():
    d=Device([web(),web(),web(NodeState.PURCHASED),web(NodeState.PURCHASED)])
    runner=engine(d,dry_run=False,purchase_mode='repeat');runner.run(calibrate=True)
    assert len(d.presses)==runner.attempts==1 and runner.actions==1
    assert runner.purchase_evidence.red_count==2 and runner.receipt['mode']=='repeat'


@pytest.mark.parametrize('mode,count',[('hold',1),('repeat',1)])
def test_slow_after_capture_is_discarded_without_repeating_purchase(monkeypatch,mode,count):
    clock=Clock();monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    d=Device([web(),web(),web(NodeState.PURCHASED),web(NodeState.PURCHASED),web(NodeState.PURCHASED)])
    runner=engine(d,dry_run=False,purchase_mode=mode);runner.settings.purchase_timeout=8;runner.wait=clock.advance
    original=d.capture;stale=[]
    def capture():
        frame=original()
        if d.presses and not stale:stale.append(frame.captured_at);clock.advance(2.0941)
        return frame
    d.capture=capture;runner.run(calibrate=True)
    assert runner.receipt and runner.receipt['verified'] and runner.actions==1
    assert len(d.presses)==runner.attempts==count and runner.purchase_evidence.red_count==2
    assert d.index==5 and not runner.pending_action
    assert sum(e['stage']=='stale_frame_discarded' for e in runner.report.entries)==1


@pytest.mark.parametrize('failure',['forever_stale','stop','deadline'])
def test_failed_after_capture_preserves_pending_and_never_clicks_again(monkeypatch,failure):
    clock=Clock();monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    d=Device([web()]);runner=engine(d,dry_run=False,purchase_mode='repeat');runner.wait=clock.advance
    runner.settings.purchase_timeout=2 if failure=='deadline' else 8
    original=d.capture
    def capture():
        frame=original()
        if d.presses:
            clock.advance(2.0941)
            if failure=='stop':runner.halt()
        return frame
    d.capture=capture;runner.run(calibrate=True)
    assert len(d.presses)==runner.attempts==1 and runner.actions==0 and runner.receipt is None
    assert runner.pending_action and runner.scene_record['pending'] and d.releases
    assert d.index== (5 if failure=='forever_stale' else 3)


def test_legacy_repeat_request_cannot_send_three_native_commands(monkeypatch):
    clock=Clock();monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    d=Device([web()]);runner=engine(d,dry_run=False,purchase_mode='repeat');runner.wait=clock.advance
    class Stop:
        def is_set(self):return False
        def wait(self,seconds):clock.advance(seconds);return False
    runner.stop=Stop();mouse=Mouse();stub=InputStub();mouse.u=stub;runner.mouse=mouse
    down=[];original=stub.SendInput
    def send(*args):
        result=original(*args)
        if stub.flags[-1]==2:down.append(clock.now)
        return result
    stub.SendInput=send;frame=d.capture()
    runner.purchase_input((-170,200),.05,lambda:None,frame,230,200,allow_repeats=True)
    assert stub.flags==[2,4] and len(down)==runner.attempts==1
    assert not mouse.held
