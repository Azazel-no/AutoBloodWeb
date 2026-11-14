"""Contract/device models and real-pixel replay; none is a live purchase test."""
import copy
import json
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from src.core.models import Analysis,Node,NodeType,NodeState,ScreenState
from src.core.screen_manager import CapturedFrame,Rect,WindowInfo,ScreenError
from src.core.path_planner import RoutePlan
from src.core.persistent_route import PersistentRoute,PersistentVision,map_key
from src.core.chain_plan import ChainPlan,ChainEvidence,PreviewObservation,PriceRegion,read_preview,propose_chain
from src.core.chain_trial import ChainPermit,execute_short_chain
from src.core.shadow_planner import ShadowPlanner


def scene(count=8):
    nodes=[Node(0,40,100,20,NodeState.CENTER,NodeType.CENTER,state_confidence=.95)]
    nodes += [Node(i,40+60*i,100,20,NodeState.AVAILABLE if i==1 else NodeState.LOCKED,NodeType.ADDON,
        'goal' if i==count else None,state_confidence=.95,geometry_confidence=.95) for i in range(1,count+1)]
    result=Analysis(640,240,ScreenState.BLOODWEB,nodes,[(i,i+1) for i in range(count)],level=20,level_confidence=1,details={'center':[40,100,20]})
    window=WindowInfo(1,'injected contract',Rect(0,0,640,240),Rect(0,0,640,240),96,True,1)
    frame=CapturedFrame(np.zeros((240,640,3),np.uint8),window,Rect(0,0,640,240),time.monotonic(),level=20,level_confidence=1,bloodpoints=30000)
    return frame,result


def bought(result,i):
    result=copy.deepcopy(result);result.nodes[i].state=NodeState.PURCHASED
    if i+1<len(result.nodes):result.nodes[i+1].state=NodeState.AVAILABLE
    return result


def trial_setup(tmp_path):
    frame,result=scene(2);frame.captured_at=time.monotonic()-.05
    previous=(replace(frame,captured_at=frame.captured_at-.03),copy.deepcopy(result))
    plan=ChainPlan(map_key(frame,result,0),1,1,2,[1,2],game_preview=[1,2],total_bp=4500,
        priorities=['goal'],preview_stamps=[frame.captured_at-.2,frame.captured_at-.1],
        physical_points=[frame.to_screen(n.x,n.y) for n in result.nodes[1:]],price_provenance='0'*64)
    permit=ChainPermit('test_scope',tmp_path/'ledger.json',1,2,4500,time.time()+10,frame.window.signature,('goal',),True,True)
    from src.core.automation import Automation,Settings
    from tests.test_automation import Device
    device=Device([result]);device.capture=lambda:frame
    device.parking_point=lambda f:(8,8);device.park=lambda *a:None
    engine=Automation(device,device,device,['goal'],Settings(dry_run=False,startup_delay=0,purchase_timeout=1,poll_interval=.01),approved=True,hotkeys_ready=True)
    return engine,plan,permit,previous,(frame,result)


def test_route_lasts_entire_level_and_only_replans_on_material_change():
    frame,result=scene();route=PersistentRoute();memory=SimpleNamespace(generation=1)
    assert route.choose(frame,result,['goal'],memory=memory).next_node==1
    assert len(route.queue)==8 and len(memory.route_plan['physical_points'])==9
    for i in range(1,9):
        result=bought(result,i);frame=replace(frame,captured_at=frame.captured_at+.3)
        plan=route.choose(frame,result,['goal'],memory=memory)
        assert plan.next_node==(i+1 if i<8 else None)
    assert route.replans==1 and route.reuses==8
    assert memory.route_plan['remaining']==[]


