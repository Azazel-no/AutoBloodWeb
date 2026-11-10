"""Outcome recovery with injected clocks/devices; never performs DBD input."""
import copy,time
import pytest
from tests.test_automation import Device,web,engine
from src.core.models import NodeState,ScreenState,Analysis
from src.core.automation import State
from src.core.purchase_outcome import PurchaseEvidence


class Clock:
    def __init__(self):self.now=100.
    def advance(self,seconds):self.now+=seconds


def scenario(monkeypatch,behavior):
    clock=Clock();monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    class Replay(Device):
        def capture(self):clock.advance(.35);return super().capture()
        def analyze(self,image,source):
            result=web();behavior(self,result,clock);return result
    device=Replay([web()]);runner=engine(device,dry_run=False,max_actions=1)
    runner.settings.purchase_timeout=3;runner.wait=clock.advance
    return runner,device,clock


def test_one_proven_miss_retries_fresh_corrected_center(monkeypatch):
    def behavior(d,r,c):
        if d.presses:r.nodes[1].x+=3
        if len(d.presses)==2:r.nodes[1].state=NodeState.PURCHASED
    runner,device,_=scenario(monkeypatch,behavior);runner.run()
    assert [p for p,d in device.presses]==[(-170,200),(-167,200)]
    assert runner.actions==1 and runner.last_purchase_outcome=='purchased'
    assert len([e for e in runner.report.entries if e['stage']=='retry_revalidated'])==1


def test_second_miss_stops_with_two_presses_maximum(monkeypatch):
    runner,device,_=scenario(monkeypatch,lambda *a:None);runner.run()
    assert len(device.presses)==2 and runner.actions==0 and not runner.pending_action
    # The second verified miss no longer pauses: it counts towards the out-of-bloodpoints rule.
    assert [e['count'] for e in runner.report.entries if e['stage']=='verified_unbought_purchase']==[1]


def test_delayed_red_during_full_recheck_never_retries(monkeypatch):
    def behavior(d,r,c):
        if d.presses and d.index>=8:r.nodes[1].state=NodeState.PURCHASED
    runner,device,_=scenario(monkeypatch,behavior);runner.run()
    assert len(device.presses)==1 and runner.actions==1 and runner.last_purchase_outcome=='purchased_late'


@pytest.mark.parametrize('change',['missing','absorbed','uncertain','identity','graph','roi'])
def test_ambiguous_or_changed_scene_never_retries(monkeypatch,change):
    def behavior(d,r,c):
        if not d.presses:return
        if change=='missing':r.nodes=r.nodes[:1];r.edges=[]
        elif change=='absorbed':r.nodes[1].state=NodeState.ABSORBED
        elif change=='uncertain':r.nodes[1].state_confidence=.5
        elif change=='identity':r.nodes[1].icon_id='other'
        elif change=='graph':r.edges=[]
        elif change=='roi':r.nodes[1].x+=40
    runner,device,_=scenario(monkeypatch,behavior);runner.run()
    assert len(device.presses)==1 and runner.actions==0


@pytest.mark.parametrize('stop',['f9','registration'])
def test_stop_or_registration_loss_during_recheck_prevents_retry(monkeypatch,stop):
    runner,device,_=scenario(monkeypatch,lambda *a:None)
    def event(state,message,*args):
        if message=='purchase_recheck':
            if stop=='f9':runner.halt()
            else:runner.input_ready_check=lambda:False
    runner.event=event;runner.run()
    assert len(device.presses)==1 and device.releases


def test_balance_decrease_is_not_proof_of_miss():
    device=Device([web()]);frame=device.capture();frame.bloodpoints=1000
    evidence=PurchaseEvidence(frame,web(),web().nodes[1],0.)
    fresh=device.capture();fresh.bloodpoints=500
    for t in (1.,2.,3.):assert not evidence.observe(fresh,web(),t)[2]


def test_one_red_then_available_never_proves_miss():
    device=Device([web()]);frame=device.capture();evidence=PurchaseEvidence(frame,web(),web().nodes[1],0.)
    assert not evidence.observe(frame,web(NodeState.PURCHASED),1.)[1]
    for t in (2.,3.,4.):assert not evidence.observe(frame,web(),t)[2]


