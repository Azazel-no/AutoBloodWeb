"""No native input: prepared unlocks, capture prohibition and interrupted ledger."""
import copy,time
from types import SimpleNamespace
import pytest
from tests.test_automation import Device,engine,web
from src.core.models import Node,NodeState,NodeType
from src.core.path_planner import RoutePlan
from src.core.turbo_burst import prepare,dispatch
from src.core.screen_manager import ScreenError


def setup():
    r=web();r.level=20;r.level_confidence=.99
    r.nodes[0].x=20;r.nodes[0].y=20
    r.nodes=r.nodes[:1];r.edges=[]
    for i in range(1,7):
        r.nodes.append(Node(i,70+i*35,80+i*35,20,NodeState.AVAILABLE if i==1 else NodeState.LOCKED,
            NodeType.ITEM,'item'+str(i),confidence=.95,state_confidence=.95,geometry_confidence=.95,
            contour_score=.9,rarity='brown'))
        r.edges.append((i-1,i))
    d=Device([r]);e=engine(d,dry_run=False,turbo_burst=True,max_actions=10);e.priorities=[]
    d.library=SimpleNamespace(event_metadata={});d.matcher=SimpleNamespace(by_id={n.icon_id:object() for n in r.nodes if n.icon_id})
    e.runtime_deadline=time.monotonic()+5;e.opening=SimpleNamespace(expected_purchases=set())
    f=d.capture();f.level=20;f.bloodpoints=50000;return e,d,f,r


def test_five_inputs_use_prepared_unlocks_and_only_post_batch_captures(tmp_path):
    e,d,f,r=setup();captures=[]
    from src.core.action_outcomes import ActionOutcomes
    e.outcome_journal=ActionOutcomes(tmp_path/'outcomes.json')
    def recover(*a,**k):
        assert len(d.presses)==5 and not e.burst_inputs_active
        captures.append(1);fresh=d.capture();after=copy.deepcopy(r)
        for n in after.nodes[1:6]:n.state=NodeState.PURCHASED
        return fresh,after,RoutePlan()
    e.recover_read=recover
    assert dispatch(e,f,r) is not None
    assert len(d.presses)==e.actions==5 and len(captures)==2 and not e.pending_action
    assert len(set(p[0] for p in d.presses))==5
    assert e.scene_record['last_outcome']['confirmed_nodes']==5


@pytest.mark.parametrize('fault',['perk','already_bought','uncertain','few','unknown','budget'])
def test_unsafe_burst_rejects_without_input(fault):
    e,d,f,r=setup()
    if fault=='perk':r.nodes[1].type=NodeType.PERK  # First target; a later perk closes the burst.
    if fault=='already_bought':r.nodes[1].state=NodeState.PURCHASED
    if fault=='uncertain':r.nodes[3].state=NodeState.UNCERTAIN
    if fault=='few':r.nodes.pop()
    if fault=='unknown':r.nodes[3].icon_id=None
    if fault=='budget':e.settings.max_input_commands=4
    assert prepare(e,f,r) is None and not d.presses


def test_perk_closes_the_burst_as_its_last_target():
    e,d,f,r=setup()
    from src.core.priority_targets import PERK_TARGET
    e.priorities=[PERK_TARGET]
    perk=r.nodes[3];perk.type=NodeType.PERK;perk.category_hint='perk';perk.category_confidence=.99
    steps=prepare(e,f,r)
    assert [n.id for _,n in steps]==[1,2,3]


def test_locked_ring_capped_near_070_does_not_reject_the_burst():
    e,d,f,r=setup()
    for n in r.nodes[2:]:n.geometry_confidence=.673
    assert len(prepare(e,f,r))==5
    r.nodes[3].geometry_confidence=.64
    assert prepare(e,f,r) is None  # Below the bound the node is not purchasable at all.


def test_capture_api_is_forbidden_in_burst():
    e,d,f,r=setup();e.burst_inputs_active=True
    with pytest.raises(ScreenError,match='burst_capture_forbidden'):e.capture_frame()
    assert d.index==1