def test_entity_and_graph_change_replan_from_memory_without_detector():
    frame,result=scene();memory=SimpleNamespace(generation=1);route=PersistentRoute()
    route.choose(frame,result,['goal'],memory=memory)
    result.nodes[8].state=NodeState.ABSORBED
    plan=route.choose(replace(frame,captured_at=frame.captured_at+.2),result,['goal'],memory=memory)
    assert route.reason=='entity_observed' and route.replans==2 and plan.next_node==1
    result.edges.remove((0,1))
    assert route.choose(frame,result,['goal'],memory=memory).next_node is None
    assert route.reason=='fresh_graph_changed' and route.replans==3


def test_pending_available_target_waits_without_replan_or_retry():
    frame,result=scene();route=PersistentRoute();route.choose(frame,result,['goal'])
    waiting=route.choose(frame,result,['goal'],excluded={1})
    assert waiting.next_node is None and route.reason=='pending_confirmation' and route.replans==1
    assert len(route.queue)==8
    result=bought(result,1)
    assert route.choose(frame,result,['goal'],excluded={1}).next_node==2
    assert route.replans==1


def test_released_unowned_exclusions_restore_empty_route_once():
    frame,result=scene();route=PersistentRoute()
    assert route.choose(frame,result,['goal'],excluded={1}).next_node is None
    assert route.choose(frame,result,['goal']).next_node==1
    assert route.reason=='unowned_exclusions_cleared' and route.replans==2
    assert route.choose(frame,result,['goal']).next_node==1 and route.replans==2


def test_confirmed_purchase_leaving_exclusions_reuses_remaining_route():
    frame,result=scene();route=PersistentRoute();route.choose(frame,result,['goal'])
    assert route.choose(frame,result,['goal'],excluded={1}).next_node is None
    result=bought(result,1)
    assert route.choose(frame,result,['goal']).next_node==2 and route.replans==1


def test_independently_accepted_same_identity_category_uncertainty_keeps_route():
    frame,result=scene(2);route=PersistentRoute();route.choose(frame,result,['goal'])
    result.nodes[2].category_hint='unknown';result.nodes[2].category_candidates=['item','addon','offering']
    route.choose(frame,result,['goal'])
    assert route.replans==1 and route.reason=='remaining_route_valid'


def test_priority_mode_keeps_perks_and_explicit_order():
    frame,result=scene(2)
    result.nodes[1].icon_id='other';result.nodes[2].state=NodeState.AVAILABLE
    result.nodes[2].type=NodeType.PERK;result.nodes[2].icon_id=None
    result.nodes[2].category_hint='perk';result.nodes[2].category_confidence=.95
    result.edges=[(0,1),(0,2)]
    route=PersistentRoute()
    assert route.choose(frame,result,['any_perk','other'],'smart').next_node==2
    assert route.choose(frame,result,['other','any_perk'],'smart').next_node==1
    assert route.reason=='priorities_changed'
    assert ShadowPlanner().observe(frame,result,['any_perk'])['center']=='excluded_by_priority_intent'


def test_map_context_invalidates_route_and_memory_clear():
    frame,result=scene();route=PersistentRoute()
    route.choose(frame,result,[])
    changed=replace(frame,window=replace(frame.window,pid=2))
    route.choose(changed,result,[])
    assert route.replans==2 and route.reason=='new_or_untrusted_map'
    from src.core.bloodweb_memory import BloodwebMemory
    memory=BloodwebMemory('killer','unknown');memory.route_plan=route.summary()
    memory.clear('test');assert memory.route_plan is None


