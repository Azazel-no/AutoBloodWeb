"""Real-pixel equivalence, frame ownership, transitions and deferred proof IO."""
import copy,json,time,threading
from dataclasses import replace
from pathlib import Path
import numpy as np
import pytest


def native_manager(root,library):
    from src.core.bloodweb_detector import BloodwebDetector
    from src.core.screen_manager import ScreenManager,WindowInfo,Rect
    from src.utils.images import read_image
    class Backend:
        captures=0
        pixels=read_image(root/'debug/v58/nicolas_20261018_181723/initial_native.png')
        window=WindowInfo(1,'Native fixture',Rect(0,0,1920,1080),Rect(0,0,1920,1080),96,True,1)
        covers=[]
        def info(self):return self.window
        def grab(self,region):self.captures+=1;return self.pixels.copy(),'saved_native'
        def occlusions(self,window):return self.covers
    backend=Backend();detector=BloodwebDetector(library,'survivor');return backend,detector,ScreenManager(backend,detector)


@pytest.mark.parametrize('kind',['continue','tutorial_unpredictability','reward','automatic'])
def test_candidate_ui_matches_original_on_independent_real_screens(root,kind):
    from src.core.video_ui import certificate
    from src.utils.images import read_image
    manifest=json.loads((root/'assets/ui/video_v54/manifest.json').read_text())
    sources=[root/manifest[k]['source'] for k in ('tutorial_unpredictability','reward','automatic')]
    sources+=[root/'tests/fixtures/v54/continue_perk_slot_ii.png',root/'debug/v58/nicolas_20261018_181723/final_native.png']
    for source in sources:
        image=read_image(source);a=certificate(image,root,kind);b=certificate(image,root,kind,fast=True)
        assert bool(a)==bool(b),(kind,source)
        if a:assert a['regions']==b['regions']


@pytest.mark.parametrize('position',[(0,0),(1750,1030),(400,400)])
@pytest.mark.parametrize('ambiguous',[False,True])
def test_whole_client_continue_and_ambiguity_are_preserved(root,position,ambiguous):
    from src.core.video_ui import certificate
    from src.utils.images import read_image
    # Explicit relocation injection, not generated game evidence.
    word=read_image(root/'assets/ui/video_v54/continue_word.png');h,w=word.shape[:2]
    pixels=np.zeros((1080,1920,3),np.uint8);x,y=position;pixels[y:y+h,x:x+w]=word
    if ambiguous:pixels[700:700+h,30:30+w]=word
    a=certificate(pixels,root,'continue');b=certificate(pixels,root,'continue',fast=True)
    assert bool(a)==bool(b)==(not ambiguous)
    if a:assert a['regions']==b['regions']


def test_same_capture_reuses_ui_and_center_without_copying_or_another_grab(root,library,monkeypatch):
    from src.core.frame_pipeline import visuals_for
    backend,detector,manager=native_manager(root,library)
    frame=manager.capture();context=visuals_for(frame)
    assert context and np.shares_memory(frame.image,frame.raw_image) and not frame.image.flags.writeable
    assert context._mask is None and 'integrals' not in context.workspace
    def forbidden(*args,**kwargs):pytest.fail('same-frame result was searched again')
    monkeypatch.setattr(detector,'tutorial_certificate',forbidden);monkeypatch.setattr(detector,'reward_box',forbidden)
    monkeypatch.setattr('src.core.bloodweb_detector.find_center',forbidden)
    result=detector.analyze_live(frame)
    assert result.nodes and backend.captures==1 and context.stats['center_reuses']==1
    assert visuals_for(replace(frame,captured_at=frame.captured_at+.01)) is None
    assert visuals_for(replace(frame,image=frame.image.copy())) is None
    assert visuals_for(replace(frame,window=replace(frame.window,dpi=144))) is None
    assert visuals_for(replace(frame,probe_only=True)) is None


def test_stable_then_real_continue_forces_transition_without_a_center_search(root,library,monkeypatch):
    from src.utils.images import read_image
    from src.core.models import ScreenState
    backend,detector,manager=native_manager(root,library);manager.capture()
    backend.pixels=read_image(root/'tests/fixtures/v54/continue_perk_slot_ii.png')
    def forbidden(*args,**kwargs):pytest.fail('CONTINUE must be detected before center lookup')
    monkeypatch.setattr('src.core.bloodweb_detector.find_center',forbidden)
    frame=manager.capture();result=detector.analyze_live(frame)
    assert result.screen_state==ScreenState.TUTORIAL and frame.visuals.mode=='SCENE_TRANSITION_CHECK'
    assert backend.captures==2


def test_evidence_writer_retains_order_and_stop_does_not_wait_for_png_io():
    from src.core.evidence_writer import EvidenceWriter
    from tests.test_automation import Device,web,engine
    entered=threading.Event();release=threading.Event();order=[];writer=EvidenceWriter()
    def first():entered.set();assert release.wait(3);order.append(1)
    writer.submit('first',first);assert entered.wait(1)
    writer.submit('second',lambda:order.append(2))
    device=Device([web()]);e=engine(device,dry_run=False);e.evidence_writer=writer
    e.halt();assert e.stop.is_set() and device.releases and not release.is_set()
    release.set();records=writer.close()
    assert order==[1,2] and [r['identity'] for r in records]==['first','second'] and not writer.failures()