def test_new_start_cannot_retry_unresolved_saved_scene():
    from src.core.scene_memory import record_scene
    device=Device([web()]);frame=device.capture();point=frame.to_screen(230,200)
    runner=engine(device,dry_run=False)
    runner.persisted_scene=record_scene(frame,web(),[point],dict(point=point,radius=20,icon='target'))
    runner.scene_record=runner.persisted_scene;runner.attempted=[point];runner.run()
    assert not device.presses and any(e.get('reason')=='purchase_result_unknown' for e in runner.report.entries)
    assert runner.scene_record['pending']


def test_new_start_resolves_two_late_red_observations_without_press():
    from src.core.scene_memory import record_scene
    device=Device([web(NodeState.PURCHASED)]);frame=device.capture();point=frame.to_screen(230,200)
    runner=engine(device,dry_run=False);runner.settings.refresh_timeout=.05
    runner.persisted_scene=record_scene(frame,web(),[point],dict(point=point,radius=20,icon='target'))
    runner.scene_record=runner.persisted_scene;runner.attempted=[point];runner.run()
    assert not device.presses and runner.scene_record['pending'] is None


def test_recovery_requires_accepted_input_and_prior_red_purchase():
    from src.core.input_method import recovery_evidence
    receipt=dict(mode='click',hold_seconds=.25,blocked_reason='purchase_not_confirmed')
    settings=dict(purchase_mode='click',hold_seconds=.25)
    events=[dict(stage='GetPhysicalCursorPos',actual=[1,2],requested=[1,2],result=1),
        dict(stage='SendInput',flag=2,result=1,time='2026-10-12T19:00:00+00:00'),
        dict(stage='SendInput',flag=4,result=1,time='2026-10-12T19:00:00.050000+00:00'),
        dict(stage='physical_action_complete',mouse_released=True),
        dict(stage='purchase_observation',after='purchased'),dict(stage='purchase_observation',after='purchased'),
        dict(stage='failure',reason='purchase_not_confirmed')]
    assert recovery_evidence(receipt,settings,dict(entries=events))[0]
    events[1]['result']=0
    assert recovery_evidence(receipt,settings,dict(entries=events))[1]=='input_recovery_input_error'


def test_original_event_webp_is_positive_but_not_a_live_web(root):
    import hashlib,json
    from src.utils.images import read_image
    from src.core.node_category import classify_category
    from src.core.bloodweb_detector import BloodwebDetector
    from src.core.icon_library import IconLibrary
    record=json.loads((root/'assets/reference_versions/v46_event.json').read_text(encoding='utf-8'))
    path=root/record['source'];assert hashlib.sha256(path.read_bytes()).hexdigest()==record['sha256']
    image=read_image(path);evidence=classify_category(image,148)
    assert evidence.event_offering and evidence.category=='offering' and record['experimental']
    assert BloodwebDetector(IconLibrary(root).build()).analyze(image,str(path)).screen_state==ScreenState.UNKNOWN


def test_six_reviewed_survivor_glyphs_without_threshold_change(root,library):
    import json,math
    from src.utils.images import read_image
    from src.core.bloodweb_detector import BloodwebDetector
    review=json.loads((root/'assets/reference_versions/v46_survivor.json').read_text(encoding='utf-8'))
    images={};results={}
    for p in review['proposals']:
        if p['source'] not in results:
            image=read_image(root/p['source'])
            if p['roi']:
                x,y,w,h=p['roi'];image=image[y:y+h,x:x+w]
            results[p['source']]=BloodwebDetector(library).analyze(image)
        node=min(results[p['source']].nodes,key=lambda n:math.hypot(n.x-p['x'],n.y-p['y']))
        assert node.icon_id==p['new'] and node.confidence>=.86 and node.icon_margin>=.035 and node.contour_score>=.68


def test_new_real_hud_digits_and_anchor_preserve_unknown(root):
    from src.core.resources import ResourceReader
    from src.utils.images import read_image
    import numpy as np
    reader=ResourceReader(root)
    assert set(reader.templates)==set('0123456789')
    assert reader.read(read_image(root/'tests/fixtures/v46/captures/wraith_3cbbffd0456a.png'))==2026884
    assert reader.read(read_image(root/'tests/fixtures/v46/captures/spirit_95203fc51064.png'))==1751634
    assert reader.read(np.zeros((1080,1920,3),np.uint8)) is None