def test_real_level49_persistent_vision_never_runs_full_after_seed(root,library,monkeypatch):
    from scripts.inspect_opening_failure_v49 import stored_frame
    from src.core.bloodweb_detector import BloodwebDetector
    folder=root/'debug/v49/live_session_bdf45c36'
    items={r['analysis_id']:r for r in json.loads((folder/'frames.json').read_text(encoding='utf-8'))}
    ids=['1791995966526131800_8a4c2a78','1791995968026885100_ddb2e932','1791995969246015200_3cb0d543']
    frames=[stored_frame(items[i]) for i in ids]
    priorities=json.loads((folder/'session.json').read_text(encoding='utf-8'))['authorization']['priorities']
    detector=BloodwebDetector(library,'killer','unknown');result=detector.analyze_live(frames[0])
    vision=PersistentVision();assert vision.begin(frames[0],result,detector,priorities,'smart',0)
    vision.plan(result,0,())
    def forbidden(*a,**k):raise AssertionError('unexpected full geometry/library work')
    monkeypatch.setattr(detector,'analyze_live',forbidden);monkeypatch.setattr(detector.matcher,'match',forbidden)
    monkeypatch.setattr('cv2.HoughCircles',forbidden)
    for frame in frames[1:]:
        compact=vision.analyze(frame,detector,priorities,'smart',0)
        assert compact.details['analysis_mode']=='PERSISTENT_MAP'
        assert vision.plan(compact,0,()).next_node is not None
    assert vision.quick_checks==2 and len(vision.route.queue)>5
    # Compact sampling also positively finds a real additional edge10–11.
    # Refresh the route from memory once, then retain it on the third frame.
    assert vision.route.replans==2 and vision.route.reuses==1


def test_compact_entity_clears_identity_and_an_unexplained_lost_line_falls_back(root,library,monkeypatch):
    from tests.test_v49_opening import opening_scene
    from src.core.fast_opening import OpeningFallback
    from src.core.bloodweb_detector import ring_fractions
    _,frame,result,detector=opening_scene(root,library)
    frame=replace(frame,level=20);result.level=20
    vision=PersistentVision();assert vision.begin(frame,result,detector,[],'strict',0)
    vision.plan(result,0,())
    victim=result.nodes[-1];old_dark=detector._dark_rim
    def fractions(hsv,x,y,r):
        return (0.,0.) if (x,y)==(victim.x,victim.y) else ring_fractions(hsv,x,y,r)
    monkeypatch.setattr('src.core.bloodweb_detector.ring_fractions',fractions)
    monkeypatch.setattr(detector,'_dark_rim',lambda gray,x,y,r:.8 if (x,y)==(victim.x,victim.y) else old_dark(gray,x,y,r))
    fresh=replace(frame,captured_at=frame.captured_at+.3)
    compact=vision.analyze(fresh,detector,[],'strict',0)
    node=next(n for n in compact.nodes if n.id==victim.id)
    assert node.state==NodeState.ABSORBED and node.icon_id is None
    record=next(r for r in detector.memory.records if tuple(r['point'])==(victim.x,victim.y))
    assert record['fingerprint'] is None and record['observations']==0 and record['match'].icon_id is None
    vision.plan(compact,0,());assert vision.route.reason=='entity_observed'
    monkeypatch.setattr(detector,'_edges',lambda *a,**k:[])
    with pytest.raises(OpeningFallback,match='connection_changed'):
        vision.analyze(replace(fresh,captured_at=fresh.captured_at+.3),detector,[],'strict',0)


def test_seven_injected_purchases_use_one_full_and_fourteen_red_confirmations(monkeypatch):
    from src.core.automation import Automation,Settings
    from tests.test_automation import Device
    frame,result=scene(8)
    # Device model: fully observed star, no graphics/Entity/physical input.
    result.edges=[(0,i) for i in range(1,9)]
    for n in result.nodes[1:]:n.state=NodeState.AVAILABLE;n.icon_id=None
    device=Device([result]);device.role='killer';device.character='unknown';device.memory=SimpleNamespace(generation=1)
    device.capture=lambda:replace(frame,captured_at=time.monotonic())
    full=[]
    def analyze(*a):full.append(1);return copy.deepcopy(result)
    device.analyze=analyze
    engine=Automation(device,device,device,[],Settings(dry_run=False,startup_delay=0,fast_early_levels=False,
        max_actions=7,poll_interval=.01,purchase_timeout=1,experimental_persistent=True),approved=True,hotkeys_ready=True)
    monkeypatch.setattr(engine.opening,'evidence',lambda *a:b'injected')
    monkeypatch.setattr(engine.opening,'parts',lambda *a:(None,(),None,None))
    reds=[]
    def compact(frame,detector,priorities,mode,actions):
        r=copy.deepcopy(result)
        for n in r.nodes[1:]:
            if n.id<=len(device.presses):n.state=NodeState.PURCHASED
        engine.opening.last_frame=frame;engine.opening.quick_checks+=1
        if len(device.presses)>actions:reds.append(len(device.presses))
        return r
    monkeypatch.setattr(engine.opening,'analyze',compact)
    engine.run()
    assert engine.actions==len(device.presses)==7 and not engine.pending_action
    assert len(full)==1 and engine.opening.route.replans==1
    assert len(reds)==14 and engine.opening.quick_checks==21