def test_background_failure_blocks_further_input_and_is_reported():
    from src.core.evidence_writer import EvidenceWriter
    from src.core.screen_manager import ScreenError
    from tests.test_automation import Device,web,engine
    writer=EvidenceWriter()
    def fail():raise OSError('disk write failed')
    writer.submit('proof',fail);writer.close()
    e=engine(Device([web()]),dry_run=False);e.evidence_writer=writer
    with pytest.raises(ScreenError,match='evidence_write_failed'):e.live_safety()
    assert writer.failures()[0]['identity']=='proof'


def test_parallel_proof_io_is_not_added_to_foreground_wall_time():
    from scripts.audit_live_speed_v58 import exclusive
    rows=[dict(stage='capture',started_at=0,ended_at=2),dict(stage='proof_io_background',started_at=0,ended_at=1,kind='background_io')]
    totals,_,_=exclusive(rows,0,2)
    assert totals=={'capture':2}


def test_turbo_rejection_is_reused_and_closed_opening_is_not_rechecked():
    from tests.test_v57_turbo import setup
    from src.core.models import NodeState
    e,d,f,r=setup();r.nodes[1].icon_id=None
    e.try_turbo(f,r,r.nodes[1]);count=sum(x['stage']=='burst_eligibility' for x in e.report.entries)
    e.try_turbo(replace(f,captured_at=f.captured_at+.01),copy.deepcopy(r),r.nodes[1])
    assert sum(x['stage']=='burst_eligibility' for x in e.report.entries)==count
    changed=copy.deepcopy(r);changed.nodes[1].state=NodeState.PURCHASED
    e.try_turbo(replace(f,captured_at=f.captured_at+.02),changed,changed.nodes[2])
    e.try_turbo(replace(f,captured_at=f.captured_at+.03),changed,changed.nodes[2])
    assert sum(x['stage']=='burst_eligibility' for x in e.report.entries)==count and e.turbo_suppressed>=3
    r.level+=1;e.try_turbo(replace(f,captured_at=f.captured_at+.04),r,r.nodes[1])
    assert sum(x['stage']=='burst_eligibility' for x in e.report.entries)==count+1 and not d.presses


@pytest.mark.parametrize('occupancy',[.08,.3,.7,.92])
@pytest.mark.parametrize('flips',[0,1,4,12,40])
def test_binary_candidate_bounds_keep_near_threshold_matches(occupancy,flips):
    from src.core.visual_search import binary_match
    # Explicit binary injection to exercise Pearson/Hamming bounds, not game
    # evidence. Both arbitrary noise and repeated targets use the old matcher.
    rng=np.random.default_rng(700+flips)
    template=(rng.random((19,61))<occupancy).astype(np.uint8)*255
    mask=(rng.random((180,300))<.15).astype(np.uint8)*255
    tile=template.copy();indices=rng.choice(tile.size,flips,replace=False)
    tile.flat[indices]=255-tile.flat[indices]
    mask[70:89,120:181]=tile
    old=binary_match(mask,template);new=binary_match(mask,template,fast=True)
    assert (old[0]>=.96)==(new[0]>=.96)
    if old[0]>=.96:assert old[1:]==new[1:]


def test_vee_series_keeps_caps_across_restart(tmp_path):
    from src.core.series_budget import SeriesBudget
    from src.core.screen_manager import ScreenError
    scope=dict(role='survivor',character='vee_boonyasak',max_bp=10000,max_nodes=2,
               max_commands=3,allow_prestige=True,allow_chain=True)
    budget=SeriesBudget(tmp_path/'ledger.json',scope)
    budget.reserve(20000,5000,'single',[(10,20)]);budget.command()
    resumed=SeriesBudget(tmp_path/'ledger.json',scope)
    assert resumed.data['commands']==1 and not resumed.data['reservations'][0]['settled']
    with pytest.raises(ScreenError,match='chain_budget_limit'):
        resumed.reserve(20000,6000,'chain',[(30,40)])
    with pytest.raises(ScreenError,match='chain_ledger_scope_mismatch'):
        SeriesBudget(tmp_path/'ledger.json',dict(scope,max_bp=20000))


def test_capture_components_have_real_nonoverlapping_intervals(root,library):
    backend,detector,manager=native_manager(root,library)
    manager.capture();previous_end=0
    for name,interval in manager.last_intervals.items():
        assert previous_end<=interval['started_at']<=interval['ended_at']
        assert manager.last_timings[name]==interval['ended_at']-interval['started_at']
        previous_end=interval['ended_at']


def test_already_captured_confirmation_does_not_duplicate_capture_measurements():
    from tests.test_automation import Device,web,engine
    device=Device([web()]);e=engine(device,dry_run=False)
    device.last_timings={'capture_dialog_check':.5,'capture_total':1}
    e.confirmation_capture=device.capture()
    e.capture_frame()
    assert device.index==1
    assert not any(s['stage']=='capture_dialog_check' for s in e.performance.samples)


