"""Purchase evidence gaps and explicit grouped-input experiments; no native input."""
import copy,time
from dataclasses import replace
import pytest
from tests.test_automation import Device,engine,web
from src.core.models import NodeState
from src.core.action_outcomes import ActionOutcomes


def test_red_missing_red_reconciles_actual_positive_frames(tmp_path):
    before=web();after=web(NodeState.PURCHASED);missing=web();missing.nodes=missing.nodes[:1]
    d=Device([before,before,after,missing,after]);e=engine(d,dry_run=False,max_actions=1)
    e.outcome_journal=ActionOutcomes(tmp_path/'outcomes.json');e.run()
    assert len(d.presses)==e.actions==1 and not e.pending_action
    assert len(e.purchase_evidence.red_observations)==2
    assert [r.nodes[1].state for f,r in e.purchase_evidence.red_observations]==[NodeState.PURCHASED]*2
    assert e.scene_record['last_outcome']['confirmed_nodes']==1


def test_available_between_red_resets_positive_evidence(tmp_path):
    from src.core.purchase_outcome import PurchaseEvidence
    d=Device([web()]);f=d.capture();n=web().nodes[1];p=PurchaseEvidence(f,web(),n,time.monotonic())
    for i,state in enumerate([NodeState.PURCHASED,NodeState.AVAILABLE,NodeState.PURCHASED]):
        found,done,miss=p.observe(replace(f,captured_at=f.captured_at+i+1),web(state),time.monotonic())
        assert not done
    assert p.red_count==1 and len(p.red_observations)==1


def test_current_native_prestige_wreath_and_occlusion_negatives(root,library):
    from src.utils.images import read_image
    from src.core.bloodweb_detector import find_center,BloodwebDetector
    from src.core.prestige import ready_evidence
    from src.core.models import ScreenState
    image=read_image(root/'references/bloodweb/v56_dark_lord_prestige/native_ready.png')
    center=find_center(image)
    assert center==(684,563,40)
    evidence=ready_evidence(image,root,center)
    assert evidence['source']=='native_reviewed_wreath' and evidence['price'] is None
    result=BloodwebDetector(library,'killer','dark_lord').analyze(image)
    assert result.screen_state==ScreenState.PRESTIGE_READY
    hidden=image.copy();hidden[503:624,624:745]=0
    assert ready_evidence(hidden,root,center) is None
    normal=read_image(root/'references/bloodweb/bloodweb_level43_start.png')
    assert ready_evidence(normal,root,find_center(normal)) is None


@pytest.mark.parametrize('reason',['frame_unstable','item_tooltip','bloodweb_not_found'])
def test_target_confirmation_transient_uses_recovery_without_second_click(monkeypatch,reason):
    from src.core.screen_manager import ScreenError
    d=Device([web(),web(),web(NodeState.PURCHASED),web(NodeState.PURCHASED)])
    e=engine(d,dry_run=False,max_actions=1);calls=[0]
    def observe(*args):
        calls[0]+=1
        if calls[0]==1:raise ScreenError(reason)
    monkeypatch.setattr('src.core.target_evidence.target_observation',observe)
    e.run();assert e.actions==1 and len(d.presses)==1 and not e.pending_action