def test_entity_removing_old_nodes_is_not_a_new_level():
    from src.core.scene_memory import record_scene,layout_changed
    from src.core.models import Node,NodeType
    d=Device([web()]);frame=d.capture();old=web()
    for i in range(2,8):old.nodes.append(Node(i,20+i*40,80,20,NodeState.LOCKED,NodeType.ITEM))
    subset=copy.deepcopy(old);subset.nodes=subset.nodes[:2]
    assert not layout_changed(record_scene(frame,old,[]),frame,subset)
    assert not engine(d).changed_layout((frame,old),(frame,subset))


def test_two_refreshes_then_old_web_keep_attempted_point():
    device=Device([web(),web(),web(NodeState.PURCHASED),web(NodeState.PURCHASED),
        Analysis(400,400,ScreenState.REFRESH),Analysis(400,400,ScreenState.REFRESH),web(),web()])
    runner=engine(device,dry_run=False,max_actions=2);runner.run()
    assert runner.actions==1 and len(device.presses)==1
    assert any(e.get('reason')=='next_bloodweb_timeout' for e in runner.report.entries)


def test_cursor_move_during_guard_prevents_down():
    import threading
    from tests.test_input_control import InputStub
    from src.core.input_control import Mouse
    from src.core.screen_manager import ScreenError
    mouse=Mouse();stub=InputStub();mouse.u=stub;checks=0
    def guard():
        nonlocal checks
        checks+=1
        if checks==2:stub.positions.append((9,9))
    with pytest.raises(ScreenError,match='cursor_position_mismatch'):mouse.purchase((1,1),.05,threading.Event(),guard)
    assert not stub.flags and not mouse.held


def test_historical_method_restoration_retains_uncertain_target(root):
    import json
    from src.core.input_method import legacy_purchase_scene
    payload=json.loads((root/'tests/fixtures/v46/historical_action_audit.json').read_text(encoding='utf-8'))
    memory=legacy_purchase_scene(payload)
    assert memory['pending']['point']==[684,685] and memory['pending']['icon']=='survivor_offering_037'
    assert [684,685] in memory['attempted'] and memory['legacy']


def test_continuous_eight_actions_across_two_confirmed_webs(monkeypatch):
    from src.core.models import Node,NodeType
    clock=Clock();monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    class Levels(Device):
        def __init__(self):
            super().__init__([web()]);self.level=0;self.complete_reads=0;self.graph=self.make_graph()
        def make_graph(self):
            r=web();r.nodes=r.nodes[:1];r.edges=[]
            for i in range(1,5):
                r.nodes.append(Node(i,230+self.level*60,50+i*60,20,NodeState.AVAILABLE,NodeType.ITEM,'target'+str(i),state_confidence=.95,geometry_confidence=.95));r.edges.append((0,i))
            return r
        def capture(self):clock.advance(.35);return super().capture()
        def analyze(self,image,source):
            if all(n.state==NodeState.PURCHASED for n in self.graph.nodes[1:]):
                self.complete_reads+=1
                if self.level==0 and self.complete_reads in (3,4):return Analysis(400,400,ScreenState.REFRESH)
                if self.level==0 and self.complete_reads>=5:self.level=1;self.graph=self.make_graph();self.complete_reads=0
            return copy.deepcopy(self.graph)
        def purchase(self,point,duration,stop,check):
            super().purchase(point,duration,stop,check)
            next(n for n in self.graph.nodes if (n.x-400,n.y)==point).state=NodeState.PURCHASED
    device=Levels();runner=engine(device,dry_run=False,max_actions=None,max_runtime=None,run_mode='continuous');runner.settings.refresh_timeout=6;runner.wait=clock.advance
    runner.event=lambda state,*a:runner.halt() if runner.actions==8 and state==State.WAIT_REFRESH else None
    runner.run()
    assert runner.actions==8 and len(device.presses)==8 and device.releases
    assert any(e['stage']=='new_level_confirmed' for e in runner.report.entries)


def test_frozen_capture_bytes_and_real_nodes_never_become_event(root,library):
    import json,hashlib
    from src.utils.images import read_image
    from src.core.bloodweb_detector import BloodwebDetector
    for screen in json.loads((root/'tests/fixtures/v46/capture_annotations.json').read_text(encoding='utf-8'))['screens']:
        path=root/screen['source'];assert hashlib.sha256(path.read_bytes()).hexdigest()==screen['sha256']
        x,y,w,h=screen['roi'];result=BloodwebDetector(library,'killer',character=screen['character']).analyze(read_image(path)[y:y+h,x:x+w])
        assert not any(n.event_offering for n in result.nodes)