def test_empty_fast_level_can_skip_unknown_filler_for_five_known_nodes():
    e,d,f,r=setup();r.nodes[1].icon_id=None;r.nodes[1].type=NodeType.UNKNOWN
    r.nodes[2].state=NodeState.AVAILABLE;r.edges.append((0,2))
    steps=prepare(e,f,r)
    assert [n.id for _,n in steps]==[2,3,4,5,6]
    assert r.nodes[1].icon_id is None
    # A real known priority whose only route starts at Unknown remains blocked.
    e.priorities=['item6'];r.nodes[2].state=NodeState.LOCKED;r.edges.remove((0,2))
    assert prepare(e,f,r) is None


def test_focus_loss_after_first_input_keeps_only_actual_attempt_pending():
    e,d,f,r=setup();purchase=d.purchase
    def lose_focus(*a):purchase(*a);d.focus=False
    d.purchase=lose_focus
    with pytest.raises(ScreenError,match='game_focus_lost'):dispatch(e,f,r)
    assert len(d.presses)==1 and e.pending_action and d.releases
    assert not e.burst_inputs_active
    assert len(e.scene_record['pending']['batch']['physical_points'])==1


def test_budget_can_bind_knight_without_accepting_unrelated_character(tmp_path):
    from src.core.series_budget import SeriesBudget
    a=dict(role='killer',character='knight',max_bp=1000000,allow_prestige=False,allow_chain=True,max_nodes=300)
    assert SeriesBudget(tmp_path/'ledger.json',a).max_nodes==300
    with pytest.raises(ValueError):SeriesBudget(tmp_path/'other.json',dict(a,character=''))


def test_fast_level_native_chain_is_a_hypothesis_and_preserves_priorities():
    from src.core.chain_plan import native_candidate_route,propose_chain
    e,d,f,r=setup()
    route=native_candidate_route(r,[],'smart')
    assert route.route==[1,2,3]
    chain=propose_chain(f,r,route,[],'map')
    assert 'native_preview_required' in chain.blockers and chain.total_bp is None
    priority=native_candidate_route(r,['item1'],'strict')
    assert priority.route==[1]
    r.nodes[1].type=NodeType.PERK
    assert native_candidate_route(r,[],'smart').route==[1]


def test_long_native_chain_needs_explicit_item_cap_and_all_node_evidence(tmp_path):
    from src.core.chain_plan import native_candidate_route,propose_chain,ChainEvidence
    from src.core.chain_trial import ChainPermit
    from src.core.scene_memory import record_scene,chain_state_signature
    e,d,f,r=setup()
    route=native_candidate_route(r,[],'smart',max_nodes=5)
    assert route.route==[1,2,3,4,5]
    chain=propose_chain(f,r,route,[],'map',max_nodes=5)
    assert chain.expected_items==5 and chain.total_bp is None
    permit=ChainPermit('scope',tmp_path/'ledger.json',10,20,50000,time.time()+60,f.window.signature,(),True,True)
    with pytest.raises(ScreenError,match='chain_item_limit'):permit.reserve(chain)
    assert not permit.ledger.exists()
    chain.total_bp=12500;permit.max_chain_items=5
    assert permit.reserve(chain)['used']==dict(commands=1,nodes=5,bp=12500)
    scene=record_scene(f,r,chain.physical_points,dict(point=chain.physical_points[-1],radius=20,chain=chain.summary()))
    after=copy.deepcopy(r)
    for n in after.nodes[1:6]:n.state=NodeState.PURCHASED
    evidence=ChainEvidence(f,r,chain)
    first=d.capture();first.level=20
    assert not evidence.observe(first,after)
    assert chain_state_signature(scene,first,after) is not None
    second=d.capture();second.level=20
    assert evidence.observe(second,after) and len(evidence.confirmed)==5