def test_completed_level_clock_and_boundaries_survive_without_final_save(tmp_path):
    from src.core.performance import Performance
    from types import SimpleNamespace
    perf=Performance(tmp_path/'run.performance.json')
    for level,stamp in ((10,100.),(11,106.),(12,112.)):
        frame=SimpleNamespace(window=SimpleNamespace(signature=(1,2)),captured_at=stamp,bloodpoints=50000)
        perf.levels.confirm(frame,SimpleNamespace(level=level),transition=level>10)
    checkpoint=json.loads((tmp_path/'run.performance.checkpoint.json').read_text())
    assert checkpoint['clock_anchor']==perf.anchor
    row=next(r for r in checkpoint['levels']['records'] if r['level']==11)
    assert row['complete'] and row['observed_from_transition'] and row['elapsed']==6
    assert not perf.path.exists()  # no normal-finally save was needed


def test_live_diagnostics_keep_fatal_handler_without_periodic_native_watchdog(monkeypatch):
    import faulthandler
    from scripts.live_optimize_v55 import enable_crash_trace
    calls=[];stream=object()
    monkeypatch.setattr(faulthandler,'cancel_dump_traceback_later',lambda:calls.append('cancel'))
    monkeypatch.setattr(faulthandler,'enable',lambda target:calls.append(target))
    def forbidden(*args,**kwargs):pytest.fail('unsafe periodic native stack walk was re-enabled')
    monkeypatch.setattr(faulthandler,'dump_traceback_later',forbidden)
    enable_crash_trace(stream)
    assert calls==['cancel',stream]


def test_offline_audit_uses_exact_checkpoint_when_native_exit_lost_final_snapshot(tmp_path):
    from src.core.performance import Performance
    from scripts.audit_live_speed_v58 import report
    from types import SimpleNamespace
    action=tmp_path/'actions.json';perf=Performance(action.with_suffix('.performance.json'))
    perf.anchor=dict(perf=0,unix=1000)  # explicit injected timing fixture
    for level,stamp in ((10,100.),(11,106.),(12,112.)):
        if level==12:perf.add('capture_total',2,started_at=107.,ended_at=109.)
        frame=SimpleNamespace(window=SimpleNamespace(signature=(1,2)),captured_at=stamp,bloodpoints=50000)
        perf.levels.confirm(frame,SimpleNamespace(level=level),transition=level>10)
    (tmp_path/'events.jsonl').write_text('')
    (tmp_path/'session.json').write_text(json.dumps(dict(pid=1,action_report=str(action))))
    rows=report(tmp_path,tmp_path/'timing')['levels']
    assert len(rows)==1 and rows[0]['level']==11 and rows[0]['recovered_from_checkpoint']
    assert rows[0]['elapsed']==6 and rows[0]['exclusive_seconds']['capture_total']==2
    assert sum(rows[0]['exclusive_seconds'].values())==6


def saved_transition_case(root,case='pending_transition_18_19',identifiers=('1792407165037349700_9b4cf43d','1792407169351564700_64774b54','1792407170480921700_ebda41a0')):
    import hashlib
    from dataclasses import fields
    from types import SimpleNamespace
    from src.core.models import Analysis,Node,NodeState,NodeType,ScreenState
    from src.core.screen_manager import CapturedFrame,WindowInfo,Rect
    folder=root/'debug/v59/vee_20261019_131032'/case
    provenance=json.loads((folder/'sources.json').read_text())
    historical=(folder/'pending_scene.json').exists()
    if historical:
        for name,row in provenance.items():assert hashlib.sha256((folder/name).read_bytes()).hexdigest()==row['sha256']
        saved=json.loads((folder/'pending_scene.json').read_text())
    else:
        for row in provenance:assert hashlib.sha256((root/row['copy']).read_bytes()).hexdigest()==row['sha256']
        saved=json.loads((folder/'vee_20261019_131032_scene_record.json').read_text())['scene']
    observations=[]
    for ident in identifiers:
        data=json.loads((folder/ident/'analysis.json' if historical else folder/(ident+'_analysis.json')).read_text())
        meta=json.loads((folder/ident/'manifest.json' if historical else folder/(ident+'_manifest.json')).read_text())['frame']
        names={f.name for f in fields(Analysis)}
        result=Analysis(**{k:v for k,v in data.items() if k in names and k!='nodes'})
        result.screen_state=ScreenState(result.screen_state);result.nodes=[Node(**n) for n in data['nodes']]
        for n in result.nodes:n.state=NodeState(n.state);n.type=NodeType(n.type)
        w=dict(meta['window']);w['client']=Rect(**w['client']);w['monitor']=Rect(**w['monitor']);roi=Rect(**meta['roi'])
        # Real serialized geometry/state, not generated pixels or a new live
        # confirmation. Only shape is required by physical to_screen mapping.
        image=SimpleNamespace(shape=(meta['image_size'][1],meta['image_size'][0],3))
        frame=CapturedFrame(image,WindowInfo(**w),roi,meta['captured_at'],level=meta['level'],level_confidence=meta['level_confidence'])
        observations.append((frame,result))
    return saved,observations


def test_real_terminal_after_frame_proves_transition_without_crediting_one_red(root):
    from src.core.scene_memory import terminal_observation
    from tests.test_automation import Device,web,engine
    saved,observations=saved_transition_case(root);original=copy.deepcopy(saved)
    witnessed=terminal_observation(saved,*observations[0])
    assert witnessed and saved==original
    e=engine(Device([web()]),dry_run=False);e.persisted_scene=copy.deepcopy(saved)
    e.persisted_scene['transition_observation']=witnessed
    e.signature=observations[1][0].window.signature
    assert not e.recheck_saved_scene(*observations[1])
    assert not e.recheck_saved_scene(*observations[1])  # duplicate stamp
    assert e.recheck_saved_scene(*observations[2])
    assert e.persisted_scene is None and e.actions==0 and not e.mouse.presses