@pytest.mark.parametrize('stop_after,transient',[(None,False),(None,True),(1,False)])
def test_group_defers_result_until_inputs_and_preserves_stop_scene(tmp_path,stop_after,transient):
    from types import SimpleNamespace
    from src.core.models import Node,NodeType
    from src.core.grouped_purchase import execute_group
    from src.core.path_planner import RoutePlan
    result=web();result.level=20;result.level_confidence=.99
    result.nodes.extend([Node(2,150,290,20,NodeState.AVAILABLE,NodeType.ITEM,'b',state_confidence=.95,geometry_confidence=.95),
        Node(3,60,200,20,NodeState.AVAILABLE,NodeType.ITEM,'c',state_confidence=.95,geometry_confidence=.95)])
    result.edges.extend([(0,2),(0,3)])
    class Capture(Device):
        fast_center_retry=False
        def analyze(self,*args):
            r=copy.deepcopy(result)
            for n in r.nodes:
                if any(self.window.client.x+n.x==p[0][0] and n.y==p[0][1] for p in self.presses):n.state=NodeState.PURCHASED
            return r
        def purchase(self,*args):
            super().purchase(*args)
            if stop_after and len(self.presses)==stop_after:e.stop.set()
    d=Capture([result]);e=engine(d,dry_run=False,max_actions=3);e.priorities=[];e.runtime_deadline=time.monotonic()+5
    e.outcome_journal=ActionOutcomes(tmp_path/'journal.json')
    calls=[0]
    def compact(*args):
        from src.core.fast_opening import OpeningFallback
        calls[0]+=1
        if transient and calls[0]==1:raise OpeningFallback('center_or_overlay_changed')
        return d.analyze()
    e.opening=SimpleNamespace(expected_purchases=set(),analyze=compact)
    f=d.capture();f.level=20;targets=result.nodes[1:]
    def recover(*args,**kwargs):
        # The first result verification occurs only after all three commands.
        assert len(d.presses)==3
        assert d.fast_center_retry  # Same bounded animation recovery as single.
        fresh=d.capture();fresh.level=20
        r=d.analyze();return fresh,r,RoutePlan()
    e.recover_read=recover
    if stop_after:
        with pytest.raises(InterruptedError):execute_group(e,f,result,targets,lambda group:None)
        assert e.pending_action and len(e.scene_record['pending']['batch']['physical_points'])==1 and d.releases
    else:
        execute_group(e,f,result,targets,lambda group:None)
        assert len(d.presses)==e.actions==3 and not e.pending_action
        assert e.scene_record['last_outcome']['confirmed_nodes']==3


def test_continuous_chain_requires_the_explicit_production_permit(tmp_path):
    from tests.test_v51_hybrid import trial_setup,bought
    from src.core.chain_trial import execute_short_chain
    from src.core.screen_manager import ScreenError
    e,plan,permit,previous,latest=trial_setup(tmp_path)
    e.settings.run_mode='continuous'
    with pytest.raises(ScreenError,match='chain_trial_only'):execute_short_chain(e,plan,permit,previous,latest)
    from src.core.path_planner import RoutePlan
    e.settings.native_chain=True;e.native_chain_permit=permit;frame,result=latest
    after=bought(bought(result,1),2);stamp=[frame.captured_at]
    def observe(deadline):
        stamp[0]+=.03
        return replace(frame,captured_at=stamp[0],bloodpoints=25500),copy.deepcopy(after),RoutePlan()
    e.recover_read=observe
    outcome=execute_short_chain(e,plan,permit,previous,latest)
    assert outcome['confirmed']==[1,2] and len(e.mouse.presses)==1 and e.settings.run_mode=='continuous'


def test_real_pixels_confirm_target_without_full_hough_or_route(root,library,monkeypatch):
    import json
    from types import SimpleNamespace
    from collections import deque
    from scripts.inspect_opening_failure_v49 import stored_frame
    from src.core.bloodweb_detector import BloodwebDetector
    from src.core.fast_opening import FastOpening
    from src.core.target_evidence import target_observation
    from src.core.geometry import purchasable
    folder=root/'debug/v49/live_session_b0c39801';rows=json.loads((folder/'frames.json').read_text());by_id={r['analysis_id']:r for r in rows}
    priorities=json.loads((folder/'session.json').read_text())['authorization']['priorities']
    initial=stored_frame(by_id['1791990044292251700_fb36ed11']);detector=BloodwebDetector(library,'killer','unknown')
    result=detector.analyze_live(initial);opening=FastOpening();assert opening.begin(initial,result,detector,priorities,'smart',0)
    def forbidden(*a,**k):pytest.fail('full analysis or library/route work during target confirmation')
    monkeypatch.setattr(detector.matcher,'match',forbidden);monkeypatch.setattr('cv2.HoughCircles',forbidden)
    monkeypatch.setattr('src.core.fast_opening.plan_route',forbidden)
    e=SimpleNamespace(opening=opening,detector=detector,recent_frames=deque(maxlen=4),actions=0,
        performance=SimpleNamespace(add=lambda *a,**k:None),trace=lambda *a,**k:None)
    states=[]
    for ident in ['1791990044684610100_2d8f042f','1791990046752724200_c52677f9','1791990047703085800_31f3b530']:
        e.capture_frame=lambda:stored_frame(by_id[ident]);observed=target_observation(e,initial,result.nodes[2])
        assert observed is not None
        f,r,p=observed;assert r.details['confirmation_only'] and len(r.nodes)==1 and p.next_node is None
        assert not purchasable(r.nodes[0],r.nodes,r.edges)
        states.append(r.nodes[0].state)
    assert states==[NodeState.AVAILABLE,NodeState.PURCHASED,NodeState.PURCHASED]