def test_filler_chain_uses_fewer_commands_after_priorities_are_absent():
    from src.core.chain_plan import native_candidate_route
    e,d,f,r=setup()
    assert native_candidate_route(r,['absent_goal'],'strict',max_nodes=5).route==[1,2,3,4,5]
    assert native_candidate_route(r,['item1'],'strict',max_nodes=5).route==[1]


def test_native_five_node_executor_resolves_one_input_and_all_five_pending_points(tmp_path,monkeypatch):
    from dataclasses import replace
    from tests.test_v51_hybrid import trial_setup,scene
    from src.core.chain_plan import ChainPlan
    from src.core.chain_trial import execute_short_chain
    from src.core.persistent_route import map_key
    from src.core.action_outcomes import ActionOutcomes
    e,_,permit,_,_=trial_setup(tmp_path)
    frame,result=scene(5);frame.captured_at=time.monotonic()-.05
    e.outcome_journal=ActionOutcomes(tmp_path/'outcomes.json')
    plan=ChainPlan(map_key(frame,result,0),1,1,5,[1,2,3,4,5],game_preview=[1,2,3,4,5],total_bp=12500,
        priorities=['goal'],preview_stamps=[frame.captured_at-.2,frame.captured_at-.1],
        physical_points=[frame.to_screen(n.x,n.y) for n in result.nodes[1:]],price_provenance='0'*64)
    permit.max_chain_items=5;permit.max_nodes=5;permit.max_bp=12500
    e.opening=SimpleNamespace(active=True,persistent=True,expected_purchases={99})
    after=copy.deepcopy(result)
    for n in after.nodes[1:]:n.state=NodeState.PURCHASED
    observations=[]
    def observe(deadline):
        assert e.opening.expected_purchases=={1,2,3,4,5,99}
        fresh=replace(frame,captured_at=frame.captured_at+.03*(len(observations)+1),bloodpoints=17500)
        observations.append((fresh,copy.deepcopy(after),RoutePlan()))
        e.recent_frames.append(observations[-1]);return observations[-1]
    monkeypatch.setattr(e,'recover_read',observe)
    outcome=execute_short_chain(e,plan,permit,(replace(frame,captured_at=frame.captured_at-.03),copy.deepcopy(result)),(frame,result))
    assert len(e.mouse.presses)==1 and len(outcome['confirmed'])==e.actions==5 and not e.pending_action
    assert e.scene_record['last_outcome']['confirmed_nodes']==5


def test_chain_missing_ring_can_recrop_once_without_repeating_input(tmp_path,monkeypatch):
    from dataclasses import replace
    from tests.test_v51_hybrid import trial_setup,bought
    from src.core.chain_trial import execute_short_chain
    e,plan,permit,previous,latest=trial_setup(tmp_path);frame,result=latest
    after=bought(bought(result,1),2);reads=[];recrops=[]
    e.screen.reset_region=lambda:recrops.append(True)
    def observe(deadline):
        reads.append(True);current=copy.deepcopy(after)
        if not recrops:current.nodes=[n for n in current.nodes if n.id!=1]
        fresh=replace(frame,captured_at=frame.captured_at+len(reads)*.03,bloodpoints=25500)
        e.recent_frames.append((fresh,current,RoutePlan()));return fresh,current,RoutePlan()
    monkeypatch.setattr(e,'recover_read',observe)
    outcome=execute_short_chain(e,plan,permit,previous,latest)
    assert recrops==[True] and len(reads)==5 and len(e.mouse.presses)==1
    assert outcome['confirmed']==[1,2] and not e.pending_action


