"""Transition sequence and safety on injected clocks/devices, no DBD input."""
import copy,time
from dataclasses import replace
import pytest
from tests.test_automation import Device,web,engine
from tests.test_v46 import Clock
from src.core.models import Analysis,ScreenState,NodeState
from src.core.automation import State
from src.core.screen_manager import ScreenError

@pytest.mark.parametrize('gap',[1,3,5,10])
def test_completed_level_waits_and_continues_without_start(monkeypatch,gap):
    clock=Clock();monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    class Sequence(Device):
        def __init__(self):super().__init__([web()]);self.level=0;self.done_at=None
        def capture(self):
            clock.advance(.2)
            if self.done_at and self.level==0 and clock.now-self.done_at>.45:
                if clock.now-self.done_at<gap+.45:raise ScreenError('bloodweb_not_found')
                self.level=1
            return super().capture()
        def analyze(self,*a):
            r=web(NodeState.PURCHASED if self.done_at and self.level==0 else NodeState.AVAILABLE)
            if self.level:r.nodes[1].x+=60;r.nodes[1].icon_id='new_target'
            return r
        def purchase(self,*args):
            super().purchase(*args)
            if self.level==0:self.done_at=clock.now
            else:runner.halt()
    d=Sequence();runner=engine(d,dry_run=False,run_mode='continuous',max_actions=None,max_runtime=None)
    runner.settings.refresh_timeout=30;runner.wait=clock.advance;runner.run()
    assert len(d.presses)==2 and runner.pending_action  # Stop during next input remains unresolved.
    assert any(e['stage']=='new_level_confirmed' for e in runner.report.entries)
    assert len([e for e in runner.report.entries if e['stage']=='run_settings'])==1

@pytest.mark.parametrize('reason',['game_focus_lost','bloodweb_occluded','input_integrity_mismatch'])
def test_loading_does_not_hide_critical_failure(monkeypatch,reason):
    d=Device([web()]);r=engine(d,dry_run=False);f=d.capture()
    r.read=lambda:(_ for _ in ()).throw(ScreenError(reason));r.wait=lambda t:None
    with pytest.raises(ScreenError,match=reason):r.wait_next_bloodweb(f,web(NodeState.PURCHASED))
    assert not d.presses and d.releases

def test_loading_timeout_and_stop_are_bounded(monkeypatch):
    clock=Clock();monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    d=Device([web()]);r=engine(d,dry_run=False);f=d.capture();r.wait=clock.advance
    r.read=lambda:(_ for _ in ()).throw(ScreenError('bloodweb_not_found'))
    with pytest.raises(ScreenError,match='next_bloodweb_timeout'):r.wait_next_bloodweb(f,web(NodeState.PURCHASED))
    assert not d.presses
    r.halt()
    with pytest.raises(InterruptedError):r.wait_next_bloodweb(f,web(NodeState.PURCHASED))

def test_slow_read_cannot_accept_a_new_level_after_deadline(monkeypatch):
    clock=Clock();monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    d=Device([web()]);r=engine(d,dry_run=False);f=d.capture();r.wait=clock.advance
    new=web();new.nodes[1].x+=60
    def read():
        clock.advance(31);return f,new,type('Plan',(),{'next_node':1})()
    r.read=read
    with pytest.raises(ScreenError,match='next_bloodweb_timeout'):r.wait_next_bloodweb(f,web(NodeState.PURCHASED))
    assert not d.presses

def test_same_slots_require_several_changed_known_glyphs():
    from src.core.scene_memory import record_scene
    from src.core.level_transition import new_level
    from src.core.models import Node,NodeType
    d=Device([web()]);f=d.capture();old=web(NodeState.PURCHASED);new=web()
    for i in range(2,5):
        old.nodes.append(Node(i,40*i,80,20,NodeState.PURCHASED,NodeType.ITEM,'old'+str(i)))
        new.nodes.append(Node(i,40*i,80,20,NodeState.LOCKED,NodeType.ITEM,'new'+str(i)))
    assert new_level(record_scene(f,old,[]),f,new)
    new.nodes[2].icon_id='old2';new.nodes[3].icon_id='old3'
    assert not new_level(record_scene(f,old,[]),f,new)

def test_manual_prestige_wait_never_clicks_center(monkeypatch):
    clock=Clock();monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    new=web();new.nodes[1].x+=60;new.nodes[1].icon_id='new'
    d=Device([Analysis(400,400,ScreenState.PRESTIGE),Analysis(400,400,ScreenState.REWARD),new,new])
    r=engine(d,dry_run=False);r.settings.refresh_timeout=3;f=d.capture();d.index=0;r.wait=clock.advance
    states=[];r.event=lambda state,*a:states.append(state)
    r.wait_next_bloodweb(f,web(NodeState.PURCHASED))
    assert State.MANUAL_ACTION in states and not d.presses and d.releases

def test_cached_retention_rechecks_new_pin_before_deletion(tmp_path):
    import json
    from src.core.diagnostics import DiagnosticStore
    store=DiagnosticStore(tmp_path,max_runs=2)
    for name in ('a','b'):
        folder=tmp_path/name;folder.mkdir();(folder/'manifest.json').write_text(json.dumps(dict(owner='autobloodweb_v43',pinned=False,total_bytes=1)))
    store.prune();store._writes=1
    path=tmp_path/'a/manifest.json';path.write_text(json.dumps(dict(owner='autobloodweb_v43',pinned=True,total_bytes=1)))
    folder=tmp_path/'c';folder.mkdir();data=dict(owner='autobloodweb_v43',pinned=False,total_bytes=1,analysis_id='c');(folder/'manifest.json').write_text(json.dumps(data))
    store._maintain(data)
    assert path.exists() and (tmp_path/'c').exists() and store._runs<=2

def test_short_center_probe_uses_fresh_pixels_then_full_fallback(monkeypatch):
    import numpy as np
    from tests.test_screen_manager import Backend
    from src.core.screen_manager import ScreenManager,WindowInfo,Rect
    window=WindowInfo(1,'DBD',Rect(0,0,1920,1080),Rect(0,0,1920,1080),96,True)
    clock=[100.];calls=[]
    def center(image,**kwargs):
        calls.append(kwargs.get('local_only',False));return None if kwargs.get('local_only') else (684,562,40)
    monkeypatch.setattr('src.core.bloodweb_detector.find_center',center)
    manager=ScreenManager(Backend(window,np.zeros((1080,1920,3),np.uint8)),clock=lambda:clock[0]);manager.fast_center_retry=True
    manager._center_hint=(window.signature,(684,562,40))
    with pytest.raises(ScreenError,match='frame_unstable'):manager.capture()
    assert calls==[True]
    clock[0]+=1.3
    with pytest.raises(ScreenError,match='frame_unstable'):manager.capture()
    clock[0]+=2.8;frame=manager.capture()
    assert calls==[True,True,True,False] and frame.live