def test_unconfirmed_final_item_retires_pending_on_proven_prestige_transition():
    from src.core.models import Analysis,ScreenState
    from src.core.scene_memory import record_scene
    d=Device([web()]);e=engine(d,dry_run=False);f=d.capture();r=web();r.level=50
    e.scene_record=record_scene(f,r,[f.to_screen(230,200)],{'point':list(f.to_screen(230,200)),'radius':20})
    ready=Analysis(400,400,ScreenState.PRESTIGE_READY,level=50,level_confidence=.99)
    a=replace(f,captured_at=f.captured_at+.1);b=replace(f,captured_at=f.captured_at+.2)
    e.recent_frames.extend([(a,ready,None),(b,ready,None)])
    e.wait_next_bloodweb=lambda *args:(b,ready,None)
    e.finish_level_attempt(f,r)
    assert e.scene_record['pending'] is None and e.unconfirmed_level_actions==1 and e.actions==0
    assert e.scene_record['terminal_transition']['stamps']==[a.captured_at,b.captured_at]


@pytest.mark.parametrize('pending',[False,True])
def test_terminal_scene_restore_requires_no_pending_and_two_current_maps(pending):
    from src.core.models import Analysis,ScreenState
    from src.core.scene_memory import record_scene
    ready=Analysis(400,400,ScreenState.PRESTIGE_READY,level=50,level_confidence=.99)
    d=Device([ready]);e=engine(d,dry_run=False,max_actions=1)
    f=d.capture();memory=record_scene(f,web(),[],{'point':[1,2],'radius':20} if pending else None)
    memory['terminal_transition']={'screen':'prestige_ready','level':50,'stamps':[1,2]}
    e.persisted_scene=memory;e.run()
    assert not d.presses
    assert bool(e.persisted_scene)==pending
    assert sum(r['stage']=='terminal_scene_restored' for r in e.report.entries)==(0 if pending else 1)


@pytest.mark.parametrize('blocked',[None,'chain','batch','automatic','chain_blocked_reason','wrong_level','no_proof','wrong_geometry'])
def test_saved_final_single_can_retire_on_two_positive_terminal_maps_without_credit(blocked):
    from src.core.models import Analysis,ScreenState
    from src.core.scene_memory import record_scene
    d=Device([web()]);e=engine(d,dry_run=False)
    f=d.capture();r=Analysis(400,400,ScreenState.PRESTIGE_READY,level=50,level_confidence=.99,
        details={'prestige_ready':{'score':.99}})
    memory=record_scene(f,web(),[],{'point':[1,2],'radius':20})
    memory.update(level=50,level_confidence=.99)
    if blocked in ('chain','batch','automatic','chain_blocked_reason'):memory['pending'][blocked]=True
    if blocked=='wrong_level':memory['level']=49
    if blocked=='no_proof':r.details={}
    if blocked=='wrong_geometry':memory['window']['dpi']=96
    e.persisted_scene=e.scene_record=memory;e.pending_action=True
    e.signature=f.window.signature;e.input_ready_check=lambda:True
    assert not e.retire_terminal_single(f,r)
    assert not e.retire_terminal_single(f,r)
    fresh=replace(f,captured_at=time.monotonic())
    assert e.retire_terminal_single(fresh,r)==(blocked is None)
    assert e.actions==0 and not d.presses
    assert bool(e.persisted_scene)==(blocked is not None)