@pytest.mark.parametrize('change',['six_pixels','nine_pixels','known_conflict'])
def test_old_owned_ring_jitter_is_not_new_purchase_but_conflicts_still_stop(tmp_path,change):
    from dataclasses import replace
    from tests.test_v51_hybrid import trial_setup,bought
    from src.core.chain_plan import ChainEvidence
    e,plan,permit,previous,latest=trial_setup(tmp_path);frame,result=latest
    result.nodes.append(Node(3,280,100,28,NodeState.PURCHASED,NodeType.ADDON,'owned',state_confidence=.93))
    evidence=ChainEvidence(frame,result,plan);after=bought(bought(result,1),2)
    after.nodes[3].state=NodeState.PURCHASED
    after.nodes[3].x+=6 if change!='nine_pixels' else 9;after.nodes[3].y+=2
    if change=='known_conflict':after.nodes[3].icon_id='other'
    fresh=replace(frame,captured_at=frame.captured_at+.1)
    if change=='six_pixels':
        assert not evidence.observe(fresh,after) and evidence.unexpected==[]
        assert evidence.observe(replace(fresh,captured_at=fresh.captured_at+.1),after)
        assert evidence.confirmed=={1,2}
    else:
        with pytest.raises(ValueError,match='chain_unexpected_purchase'):evidence.observe(fresh,after)


def test_saved_chain_missing_ring_gets_bounded_read_only_retry_then_two_real_states(tmp_path):
    from dataclasses import replace
    from tests.test_v51_hybrid import trial_setup,bought
    from src.core.scene_memory import record_scene
    e,plan,permit,previous,latest=trial_setup(tmp_path);frame,result=latest
    e.persisted_scene=record_scene(frame,result,plan.physical_points,dict(point=list(plan.physical_points[-1]),radius=20,chain=plan.summary()))
    e.settings.purchase_timeout=4
    absent=bought(result,1);absent.nodes=absent.nodes[:2]
    for offset in (.1,.2):assert not e.recheck_saved_scene(replace(frame,captured_at=frame.captured_at+offset),absent)
    assert not e.mouse.presses and e.persisted_scene['pending']
    after=bought(bought(result,1),2)
    assert not e.recheck_saved_scene(replace(frame,captured_at=frame.captured_at+.3),after)
    assert e.recheck_saved_scene(replace(frame,captured_at=frame.captured_at+.4),after)
    assert not e.mouse.presses and e.persisted_scene is None


def test_continue_variants_share_one_fresh_mask(root,monkeypatch):
    from src.core import video_ui
    from src.utils.images import read_image
    original=video_ui.text_mask;calls=[]
    # Warm verified reference caches before counting capture masks.
    video_ui.references(str(root),'continue');video_ui.continue_variant(str(root))
    def counted(image):calls.append(image.shape);return original(image)
    monkeypatch.setattr(video_ui,'text_mask',counted)
    proof=video_ui.certificate(read_image(root/'debug/v57/knight_20261018/continue_iii_1.png'),root,'continue')
    assert proof and len(calls)==1


def test_native_capture_preserves_original_bgr_bytes_without_bgra_alias(monkeypatch):
    import numpy as np
    from contextlib import nullcontext
    from src.core.screen_manager import WindowsBackend,Rect,pixel_variation
    pixels=np.arange(24*32*4,dtype=np.uint8).reshape(24,32,4);calls=[]
    class MSS:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def grab(self,rect):calls.append(rect);return pixels
    monkeypatch.setattr('src.core.screen_manager.mss.MSS',MSS)
    backend=WindowsBackend.__new__(WindowsBackend);backend.pixels=nullcontext
    result,provider=backend.grab(Rect(4,5,32,24))
    assert provider=='mss' and np.array_equal(result,pixels[:,:,:3]) and not np.shares_memory(result,pixels)
    assert calls==[dict(left=4,top=5,width=32,height=24)]
    # Include flat colour: global BGR variation differs from per-channel noise.
    for image in (result,np.zeros((10,10,3),np.uint8),np.full((10,10,3),[0,0,255],np.uint8),
                  np.tile(np.array([0,4],np.uint8),150).reshape(10,10,3)):
        assert pixel_variation(image)==pytest.approx(float(image.std()),abs=1e-11)
        assert (pixel_variation(image)>2)==(image.std()>2)