@pytest.mark.parametrize('bad',['probe','partial','old_header','identity_conflict','target_not_red'])
def test_terminal_transition_observation_rejects_unproven_or_conflicting_old_map(root,bad):
    from src.core.scene_memory import terminal_observation
    from src.core.models import NodeState
    saved,observations=saved_transition_case(root);frame,result=copy.deepcopy(observations[0])
    target=next(n for n in result.nodes if n.id==4)
    if bad=='probe':frame.probe_only=True
    if bad=='partial':result.details['confirmation_only']=True
    if bad=='old_header':result.level=None
    if bad=='identity_conflict':target.icon_id='contradictory'
    if bad=='target_not_red':target.state=NodeState.AVAILABLE
    assert terminal_observation(saved,frame,result) is None


def test_terminal_witness_does_not_remove_new_map_identity_requirement(root):
    from src.core.scene_memory import terminal_observation
    from src.core.screen_manager import ScreenError
    from tests.test_automation import Device,web,engine
    saved,observations=saved_transition_case(root)
    e=engine(Device([web()]),dry_run=False);e.persisted_scene=copy.deepcopy(saved)
    e.persisted_scene['transition_observation']=terminal_observation(saved,*observations[0])
    e.signature=observations[1][0].window.signature
    for frame,result in observations[1:]:
        for node in result.nodes:node.icon_id=None
    assert not e.recheck_saved_scene(*observations[1])
    # 14.11: an unprovable click is no longer an immediate pause; it is watched read-only, then retired with no credit.
    try:assert e.recheck_saved_scene(*observations[2]) is False   # Never credited, never clicked again.
    except ScreenError as exc:assert exc.code=='purchase_result_unknown'   # An unreadable frame still refuses.
    assert e.persisted_scene['pending'] and e.actions==0 and not e.mouse.presses


def test_native_closing_chain_retires_only_after_two_complete_pristine_maps(root):
    from tests.test_automation import Device,web,engine
    saved,observations=saved_transition_case(root,'chain_transition_21_22',('1792407796551697500_db0ea963','1792407796903940300_5cd768ad'))
    e=engine(Device([web()]),dry_run=False);e.persisted_scene=copy.deepcopy(saved)
    e.signature=observations[0][0].window.signature
    assert not e.recheck_saved_scene(*observations[0])
    assert not e.recheck_saved_scene(*observations[0])
    assert e.recheck_saved_scene(*observations[1])
    assert e.actions==0 and not e.mouse.presses and not e.persisted_scene


def test_one_pristine_chain_map_cannot_lend_its_proof_to_unknown_next_map(root):
    from tests.test_automation import Device,web,engine
    saved,observations=saved_transition_case(root,'chain_transition_21_22',('1792407796551697500_db0ea963','1792407796903940300_5cd768ad'))
    e=engine(Device([web()]),dry_run=False);e.persisted_scene=copy.deepcopy(saved)
    e.signature=observations[0][0].window.signature
    assert not e.recheck_saved_scene(*observations[0])
    weak=copy.deepcopy(observations[1]);weak[1].level=None
    assert not e.recheck_saved_scene(*weak)
    assert not e.recheck_saved_scene(*observations[1])
    assert e.persisted_scene['pending'] and not e.mouse.presses and e.actions==0


@pytest.mark.parametrize('calibrating',[False,True])
def test_price_hover_follows_new_worker_target_before_probe_but_keeps_manual_target(root,tmp_path,monkeypatch,calibrating):
    from scripts.live_test_v54 import NativeReview
    from src.core.path_planner import RoutePlan
    from tests.test_automation import Device,web,engine
    saved,observations=saved_transition_case(root,'price_plan_22',('1792408189317662600_191cc156',))
    frame,result=observations[0];old=next(n for n in result.nodes if n.id==2);fresh=next(n for n in result.nodes if n.id==3)
    e=engine(Device([web()]),dry_run=False);e.settings.run_mode='continuous';e.calibrating=calibrating
    e.runtime_deadline=float('inf')
    e.native_review_ui_settle=0;e.recover_read=lambda **kwargs:(frame,result,RoutePlan(3,[3]))
    e.check_held=lambda f,x,y:f.to_screen(x,y)
    expected=frame.to_screen((old if calibrating else fresh).x,(old if calibrating else fresh).y)
    def stop_at_hover(point,*args):
        assert point==expected
        raise InterruptedError('injection stops before any capture/input')
    e.mouse.park=stop_at_hover
    monkeypatch.setattr('scripts.live_test_v54.write_image',lambda *args:None)
    monkeypatch.setattr('scripts.live_test_v54.digest',lambda *args:'injected-no-file')
    with pytest.raises(InterruptedError):NativeReview(e,tmp_path).prepare(frame,result,None,[old],'single')
    assert not e.mouse.presses


def gold_case(root,name):
    import hashlib,cv2
    from src.utils.images import read_image
    folder=root/'debug/v59/vee_extension_20261019_144011/missing_target_30'
    record=json.loads((folder/'sources.json').read_text())[name]
    path=folder/name;assert hashlib.sha256(path.read_bytes()).hexdigest()==record['sha256']
    pixels=read_image(path)
    return pixels,cv2.cvtColor(pixels,cv2.COLOR_BGR2HSV),cv2.cvtColor(pixels,cv2.COLOR_BGR2GRAY)