def test_shadow_is_read_only_and_graph_path_is_not_native_preview():
    frame,result=scene(3);before=copy.deepcopy(result.to_dict())
    decision=ShadowPlanner().observe(frame,result,['goal'])
    assert result.to_dict()==before and decision['dispatch'] is False
    assert decision['hypothetical_saved_commands']==2 and decision['executable_saved_commands']==0
    assert decision['chain']['game_preview']==[] and decision['chain']['total_bp'] is None
    assert 'native_preview_required' in decision['chain']['blockers']


def test_chain_rejects_branch_path_and_higher_priority_outside():
    frame,result=scene(3);result.nodes[1].icon_id='high'
    result.edges=[(0,1),(0,2),(0,3)]
    chain=propose_chain(frame,result,RoutePlan(2,[2,3],'goal'),['high','goal'],'m')
    assert 'graph_route_not_a_chain' in chain.blockers and 'higher_priority_outside_chain' in chain.blockers


@pytest.mark.parametrize('failure',['duplicate','path','map','price','provenance'])
def test_chain_preview_mismatch_never_authorizes_graph_route(failure):
    plan=ChainPlan('m',1,1,2,[1,2],blockers=['native_preview_required','price_unknown'])
    a=PreviewObservation('m',1,[1,2],4500,'p',[(0,1),(1,2)],'observed_preview');b=copy.deepcopy(a);b.stamp=2
    if failure=='duplicate':b.stamp=1
    if failure=='path':b.ordered_nodes=[2,1]
    if failure=='map':b.map_id='new'
    if failure=='price':b.total_bp=None
    if failure=='provenance':b.price_provenance=None
    plan.bind_preview([a,b]);assert plan.blockers and plan.total_bp is None


def test_preview_reads_highlight_graph_and_requires_reviewed_total_label(monkeypatch):
    frame,result=scene(2);frame.probe_only=True
    clean_frame=replace(frame,captured_at=frame.captured_at-.2,probe_only=False)
    monkeypatch.setattr('src.core.chain_plan.red_preview_edges',lambda *a:[(0,1),(1,2)])
    reader=SimpleNamespace(decode=lambda crop:4500)
    assert read_preview(frame,result,2,'m').reason=='preview_context_unproven'
    observation=read_preview(frame,result,2,'m',reader,PriceRegion((5,5,30,20),'0'*64,False),clean_frame)
    assert observation.ordered_nodes==[1,2] and observation.total_bp is None
    observation=read_preview(frame,result,2,'m',reader,PriceRegion((5,5,30,20),'0'*64,True),clean_frame)
    assert observation.total_bp==4500 and observation.reason=='observed_preview'
    monkeypatch.setattr('src.core.chain_plan.red_preview_edges',lambda *a:[(0,1),(1,2),(0,2)])
    assert read_preview(frame,result,2,'m',clean_frame=clean_frame).ordered_nodes==[]