def test_native_knight_price_row_rejects_scenery_above_baseline(root):
    import hashlib,json
    from scripts.native_price_v55 import NativePriceLocator
    from src.core.resources import ResourceReader
    from src.utils.images import read_image
    path=root/'debug/v57/knight_20261018/price_review_005/node_3_3.png'
    assert hashlib.sha256(path.read_bytes()).hexdigest()=='2aec7d30ab8d74d4d7db9ad2f7520e481121a3191707c8abb7eb468ac0df558f'
    reader=NativePriceLocator(root,json.loads((root/'data/native_price_locator.json').read_text()),ResourceReader(root))
    assert reader.locate(read_image(path),(579,624))['value']==2500


def test_native_continue_iii_matches_independent_pixels_without_lower_threshold(root):
    from src.core.video_ui import certificate
    from src.utils.images import read_image
    proof=certificate(read_image(root/'debug/v57/knight_20261018/continue_iii_1.png'),root,'continue')
    assert proof['regions']['continue']==(686,596,101,20) and proof['scores']['continue']>=.96
    assert certificate(read_image(root/'debug/v57/knight_20261018/price_review_014/clean.png'),root,'continue') is None


@pytest.mark.parametrize('submitted',[False,True])
def test_center_failure_clears_pending_only_before_native_down_api(submitted):
    from src.core.early_unlock import purchase_level
    r=web();r.level=4;r.level_confidence=.99;r.nodes[0].geometry_confidence=.95
    d=Device([r]);d.down_attempt_sequence=0;d.parking_point=lambda f:None
    e=engine(d,dry_run=False);f=d.capture()
    e.automatic_proof=(f.window.signature,4,f.to_screen(150,200),time.monotonic())
    def purchase(*a,**k):
        if submitted:d.down_attempt_sequence+=1
        raise ScreenError('chain_map_changed')
    d.purchase=purchase
    with pytest.raises(ScreenError,match='chain_map_changed'):purchase_level(e,f,r)
    assert e.pending_action is submitted and bool(e.scene_record.get('pending')) is submitted
    assert bool(e.attempted) is submitted


@pytest.mark.parametrize('shift',[6,9])
def test_canonical_and_saved_chain_use_same_unique_ring_tolerance(tmp_path,shift):
    from dataclasses import replace
    from src.core.scene_memory import record_scene,chain_state_signature
    from src.core.action_outcomes import ActionOutcomes
    r=web();r.level=20;r.nodes[1].radius=40
    r.nodes.append(replace(r.nodes[1],id=2,x=330))
    f=Device([r]).capture();points=[list(f.to_screen(n.x,n.y)) for n in r.nodes[1:]]
    memory=record_scene(f,r,[],dict(point=points[-1],radius=40,chain={'physical_points':points}))
    after=copy.deepcopy(r)
    for n in after.nodes[1:]:n.state=NodeState.PURCHASED
    after.nodes[1].y+=shift
    observations=[(replace(f,captured_at=f.captured_at+i),after) for i in (1,2)]
    journal=ActionOutcomes(tmp_path/'outcomes.json')
    if shift==6:
        assert chain_state_signature(memory,observations[-1][0],after)==('purchased','purchased')
        assert journal.reconcile(memory,observations,'native_ring_regression')['confirmed_nodes']==2
    else:
        # A red ring outside every path and owned tolerance is an off-path
        # purchase, as in ChainEvidence: critical, not merely missing evidence.
        assert chain_state_signature(memory,observations[-1][0],after) is None
        with pytest.raises(ScreenError,match='chain_unexpected_purchase'):journal.reconcile(memory,observations,'negative')
        assert not journal.path.exists()


def chain_outcome_setup(tmp_path):
    from dataclasses import replace
    from src.core.scene_memory import record_scene
    from src.core.action_outcomes import ActionOutcomes
    r=web();r.level=20;r.nodes[1].radius=40
    r.nodes.append(replace(r.nodes[1],id=2,x=330))
    f=Device([r]).capture();points=[list(f.to_screen(n.x,n.y)) for n in r.nodes[1:]]
    memory=record_scene(f,r,[],dict(point=points[-1],radius=40,chain={'physical_points':points}))
    after=copy.deepcopy(r)
    for n in after.nodes[1:]:n.state=NodeState.PURCHASED
    return f,memory,after,ActionOutcomes(tmp_path/'outcomes.json')