@pytest.mark.parametrize('point,radius',[((678,918),48),((686,917),40)])
def test_actual_consumed_tile_cannot_borrow_gold_from_fire(root,point,radius):
    from src.core.bloodweb_detector import ring_fractions,gold_ring_support
    _,hsv,_=gold_case(root,'absorbed_original.png')
    assert gold_ring_support(hsv,*point,radius)<.6
    assert ring_fractions(hsv,*point,radius)[1]==0


@pytest.mark.parametrize('point,radius',[((684,918),40),((622,796),40),((443,501),40),((995,389),40)])
def test_independent_native_available_rims_keep_color_and_ridge_evidence(root,point,radius):
    from src.core.bloodweb_detector import ring_fractions,gold_ring_support
    _,hsv,gray=gold_case(root,'available_original.png')
    assert gold_ring_support(hsv,*point,radius)>.75
    assert ring_fractions(hsv,*point,radius)[1]>.42


def test_oversized_fire_proposal_refines_only_to_strong_current_dark_frame(root):
    from src.core.bloodweb_detector import fresh_dark_radius,BloodwebDetector
    _,hsv,gray=gold_case(root,'absorbed_original.png')
    radius=fresh_dark_radius(hsv,gray,678,918,48,40)
    assert 26<=radius<=36 and BloodwebDetector._dark_rim(gray,678,918,radius)>.6
    _,available_hsv,available_gray=gold_case(root,'available_original.png')
    assert fresh_dark_radius(available_hsv,available_gray,684,918,40,40)==40


def test_expired_burst_stops_before_down_and_confirms_only_sent_prefix(tmp_path):
    from tests.test_v57_turbo import setup
    from src.core.turbo_burst import dispatch
    from src.core.screen_manager import ScreenError
    from src.core.models import NodeState
    from src.core.path_planner import RoutePlan
    from src.core.action_outcomes import ActionOutcomes
    e,d,f,r=setup();e.outcome_journal=ActionOutcomes(tmp_path/'journal.json')
    d.down_attempt_sequence=0;original=e._buy_once;calls=[];trace=[]
    purchase=d.purchase
    def submit(*args):d.down_attempt_sequence+=1;return purchase(*args)
    d.purchase=submit
    def guarded(*args,**kwargs):
        if len(d.presses)==3:raise ScreenError('stale_frame')
        return original(*args,**kwargs)
    e._buy_once=guarded
    e.trace=lambda stage,**details:trace.append((stage,details))
    def recover(*args,**kwargs):
        assert len(d.presses)==3 and not e.burst_inputs_active
        calls.append(1);fresh=d.capture();fresh.level=20;after=copy.deepcopy(r)
        for n in after.nodes[1:4]:n.state=NodeState.PURCHASED
        return fresh,after,RoutePlan()
    e.recover_read=recover
    assert dispatch(e,f,r)
    assert len(d.presses)==e.actions==3 and len(calls)==2 and not e.pending_action
    assert e.scene_record['last_outcome']['confirmed_nodes']==3
    assert len(e.scene_record['last_outcome']['points'])==3
    stopped=next(data for stage,data in trace if stage=='burst_prefix_stopped')
    assert stopped['down_api_attempted'] is False
    outcome=next(data for stage,data in trace if stage=='burst_outcome')
    assert not outcome['all_planned_inputs_sent'] and outcome['input_stop_reason']=='burst_lease_expired_before_down'


@pytest.mark.parametrize('fresh_age',[0.,1.])
def test_burst_refreshes_aged_full_before_reservation_and_never_extends_lease(fresh_age):
    from tests.test_v57_turbo import setup
    from src.core.turbo_burst import prepare
    from src.core.grouped_purchase import execute_group
    from src.core.path_planner import RoutePlan
    from src.core.screen_manager import ScreenError
    e,d,f,r=setup();e.screen.backend=object();f.captured_at=time.monotonic()-1.
    steps=prepare(e,f,r);fresh=d.capture();fresh.captured_at=time.monotonic()-fresh_age;fresh.bloodpoints=f.bloodpoints
    calls=[];reserved=[]
    e.recover_read=lambda **kwargs:(calls.append(kwargs) or (fresh,copy.deepcopy(r),RoutePlan()))
    def reserve(group):reserved.append(group);raise InterruptedError('stop before any input')
    if fresh_age:
        with pytest.raises(ScreenError,match='purchase_preparation_changed'):
            execute_group(e,f,r,[n for _,n in steps],reserve,prepared=steps)
        assert not reserved and e.turbo_unfresh_scene==(fresh.window.signature,r.level)
        assert e.preparation_changed_without_input
    else:
        with pytest.raises(InterruptedError):execute_group(e,f,r,[n for _,n in steps],reserve,prepared=steps)
        assert reserved[0].frame is fresh and len(reserved[0].targets)==5
    assert len(calls)==1 and not d.presses and not e.pending_action