@pytest.mark.parametrize('case',['ignored_first','debit','command_cap','focus','stop','unknown','first_web'])
def test_prestige_repeats_only_for_two_unchanged_ready_frames(monkeypatch,case):
    from src.core.models import Analysis,ScreenState,Node,NodeType
    from src.core.prestige import activate_ready
    from src.core.screen_manager import ScreenError
    from src.core.path_planner import RoutePlan
    from tests.test_v46 import Clock
    clock=Clock();monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    ready=Analysis(400,400,ScreenState.PRESTIGE_READY,
        [Node(0,200,200,35,NodeState.CENTER,NodeType.PRESTIGE,geometry_confidence=.95)],
        level=50,level_confidence=.99,details={'prestige_ready':{'score':1}})
    next_web=web();next_web.level=1;next_web.level_confidence=.99
    d=Device([ready]);d.parking_point=lambda f:None
    e=engine(d,dry_run=False,auto_prestige=True);e.prestige_approved=True;e.wait=clock.advance
    e.runtime_deadline=clock.now+2
    initial=replace(d.capture(),bloodpoints=50000,level=50,level_confidence=.99)
    e.prestige_ui_proof=dict(signature=initial.window.signature,created=clock.now,price=20000,point=(-200,200))
    reads=[0]
    def read(*args,**kwargs):
        reads[0]+=1;clock.advance(.05)
        if case=='stop' and reads[0]==2:e.stop.set()
        if case=='focus' and reads[0]==2:d.focus=False
        if case=='first_web':r=next_web if reads[0]==1 or reads[0]>=4 else ready
        elif case=='unknown' and reads[0]<=2:r=Analysis(400,400,ScreenState.UNKNOWN)
        else:r=ready if reads[0]<=2 else next_web
        bp=30000 if r.screen_state==ScreenState.BLOODWEB or case=='debit' else 50000
        return replace(initial,captured_at=clock.now,bloodpoints=bp),copy.deepcopy(r),RoutePlan()
    e.recover_read=read
    if case=='command_cap':e.settings.max_input_commands=1
    if case in ('command_cap','focus','stop'):
        with pytest.raises((ScreenError,InterruptedError)):activate_ready(e,initial,ready)
        assert len(d.presses)==1 and e.pending_action and d.releases
    else:
        activate_ready(e,initial,ready)
        assert len(d.presses)==(2 if case=='ignored_first' else 1)
        assert not e.pending_action and e.prestige_actions==1
        assert e.actions==0 and e.attempts==len(d.presses)


def test_prestige_animation_does_not_expire_new_rewards_stability(monkeypatch):
    from src.core.models import Analysis,ScreenState,Node,NodeType
    from src.core.path_planner import RoutePlan
    from tests.test_v54_video import modal_runner,reward
    ready=Analysis(400,400,ScreenState.PRESTIGE_READY,
        [Node(0,200,200,35,NodeState.CENTER,NodeType.PRESTIGE,geometry_confidence=.95)],
        level=50,level_confidence=.99,details={'prestige_ready':{'score':1}})
    rows=[ready]*4+[reward()]*2+[web()]*3+[web(NodeState.PURCHASED)]*2
    clock,d,e=modal_runner(monkeypatch,rows);e.settings.max_actions=1
    original_capture=d.capture
    def independent_capture():
        clock.advance(.02)
        return original_capture()
    d.capture=independent_capture
    def activated(engine,frame,result):
        clock.advance(2)  # Longer than the old purchase/stability timeout.
        return replace(frame,captured_at=clock.now),reward(),RoutePlan()
    monkeypatch.setattr('src.core.prestige.activate_ready',activated)
    e.run()
    assert not [r for r in e.report.entries if r['stage']=='failure' and r['reason']!='action_limit']
    assert e.actions==1 and len(d.presses)==2 and not e.pending_action
    assert any(r['stage']=='reward_dismissed' for r in e.report.entries)


@pytest.mark.parametrize('stale_count',[1,3])
def test_reward_transition_discards_bounded_stale_frames_without_click_retry(monkeypatch,stale_count):
    from tests.test_v54_video import modal_runner,reward
    from src.core.modal_recovery import dismiss_reward
    from src.core.screen_manager import ScreenError
    clock,d,e=modal_runner(monkeypatch,[reward(),reward(),web(),web()])
    e.settings.refresh_timeout=4
    original=d.capture;remaining=[stale_count]
    def capture():
        if d.presses and remaining[0]:
            remaining[0]-=1;clock.advance(.05);raise ScreenError('stale_frame')
        return original()
    d.capture=capture;frame=d.capture()
    if stale_count==1:
        fresh,result,plan=dismiss_reward(e,frame,reward())
        assert result.screen_state.value=='bloodweb'
    else:
        with pytest.raises(ScreenError,match='stale_frame'):dismiss_reward(e,frame,reward())
    assert len(d.presses)==1 and d.releases>=2