def test_missing_path_ring_without_a_stray_purchase_is_only_missing_evidence(tmp_path):
    from dataclasses import replace
    f,memory,after,journal=chain_outcome_setup(tmp_path)
    after.nodes.pop(1)
    observations=[(replace(f,captured_at=f.captured_at+i),after) for i in (1,2)]
    with pytest.raises(ScreenError,match='outcome_evidence_required'):journal.reconcile(memory,observations,'negative')
    assert not journal.path.exists()


def test_off_path_purchase_blocks_credit_even_when_every_path_point_is_resolved(tmp_path):
    from dataclasses import replace
    from src.core.scene_memory import chain_state_signature
    f,memory,after,journal=chain_outcome_setup(tmp_path)
    after.nodes.append(replace(after.nodes[1],id=9,x=60,y=60))
    observations=[(replace(f,captured_at=f.captured_at+i),after) for i in (1,2)]
    # The saved-scene signature checks path points only, so this journal check
    # is what stops the stray purchase from being credited on that route.
    assert chain_state_signature(memory,observations[-1][0],after)==('purchased','purchased')
    with pytest.raises(ScreenError,match='chain_unexpected_purchase'):journal.reconcile(memory,observations,'negative')
    assert not journal.path.exists()


@pytest.mark.parametrize('change',[None,'same_level','old_point_reused','not_mostly_owned','batch','state','focus'])
def test_absent_old_single_retires_only_a_proven_new_level(change):
    from dataclasses import replace
    from src.core.scene_memory import record_scene
    from src.core.level_transition import absent_single_on_next_level
    old=web();old.level=17;old.level_confidence=.99
    for i in range(2,8):old.nodes.append(replace(old.nodes[1],id=i,x=20+i*25,y=300,state=NodeState.PURCHASED))
    new=web();new.level=18;new.level_confidence=.99
    new.nodes=[new.nodes[0]]+[Node(i,40+i*35,60,15,NodeState.AVAILABLE,NodeType.UNKNOWN,
        state_confidence=.9,geometry_confidence=.9) for i in range(1,7)]
    f=Device([new]).capture();memory=record_scene(f,old,[],dict(point=list(f.to_screen(230,200)),radius=20))
    if change=='same_level':new.level=17
    if change=='old_point_reused':new.nodes[1].x=230;new.nodes[1].y=200
    if change=='not_mostly_owned':memory['nodes'][2]['state']='locked';memory['nodes'][3]['state']='locked'
    if change=='batch':memory['pending']['batch']={'physical_points':[[1,2]]}
    if change=='state':new.nodes[1].state=NodeState.PURCHASED
    if change=='focus':f.window=replace(f.window,foreground=False)
    assert absent_single_on_next_level(memory,f,new)==(change is None)


def test_native_red_outer_ring_is_rediscovered_from_same_capture(root,library):
    import json,hashlib
    from src.utils.images import read_image
    from src.core.screen_manager import CapturedFrame,WindowInfo,Rect
    from src.core.bloodweb_detector import BloodwebDetector
    folder=root/'debug/v57/knight_20261018/chain_probe_016'
    manifest=json.loads((folder/'probe.json').read_text(encoding='utf-8'));m=manifest['clean_frame'];w=m['window']
    assert hashlib.sha256((folder/'clean.png').read_bytes()).hexdigest()==manifest['clean']['sha256']
    window=WindowInfo(w['hwnd'],w['title'],Rect(**w['client']),Rect(**w['monitor']),w['dpi'],w['foreground'],w['pid'])
    raw=read_image(folder/'clean.png');roi=Rect(**m['roi'])
    f=CapturedFrame(raw[roi.y:roi.y+roi.height,roi.x:roi.x+roi.width].copy(),window,roi,m['captured_at'],
        bloodpoints=m['bloodpoints'],raw_image=raw,visibility=m['visibility'],level=13,level_confidence=.99)
    result=BloodwebDetector(library,'killer','knight').analyze_live(f)
    target=[n for n in result.nodes if abs(f.to_screen(n.x,n.y)[0]-685)<8 and abs(f.to_screen(n.x,n.y)[1]-915)<8]
    assert len(target)==1 and target[0].state==NodeState.PURCHASED and target[0].state_confidence>=.8
    assert result.details['same_capture_native_geometry_candidates']>0