@pytest.mark.parametrize('strong',[True,False])
def test_first_proven_next_map_at_timeout_gets_only_a_bounded_second_read(strong):
    from tests.test_v57_turbo import setup
    from src.core.models import NodeState
    from src.core.path_planner import RoutePlan
    from src.core.screen_manager import ScreenError
    e,d,f,old=setup();e.settings.fast_opening=False;e.settings.purchase_timeout=.03
    f.level=20;f.level_confidence=.99
    for n in old.nodes[1:6]:n.state=NodeState.PURCHASED
    old.nodes[6].state=NodeState.AVAILABLE
    new=copy.deepcopy(old);new.level=21
    for i,n in enumerate(new.nodes[1:],1):
        n.x+=60;n.y-=30;n.icon_id='next'+str(i);n.icon_margin=.1
        n.state=NodeState.AVAILABLE if i<3 else NodeState.LOCKED
        if not strong:n.icon_id=None
    reads=[];trace=[];last=[]
    e.trace=lambda stage,**details:trace.append((stage,details))
    def recover(*args,**kwargs):
        if not reads:time.sleep(.05)  # initial transition read crosses deadline
        reads.append(1);fresh=d.capture();fresh.level=21;fresh.level_confidence=.99
        last[:]=[fresh,copy.deepcopy(new),RoutePlan()]
        return tuple(last)
    e.recover_read=recover;e.wait_next_bloodweb=lambda *args:tuple(last)
    e.poll=lambda:None
    if strong:
        outcome=e._buy_once(f,old,node=old.nodes[6])
        assert outcome[1].level==21 and len(reads)==2 and not e.pending_action
        assert e.last_purchase_outcome=='level_changed_unconfirmed'
    else:
        with pytest.raises(ScreenError,match='purchase_result_unknown'):e._buy_once(f,old,node=old.nodes[6])
        assert len(reads)==1 and e.pending_action
    assert len(d.presses)==1 and e.actions==0
    grace=[data for stage,data in trace if stage=='transition_confirmation_grace']
    assert len(grace)==(1 if strong else 0)
    if strong:assert grace[0]['extra_seconds']==2 and grace[0]['paid_inputs']==0


def test_real_same_lattice_transition_uses_only_historical_accepted_glyph_provenance(root):
    from src.core.level_transition import known_glyph_transition
    from tests.test_automation import Device,web,engine
    folder=root/'debug/v59/vee_extension_20261019_144011/historical_transition_37_38'
    saved,observations=saved_transition_case(root,folder,('1792411779714841800_b634f7e5','1792411780476439900_26238d22'))
    assert not known_glyph_transition(saved,*observations[0])
    original=copy.deepcopy(saved)
    saved['transition_glyphs']=json.loads((folder/'with_history.json').read_text())['transition_glyphs']
    e=engine(Device([web()]),dry_run=False);e.persisted_scene=copy.deepcopy(saved);e.signature=observations[0][0].window.signature
    assert not e.recheck_saved_scene(*observations[0])
    assert not e.recheck_saved_scene(*observations[0])
    assert e.recheck_saved_scene(*observations[1])
    assert saved['nodes']==original['nodes'] and saved['pending']==original['pending']
    assert e.actions==0 and not e.mouse.presses and not e.persisted_scene


@pytest.mark.parametrize('fault',['conflict','duplicate','weak_score','future_stamp','wrong_header'])
def test_historical_glyph_cannot_bypass_original_transition_guards(root,fault):
    from src.core.level_transition import known_glyph_transition
    folder=root/'debug/v59/vee_extension_20261019_144011/historical_transition_37_38'
    saved,observations=saved_transition_case(root,folder,('1792411779714841800_b634f7e5',))
    saved['transition_glyphs']=json.loads((folder/'with_history.json').read_text())['transition_glyphs']
    if fault=='duplicate':saved['transition_glyphs']*=2
    for g in saved['transition_glyphs']:
        if fault=='conflict':g['conflicted']=True
        if fault=='weak_score':g['score']=.1
        if fault=='future_stamp':g['stamp']=saved['captured_at']+1
    if fault=='wrong_header':observations[0][1].level=39
    assert not known_glyph_transition(saved,*observations[0])


def test_historical_transition_provenance_rebuilds_from_original_accepted_maps(root):
    from src.core.scene_memory import record_scene
    folder=root/'debug/v59/vee_extension_20261019_144011/historical_transition_37_38'
    identifiers=sorted(p.name for p in folder.iterdir() if p.is_dir() and
        json.loads((p/'analysis.json').read_text())['level']==37)
    _,observations=saved_transition_case(root,folder,identifiers)
    prior=None
    for frame,result in observations:prior=record_scene(frame,result,[],previous=prior)
    archived=json.loads((folder/'with_history.json').read_text())
    assert prior['transition_glyphs']==archived['transition_glyphs']
    assert len(prior['transition_glyphs'])==6
    # Fresh Unknown nodes remain Unknown even while old accepted provenance is
    # retained separately. A new level cannot inherit this old-level history.
    frame,result=copy.deepcopy(observations[-1]);frame.captured_at+=1
    for node in result.nodes:node.icon_id=None
    saved=record_scene(frame,result,[],previous=prior)
    assert all(n['icon'] is None for n in saved['nodes']) and saved['transition_glyphs']
    result.level+=1
    assert not record_scene(frame,result,[],previous=prior)['transition_glyphs']