def test_empty_compact_route_with_remaining_nodes_gets_full_same_frame(monkeypatch):
    from src.core.path_planner import RoutePlan
    d=Device([web()]);e=engine(d,dry_run=False)
    e.opening.active=True
    e.opening.analyze=lambda *a:copy.deepcopy(web())
    e.opening.plan=lambda *a:RoutePlan(reason='persistent:no_confident_action')
    original=d.analyze;full=[0]
    def analyze(*args):full[0]+=1;return original(*args)
    d.analyze=analyze
    frame,result,plan=e.read()
    assert full[0]==1 and e.opening.full_scans==1 and plan.next_node==1
    assert not d.presses
    assert e.opening.aborts=={'remaining_action_unproven':1}


def test_saved_two_input_group_resolves_both_points_before_resume(tmp_path):
    from src.core.models import Node,NodeType
    from src.core.scene_memory import record_scene
    initial=web();initial.level=4
    initial.nodes.append(Node(2,300,200,20,NodeState.AVAILABLE,NodeType.ITEM,'b',state_confidence=.95,geometry_confidence=.95))
    initial.edges.append((0,2));d=Device([initial]);e=engine(d,dry_run=False)
    frame=d.capture();points=[frame.to_screen(n.x,n.y) for n in initial.nodes[1:]]
    memory=record_scene(frame,initial,points,dict(point=points[-1],radius=20,batch={'physical_points':points}))
    e.persisted_scene=e.scene_record=memory;e.attempted=points
    e.outcome_journal=ActionOutcomes(tmp_path/'outcomes.json')
    after=copy.deepcopy(initial)
    for n in after.nodes[1:]:n.state=NodeState.PURCHASED
    assert not e.recheck_saved_scene(replace(frame,captured_at=frame.captured_at+.1),after)
    assert e.recheck_saved_scene(replace(frame,captured_at=frame.captured_at+.2),after)
    assert not e.persisted_scene and e.scene_record['pending'] is None
    assert e.scene_record['last_outcome']['confirmed_nodes']==2 and not d.presses




@pytest.mark.parametrize('clears',[True,False])
def test_price_preparation_requires_two_consecutive_clean_maps_without_new_hover(clears):
    from scripts.live_test_v54 import scene_key,wait_price_scene
    from src.core.path_planner import RoutePlan
    from src.core.screen_manager import ScreenError
    initial=web();initial.level=9;initial.level_confidence=.99
    d=Device([initial]);e=engine(d,dry_run=False);f=d.capture()
    blocked=copy.deepcopy(initial);blocked.nodes[1].state=NodeState.ABSORBED
    maps=[blocked,initial,initial] if clears else [blocked,initial,blocked,initial]
    reads=[0]
    def read(*args,**kwargs):
        result=maps[reads[0]];reads[0]+=1
        return replace(f,captured_at=f.captured_at+reads[0]*.01),result,RoutePlan(1,[1])
    e.recover_read=read
    if clears:
        first,second=wait_price_scene(e,scene_key(f,initial),f.to_screen(230,200),True)
        assert reads[0]==3 and first[0].captured_at<second[0].captured_at
    else:
        with pytest.raises(ScreenError,match='chain_map_changed'):wait_price_scene(e,scene_key(f,initial),f.to_screen(230,200),True)
    assert not d.presses


def test_center_price_clean_wait_reseeds_incomplete_compact_once_without_input():
    from scripts.live_test_v54 import scene_key,wait_price_scene
    from src.core.path_planner import RoutePlan
    initial=web();initial.level=8;initial.level_confidence=.99;initial.nodes[0].geometry_confidence=.95
    partial=copy.deepcopy(initial);partial.nodes=partial.nodes[:1]
    d=Device([initial]);e=engine(d,dry_run=False);f=d.capture();maps=[partial,initial,initial]
    reads=[];cancels=[];e.opening.cancel=lambda reason:cancels.append(reason)
    def read(*args,**kwargs):
        result=maps[len(reads)];reads.append(result)
        return replace(f,captured_at=f.captured_at+len(reads)*.01),result,RoutePlan(1,[1])
    e.recover_read=read
    a,b=wait_price_scene(e,scene_key(f,initial),f.to_screen(230,200),False,True)
    assert len(reads)==3 and cancels==['native_price_clean_incomplete']
    assert a[0].captured_at<b[0].captured_at and not d.presses