def test_native_fire_cannot_become_off_preview_purchased_node(root,library):
    import json,hashlib
    from src.utils.images import read_image
    from src.core.screen_manager import CapturedFrame,WindowInfo,Rect
    from src.core.bloodweb_detector import BloodwebDetector
    folder=root/'debug/v57/knight_20261018_1101'
    path=folder/'unexpected_1035_662/1792310869207058200_3810639e/raw.png'
    assert hashlib.sha256(path.read_bytes()).hexdigest()=='936ff49f6f63633245aad9d8fd904d554d8979fd99d2854f26eb290a35de2d8c'
    m=json.loads((folder/'chain_probe_005/probe.json').read_text())['clean_frame'];w=m['window']
    window=WindowInfo(w['hwnd'],w['title'],Rect(**w['client']),Rect(**w['monitor']),w['dpi'],True,w['pid'])
    raw=read_image(path);roi=Rect(**m['roi'])
    frame=CapturedFrame(raw[roi.y:roi.y+roi.height,roi.x:roi.x+roi.width].copy(),window,roi,m['captured_at'],
        raw_image=raw,visibility=m['visibility'],level=30,level_confidence=.99)
    result=BloodwebDetector(library,'killer','knight').analyze_live(frame)
    assert not any(abs(frame.to_screen(n.x,n.y)[0]-1035)<10 and abs(frame.to_screen(n.x,n.y)[1]-662)<10 for n in result.nodes)
    assert any(abs(frame.to_screen(n.x,n.y)[0]-747)<8 and abs(frame.to_screen(n.x,n.y)[1]-328)<8 and n.state==NodeState.PURCHASED for n in result.nodes)


def test_real_price_survey_keeps_two_matching_readings_across_unknown_frames(root,tmp_path,monkeypatch):
    import json,threading
    from dataclasses import replace
    from scripts import live_test_v54 as survey
    from scripts.native_price_v55 import NativePriceLocator
    from src.core.resources import ResourceReader
    from src.core.screen_manager import CapturedFrame,WindowInfo,Rect
    from src.utils.images import read_image
    from tests.test_v51_hybrid import scene
    folder=root/'debug/v57/knight_20261018_1101/price_review_006'
    pixels=[read_image(folder/f'node_10_{i}.png') for i in range(4)]
    locator=NativePriceLocator(root,json.loads((root/'data/native_price_locator.json').read_text()),ResourceReader(root))
    assert [bool(locator.locate(p,(684,210))) for p in pixels]==[True,False,False,True]
    _,result=scene(2);result.nodes[1].x=684;result.nodes[1].y=210
    window=WindowInfo(1,'injected stand',Rect(0,0,1920,1080),Rect(0,0,1920,1080),96,True,1)
    frame=CapturedFrame(pixels[0],window,Rect(0,0,1920,1080),time.monotonic(),raw_image=pixels[0],level=20)
    captures=iter(enumerate(pixels));moves=[]
    def capture(_):
        i,image=next(captures);return replace(frame,image=image,captured_at=frame.captured_at+i+.1)
    class SurveyFinished(Exception):pass
    def trace(stage,**kwargs):
        if stage=='native_price_review_required':raise SurveyFinished
    engine=SimpleNamespace(state='ready',event=lambda *a:None,wait=lambda *a:None,
        recover_read=lambda **kw:(frame,result,RoutePlan(1,[1])),runtime_deadline=time.monotonic()+30,
        gate=lambda:None,check_held=lambda f,x,y:f.to_screen(x,y),stop=threading.Event(),
        mouse=SimpleNamespace(park=lambda p,*a:moves.append(p),release=lambda:None),
        screen=SimpleNamespace(capture_overlay=capture,parking_point=lambda f:(8,8)),native_price_locator=locator,trace=trace)
    monkeypatch.setattr(survey,'ROOT',tmp_path)
    with pytest.raises(SurveyFinished):survey.NativeReview(engine,tmp_path).prepare(frame,result,RoutePlan(1,[1]),[result.nodes[1]],'single')
    request=json.loads((tmp_path/'price_review_001/request.json').read_text())
    assert [p['native_value'] for p in request['targets'][0]['frames']]==[2500,2500]
    assert request['targets'][0]['frames'][0]['stamp']<request['targets'][0]['frames'][1]['stamp']