def filler_price_case(root):
    import hashlib
    from dataclasses import fields
    from types import SimpleNamespace
    from src.core.models import Analysis,Node,NodeState,ScreenState
    from src.core.screen_manager import CapturedFrame,WindowInfo,Rect
    folder=root/'debug/v59/vee_extension_20261019_144011/filler_preparation_39'
    for name,row in json.loads((folder/'sources.json').read_text()).items():
        assert hashlib.sha256((folder/name).read_bytes()).hexdigest()==row['sha256']
    request=json.loads((folder/'request.json').read_text());observations=[]
    for ident in ('1792412310020960200_ba13a10d','1792412310377267800_5e052ec2'):
        data=json.loads((folder/(ident+'_analysis.json')).read_text())
        m=json.loads((folder/(ident+'_manifest.json')).read_text())['frame'];names={f.name for f in fields(Analysis)}
        r=Analysis(**{k:v for k,v in data.items() if k in names and k!='nodes'})
        r.screen_state=ScreenState(r.screen_state);r.nodes=[Node(**n) for n in data['nodes']]
        for n in r.nodes:n.state=NodeState(n.state)
        w=dict(m['window']);w['client']=Rect(**w['client']);w['monitor']=Rect(**w['monitor'])
        f=CapturedFrame(SimpleNamespace(shape=(m['image_size'][1],m['image_size'][0],3)),WindowInfo(**w),Rect(**m['roi']),m['captured_at'])
        observations.append((f,r))
    return request,observations


@pytest.mark.parametrize('fault',['none','persistent_filler','priority','persistent_priority','manual','unknown','conflict','weak_geometry'])
def test_same_known_filler_price_can_survive_only_equivalent_fresh_filler_decision(root,fault):
    from scripts.native_price_v55 import single_scene_valid
    from src.core.path_planner import RoutePlan
    request,observations=filler_price_case(root);key=request['scene'];point=request['targets'][0]['point']
    key['single_filler_preparation']=fault!='manual'
    reason={'priority':'priority','persistent_priority':'PERSISTENT:priority','persistent_filler':'PERSISTENT:filler'}.get(fault,'filler')
    plan=RoutePlan(3,[3],reason=reason)
    for f,r in observations:
        node=next(n for n in r.nodes if n.id==4)
        if fault=='unknown':node.icon_id=None
        if fault=='conflict':node.icon_id='conflicting-fresh-identity'
        if fault=='weak_geometry':node.geometry_confidence=.7
        assert single_scene_valid(key,f,r,plan,point)==(fault in ('none','persistent_filler'))


def test_filler_price_still_waits_for_two_independent_clean_maps(root):
    from types import SimpleNamespace
    from src.core.path_planner import RoutePlan
    from src.core.automation import Automation
    from scripts.live_test_v54 import wait_price_scene
    request,observations=filler_price_case(root);request['scene']['single_filler_preparation']=True
    reads=iter([(f,r,RoutePlan(3,[3],reason='filler')) for f,r in observations]);calls=[]
    def recover(*args,**kwargs):calls.append(1);return next(reads)
    e=SimpleNamespace(settings=SimpleNamespace(purchase_timeout=1),runtime_deadline=time.monotonic()+2,
        gate=lambda:None,recover_read=recover,poll=lambda:None,trace=lambda *a,**k:None,stable_scene=Automation.stable_scene)
    first,second=wait_price_scene(e,request['scene'],request['targets'][0]['point'],True)
    assert len(calls)==2 and second[0].captured_at>first[0].captured_at


def test_reviewed_filler_contract_reaches_final_mouse_guard(root):
    from scripts.native_price_v55 import prepared_single_key,single_scene_valid
    from src.core.path_planner import RoutePlan
    request,observations=filler_price_case(root);source=request['scene']
    source['single_filler_preparation']=True;point=request['targets'][0]['point']
    frame,result=observations[-1]
    reviewed=prepared_single_key(frame,result,source)
    final=prepared_single_key(frame,result,reviewed)
    assert single_scene_valid(final,frame,result,RoutePlan(3,[3],reason='PERSISTENT:filler'),point)
    assert not single_scene_valid(final,frame,result,RoutePlan(3,[3],reason='PERSISTENT:priority'),point)
    source['level']-=1
    assert not prepared_single_key(frame,result,source).get('single_filler_preparation')


def test_weak_reused_slot_cannot_lend_transition_proof_to_next_strong_map(root):
    from tests.test_automation import Device,web,engine
    folder=root/'debug/v59/vee_extension_20261019_144011/weak_transition_40_41'
    saved,observations=saved_transition_case(root,folder,('1792412781131986100_0dbcdbdf','1792412782364947600_67a15e27'))
    e=engine(Device([web()]),dry_run=False);e.persisted_scene=copy.deepcopy(saved);e.signature=observations[0][0].window.signature
    assert not e.recheck_saved_scene(*observations[0])
    assert not e.recheck_saved_scene(*observations[1])  # real weak target geometry
    # Explicit timestamp injection checks the counter contract, not a new
    # native success. Both subsequent serialized maps pass independently.
    good=copy.deepcopy(observations[0]);good[0].captured_at=observations[1][0].captured_at+1
    assert not e.recheck_saved_scene(*good)
    good2=copy.deepcopy(good);good2[0].captured_at+=1
    assert e.recheck_saved_scene(*good2)
    assert e.actions==0 and not e.mouse.presses