def test_chain_outcomes_deduplicate_and_separate_entity_and_partial():
    frame,result=scene(3);plan=ChainPlan('m',1,1,3,[1,2,3],physical_points=[frame.to_screen(n.x,n.y) for n in result.nodes[1:]])
    evidence=ChainEvidence(frame,result,plan);after=bought(result,1);after.nodes[2].state=NodeState.ABSORBED
    fresh=replace(frame,captured_at=frame.captured_at+.2)
    assert not evidence.observe(fresh,after) and not evidence.observe(fresh,after)
    assert not evidence.confirmed and not evidence.entity
    evidence.observe(replace(fresh,captured_at=fresh.captured_at+.2),after)
    assert evidence.confirmed=={1} and evidence.entity=={2} and evidence.summary()['unconfirmed']==[3]
    assert evidence.summary()['automatic_retry'] is False
    changed=replace(fresh,captured_at=fresh.captured_at+.4,level=21)
    new=copy.deepcopy(result);new.level=21
    evidence.observe(changed,new);assert evidence.confirmed=={1} and evidence.reason=='level_or_screen_changed_unconfirmed'


def test_chain_missing_or_conflicting_identity_does_not_confirm():
    frame,result=scene(2);plan=ChainPlan('m',1,1,2,[1,2],physical_points=[frame.to_screen(n.x,n.y) for n in result.nodes[1:]])
    evidence=ChainEvidence(frame,result,plan);after=bought(bought(result,1),2);after.nodes[2].icon_id='conflict'
    for i in range(2):evidence.observe(replace(frame,captured_at=frame.captured_at+.2*(i+1)),after)
    assert evidence.confirmed=={1}


@pytest.mark.parametrize('failure',['approval','expired','queue','price','preview','bp','context','pending','stale','ordinary','geometry'])
def test_chain_guard_refusals_have_zero_device_calls_and_no_reservation(tmp_path,failure):
    engine,plan,permit,previous,latest=trial_setup(tmp_path)
    if failure=='approval':permit.approved=False
    if failure=='expired':permit.expires_at=time.time()-1
    if failure=='queue':permit.acknowledge_queue_cancellation_unverified=False
    if failure=='price':plan.total_bp=None
    if failure=='preview':plan.game_preview=[]
    if failure=='bp':engine.settings.bloodpoint_checks=True;latest[0].bloodpoints=None
    if failure=='context':permit.window_signature=()
    if failure=='pending':engine.pending_action=True
    if failure=='stale':latest[0].captured_at=time.monotonic()-3
    if failure=='ordinary':engine.settings.run_mode='continuous'
    if failure=='geometry':plan.physical_points[0]=(1,2)
    with pytest.raises(ScreenError):execute_short_chain(engine,plan,permit,previous,latest)
    assert not engine.mouse.presses and not permit.ledger.exists() and engine.attempts==0


def test_injected_chain_partial_is_charged_once_and_never_retried(tmp_path,monkeypatch):
    engine,plan,permit,previous,latest=trial_setup(tmp_path)
    frame,result=latest;after=bought(result,1);after.nodes[2].state=NodeState.ABSORBED
    stamp=[frame.captured_at]
    def observe(deadline):
        stamp[0]+=.03
        return replace(frame,captured_at=stamp[0],bloodpoints=28000),copy.deepcopy(after),RoutePlan()
    monkeypatch.setattr(engine,'recover_read',observe)
    outcome=execute_short_chain(engine,plan,permit,previous,latest)
    assert len(engine.mouse.presses)==engine.attempts==1 and engine.actions==1
    assert outcome['confirmed']==[1] and outcome['absorbed']==[2] and outcome['partial']
    assert not outcome['cancellation_verified'] and not outcome['automatic_retry']
    assert json.loads(permit.ledger.read_text())['used']==dict(commands=1,nodes=2,bp=4500)
    engine.attempted=[]
    with permit.locked(),pytest.raises(ScreenError,match='chain_budget_limit'):permit.reserve(plan)
    assert len(engine.mouse.presses)==1


def test_chain_stop_releases_and_keeps_unresolved_scene(tmp_path,monkeypatch):
    engine,plan,permit,previous,latest=trial_setup(tmp_path)
    def stopping(*args):engine.stop.set()
    monkeypatch.setattr(engine.mouse,'purchase',stopping)
    with pytest.raises(InterruptedError):execute_short_chain(engine,plan,permit,previous,latest)
    assert engine.mouse.releases>=2 and engine.pending_action and engine.scene_record['pending']['chain']['ordered_nodes']==[1,2]
    assert json.loads(permit.ledger.read_text())['used']['nodes']==2