@pytest.mark.parametrize('delay',[.5,2.0])
def test_deferred_mixed_batch_credits_only_twice_red_and_unblocks_only_proven_misses(tmp_path,delay):
    from dataclasses import replace
    from src.core.action_outcomes import ActionOutcomes
    from src.core.scene_memory import record_scene
    e,d,f,r=setup();e.outcome_journal=ActionOutcomes(tmp_path/'outcomes.json')
    points=[list(f.to_screen(n.x,n.y)) for n in r.nodes[1:4]]
    e.scene_record=record_scene(f,r,points,dict(point=points[-1],batch=dict(physical_points=points,
        deferred_verification=True,inputs_completed_at=f.captured_at)))
    e.attempted=[tuple(p) for p in points];after=copy.deepcopy(r)
    after.nodes[1].state=NodeState.PURCHASED;after.nodes[2].state=NodeState.AVAILABLE
    observations=[(replace(f,captured_at=f.captured_at+delay+i*.1),after) for i in range(2)]
    if delay<1.5:
        with pytest.raises(ScreenError,match='outcome_evidence_required'):e.reconcile_operation(observations,'batch')
        assert len(e.attempted)==3
    else:
        row=e.reconcile_operation(observations,'batch')
        assert row['confirmed_nodes']==1 and row['missed_nodes']==2 and not row['authorizes_input']
        assert e.attempted==[tuple(points[0])]


def test_native_automatic_hover_is_parked_before_any_full_center_search(root,library,monkeypatch):
    from tests.test_screen_manager import Backend
    from src.core.screen_manager import ScreenManager,WindowInfo,Rect
    from src.core.bloodweb_detector import BloodwebDetector
    from src.utils.images import read_image
    raw=read_image(root/'debug/v57/knight_20261018/inspection_current15.png')
    w=WindowInfo(1,'DBD',Rect(0,0,1920,1080),Rect(0,0,1920,1080),96,True)
    screen=ScreenManager(Backend(w,raw),detector=BloodwebDetector(library,'killer','knight'))
    monkeypatch.setattr('src.core.bloodweb_detector.find_center',lambda *a,**k:pytest.fail('expensive full center search on recognized hover'))
    with pytest.raises(ScreenError,match='item_tooltip'):screen.capture()
    moves=[];mouse=SimpleNamespace(release=lambda:None,park=lambda point,stop,guard:moves.append(point))
    assert screen.park_hover(mouse,None,lambda:None) and moves==[(8,540)]


def test_simulation_copy_ignores_attributes_the_gui_attaches():
    """Live 2026-11-08 19:26: the GUI thread added attributes during deepcopy and the run ended."""
    r=web();r.renderer=object();r.raw_image=object()
    copied=r.simulation_copy()
    assert not hasattr(copied,'renderer') and not hasattr(copied,'raw_image')
    copied.nodes[0].state=NodeState.PURCHASED
    assert r.nodes[0].state!=NodeState.PURCHASED