def test_reused_slot_read_only_retries_remain_bounded(root):
    from tests.test_automation import Device,web,engine
    from src.core.screen_manager import ScreenError
    folder=root/'debug/v59/vee_extension_20261019_144011/weak_transition_40_41'
    saved,observations=saved_transition_case(root,folder,('1792412782364947600_67a15e27',))
    e=engine(Device([web()]),dry_run=False);e.persisted_scene=copy.deepcopy(saved);e.signature=observations[0][0].window.signature
    frame,result=observations[0];e.settings.purchase_timeout=4
    assert not e.recheck_saved_scene(frame,result)
    # 14.11: an unprovable click is no longer an immediate pause; it is watched read-only, then retired with no credit.
    outcome=None
    for i in range(1,16):
        injected=replace(frame,captured_at=frame.captured_at+i)
        try:outcome=e.recheck_saved_scene(injected,result)
        except ScreenError as exc:outcome=exc.code;break      # An unreadable map still refuses, as before.
        if outcome:break
    assert outcome in (True,'purchase_result_unknown') and e.actions==0 and not e.mouse.presses   # Bounded either way.
    if outcome is True:assert e.persisted_scene is None


def test_actual_dim_locked_frame_gets_observation_score_without_input_or_identity_promotion(root):
    from src.core.bloodweb_detector import framed_locked_confidence
    from src.core.models import NodeState
    from tests.test_automation import Device,web,engine
    folder=root/'debug/v59/vee_extension_20261019_144011/weak_transition_40_41'
    saved,observations=saved_transition_case(root,folder,('1792412781131986100_0dbcdbdf','1792413001016111900_d36f9ce5'))
    f,r=observations[1];node=next(n for n in r.nodes if n.geometry_confidence<.65)
    proof=next(g for g in r.details['node_validation'] if g['accepted'] and (g['x'],g['y'],g['radius'])==(node.x,node.y,node.radius))
    assert node.state==NodeState.LOCKED and node.icon_id is None and node.geometry_confidence==.628
    score=framed_locked_confidence(node.geometry_confidence,node.state,proof)
    assert .65<=score<.7 and node.icon_id is None
    for weakened in (dict(proof,connected=False),dict(proof,valley=.3),dict(proof,regular=.5)):
        assert framed_locked_confidence(.628,node.state,weakened)==.628
    assert framed_locked_confidence(.628,NodeState.AVAILABLE,proof)==.628
    node.geometry_confidence=score  # replay the new production score, no pixels invented
    e=engine(Device([web()]),dry_run=False);e.persisted_scene=copy.deepcopy(saved);e.signature=f.window.signature
    assert not e.recheck_saved_scene(*observations[0])
    assert e.recheck_saved_scene(*observations[1])
    assert e.actions==0 and not e.mouse.presses and node.icon_id is None


def test_one_pixel_hough_alias_uses_fresh_rim_pixels_below_input_floor(root):
    import cv2
    from src.utils.images import read_image
    from src.core.bloodweb_detector import framed_locked_confidence
    folder=root/'debug/v59/vee_extension_20261019_144011/weak_transition_40_41'
    _,observations=saved_transition_case(root,folder,('1792412782364947600_67a15e27',))
    f,r=observations[0];node=next(n for n in r.nodes if n.geometry_confidence<.65)
    proof=next(g for g in r.details['node_validation'] if g['accepted'] and (g['x'],g['y'],g['radius'])==(node.x,node.y,node.radius))
    assert framed_locked_confidence(.628,node.state,proof)==.628
    pixels=read_image(folder/'native_original.png');gray=cv2.cvtColor(pixels,cv2.COLOR_BGR2GRAY);x,y=f.to_screen(node.x,node.y)
    score=framed_locked_confidence(.628,node.state,proof,gray,x,y,node.radius)
    assert .65<=score<.7 and node.icon_id is None and node.geometry_confidence==.628
    assert framed_locked_confidence(.628,node.state,dict(proof,connected=False),gray,x,y,node.radius)==.628


@pytest.mark.parametrize('fault',['unsubmitted','down_attempted','pending','no_counter','repeated'])
def test_stale_preinput_recovery_requires_no_native_down_and_stays_bounded(fault):
    from tests.test_automation import Device,web,engine
    from src.core.screen_manager import ScreenError
    d=Device([web()]);e=engine(d,dry_run=False,max_actions=1)
    d.down_attempt_sequence=0;calls=[];traces=[]
    e.trace=lambda stage,**kw:traces.append((stage,kw))
    if fault=='no_counter':del d.down_attempt_sequence
    def buy(f,r,**kwargs):
        calls.append(1)
        if fault=='down_attempted':d.down_attempt_sequence+=1
        if fault=='pending':e.pending_action=True
        if fault!='unsubmitted' or len(calls)<=2:raise ScreenError('stale_frame')
        from src.core.path_planner import RoutePlan
        e.actions+=1
        return f,r,RoutePlan(reason='injected_test_result')
    e.buy=buy;e.run()
    assert not d.presses
    assert len(calls)==({'unsubmitted':3,'repeated':4}.get(fault,1))
    retries=[kw for stage,kw in traces if stage=='preinput_stale_recovery']
    assert len(retries)==({'unsubmitted':2,'repeated':3}.get(fault,0))
    assert all(kw['paid_inputs']==0 and kw['reservations_retained'] for kw in retries)