def test_corrupt_chain_ledger_and_changed_scope_never_reset(tmp_path):
    engine,plan,permit,*_=trial_setup(tmp_path)
    permit.ledger.write_text('{',encoding='utf-8')
    with permit.locked(),pytest.raises(ScreenError,match='invalid_chain_ledger'):permit.reserve(plan)
    permit.ledger.unlink()
    with permit.locked():permit.reserve(plan)
    permit.max_nodes=3
    with permit.locked(),pytest.raises(ScreenError,match='chain_ledger_scope_mismatch'):permit.reserve(plan)


def test_chain_saved_scene_requires_each_point_twice_and_never_counts_tip_alone(tmp_path):
    from src.core.scene_memory import record_scene
    engine,plan,permit,previous,latest=trial_setup(tmp_path);frame,result=latest
    tip=result.nodes[2]
    memory=record_scene(frame,result,plan.physical_points,dict(point=list(plan.physical_points[-1]),radius=20,icon=tip.icon_id,chain=plan.summary()))
    engine.persisted_scene=copy.deepcopy(memory)
    after=copy.deepcopy(result);after.nodes[2].state=NodeState.PURCHASED
    assert not engine.recheck_saved_scene(replace(frame,captured_at=frame.captured_at+.1),after)
    engine.saved_chain_retry=(memory['pending']['operation_id'],frame.captured_at-5,0)
    # 14.11: an unprovable click is no longer an immediate pause; it is watched read-only, then retired with no credit.
    assert engine.recheck_saved_scene(replace(frame,captured_at=frame.captured_at+.2),after) is False
    assert engine.persisted_scene['pending'] is not None and engine.actions==0
    after.nodes[1].state=NodeState.PURCHASED
    assert not engine.recheck_saved_scene(replace(frame,captured_at=frame.captured_at+.3),after)
    assert engine.recheck_saved_scene(replace(frame,captured_at=frame.captured_at+.4),after)
    assert engine.persisted_scene is None and engine.actions==0


@pytest.mark.parametrize('failure',['extra_purchase','bp_exceeded'])
def test_chain_unexpected_spend_or_node_stops_and_blocks_saved_scene(tmp_path,monkeypatch,failure):
    engine,plan,permit,previous,latest=trial_setup(tmp_path);frame,result=latest
    if failure=='extra_purchase':
        result.nodes.append(Node(3,260,100,20,NodeState.AVAILABLE,NodeType.ADDON,state_confidence=.95,geometry_confidence=.95))
        previous=(previous[0],copy.deepcopy(result));plan.map_id=map_key(frame,result,0)
    after=bought(bought(result,1),2)
    if failure=='extra_purchase':after.nodes[-1].state=NodeState.PURCHASED
    def observing(deadline):
        return replace(frame,captured_at=frame.captured_at+.1,bloodpoints=24000 if failure=='bp_exceeded' else 26000),after,RoutePlan()
    monkeypatch.setattr(engine,'recover_read',observing)
    reason='chain_unexpected_purchase' if failure=='extra_purchase' else 'chain_bp_exceeded'
    with pytest.raises(ScreenError,match=reason):execute_short_chain(engine,plan,permit,previous,latest)
    assert engine.stop.is_set() and engine.pending_action and len(engine.mouse.presses)==1
    assert engine.scene_record['pending']['chain_blocked_reason']==reason
    from src.core.scene_memory import chain_state_signature
    assert chain_state_signature(engine.scene_record,frame,after) is None


def test_experiments_default_off_and_chain_not_dispatched_by_run():
    from src.core.automation import Settings,Automation
    assert not Settings().experimental_persistent and not Settings().shadow_planner
    for key in ('experimental_persistent','shadow_planner'):
        with pytest.raises(ValueError):Settings(**{key:1}).validate()
    import inspect
    assert 'execute_short_chain' not in inspect.getsource(Automation.run)
