"""Observed-result reconciliation and single-pulse production contracts."""
import copy,json,time
from dataclasses import replace
from types import SimpleNamespace
import pytest
from src.core.action_outcomes import ActionOutcomes,operation_id
from src.core.scene_memory import record_scene
from src.core.models import NodeState
from src.core.screen_manager import ScreenError
from tests.test_automation import Device,engine,web
from tests.test_v51_hybrid import trial_setup,bought


def outcome_case(tmp_path):
    e,plan,permit,previous,latest=trial_setup(tmp_path);frame,result=latest
    scene=record_scene(frame,result,plan.physical_points,dict(point=plan.physical_points[-1],chain=plan.summary()))
    after=bought(bought(result,1),2)
    observations=[(replace(frame,captured_at=frame.captured_at+.1+i*.1,bloodpoints=25500),copy.deepcopy(after)) for i in range(2)]
    return ActionOutcomes(tmp_path/'outcomes.json'),scene,observations


def test_worker_and_late_checker_share_idempotent_per_node_result(tmp_path):
    journal,scene,observations=outcome_case(tmp_path)
    first=journal.reconcile(scene,observations,'late_checker')
    again=ActionOutcomes(journal.path).reconcile(scene,observations,'worker_restart')
    assert first==again and first['confirmed_nodes']==2 and first['bp_delta']==4500
    assert not first['authorizes_input'] and not first['reservation_refunded']
    assert len(json.loads(journal.path.read_text())['operations'])==1
    assert scene['pending']  # journal itself cannot clear or authorize pending


@pytest.mark.parametrize('bad',['duplicate','probe','one_terminal','missing','identity','level','state_conflict'])
def test_journal_never_resolves_incomplete_or_conflicting_chain(tmp_path,bad):
    journal,scene,observations=outcome_case(tmp_path)
    if bad=='duplicate':observations[1]=(observations[0][0],observations[1][1])
    if bad=='probe':observations[1]=(replace(observations[1][0],probe_only=True),observations[1][1])
    if bad=='one_terminal':observations[0][1].nodes[1].state=NodeState.AVAILABLE
    if bad=='missing':observations[1][1].nodes.pop(1)
    if bad=='identity':scene['nodes'][1]['icon']='old';observations[1][1].nodes[1].icon_id='new'
    if bad=='level':observations[1][1].level+=1
    if bad=='state_conflict':observations[1][1].nodes[1].state=NodeState.ABSORBED
    with pytest.raises(ScreenError):journal.reconcile(scene,observations,'injected')
    assert not journal.path.exists()


def test_reconciled_state_cannot_regress_or_refund_budget(tmp_path):
    journal,scene,observations=outcome_case(tmp_path);journal.reconcile(scene,observations,'worker')
    for f,r in observations:r.nodes[1].state=NodeState.ABSORBED
    with pytest.raises(ScreenError,match='contradiction'):journal.reconcile(scene,observations,'later')
    assert next(iter(json.loads(journal.path.read_text())['operations'].values()))['confirmed_nodes']==2


def test_saved_scene_resolution_updates_same_journal_without_mouse(tmp_path):
    journal,scene,observations=outcome_case(tmp_path)
    d=Device([]);e=engine(d,dry_run=False);e.outcome_journal=journal
    e.persisted_scene=copy.deepcopy(scene);e.scene_record=e.persisted_scene
    assert not e.recheck_saved_scene(*observations[0])
    assert e.recheck_saved_scene(*observations[1])
    assert not e.scene_record['pending'] and e.scene_record['last_outcome']['confirmed_nodes']==2
    assert e.scene_record['attempted']==scene['attempted'] and not d.presses


def test_production_repeat_mode_observes_two_red_after_one_pulse():
    d=Device([web(),web(),web(NodeState.PURCHASED),web(NodeState.PURCHASED)])
    e=engine(d,dry_run=False,purchase_mode='repeat',max_actions=1);e.run()
    assert len(d.presses)==e.attempts==e.actions==1 and not e.pending_action
    assert sum(row['stage']=='purchase_observation' for row in e.report.entries)>=2


def test_unknown_after_one_pulse_never_submits_another():
    d=Device([web(),web(),web(NodeState.UNCERTAIN)])
    e=engine(d,dry_run=False,purchase_mode='repeat',max_actions=1);e.run()
    assert len(d.presses)==e.attempts==1 and e.actions==0 and e.pending_action


def test_preparation_is_inside_three_second_countdown(monkeypatch):
    from tests.test_v46 import Clock
    clock=Clock();clock.now=0;monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    d=Device([web()]);d.prepare=lambda:clock.advance(1.7)
    e=engine(d,dry_run=False,max_actions=1);e.settings.startup_delay=3;e.wait=clock.advance
    def read():
        assert clock.now==pytest.approx(3)
        e.stop.set();raise InterruptedError
    e.read=read;e.run()
    assert not d.presses and clock.now==pytest.approx(3)


def test_compact_failure_keeps_original_and_click_geometry(tmp_path):
    import hashlib
    from src.core.diagnostics import DiagnosticStore
    d=Device([web()]);f=d.capture();result=web();result.details['attempted_screen_points']=[f.to_screen(230,200)]
    renderer=SimpleNamespace(save_debug=lambda *a,**k:pytest.fail('heavy node exports'))
    folder=DiagnosticStore(tmp_path).record(f.image,result,renderer,f,phase='failure',reason='test',full=False)
    manifest=json.loads((folder/'manifest.json').read_text())
    assert manifest['mode']=='compact_failure'
    assert set(manifest['files'])=={'raw.png','click_geometry.png','analysis.json'}
    assert all(hashlib.sha256((folder/name).read_bytes()).hexdigest()==digest for name,digest in manifest['files'].items())
    from src.utils.images import read_image
    assert list(read_image(folder/'click_geometry.png')[200,230])==[0,0,255]


def test_authorized_series_node_and_command_caps_survive_restart(tmp_path):
    from src.core.series_budget import SeriesBudget
    auth=dict(role='killer',character='dark_lord',max_bp=1000000,allow_prestige=True,allow_chain=True,
        max_commands=2,max_nodes=3,max_prestiges=1)
    path=tmp_path/'ledger.json';budget=SeriesBudget(path,auth)
    budget.reserve(1500000,8000,'chain',[(1,1),(2,2),(3,3)]);budget.command()
    budget=SeriesBudget(path,auth)
    with pytest.raises(ScreenError,match='action_limit'):budget.reserve(1500000,2000,'single',[(4,4)])
    budget.reserve(1500000,20000,'prestige',[(5,5)]);budget.command()
    with pytest.raises(ScreenError,match='prestige_manual'):budget.reserve(1500000,20000,'prestige',[(5,5)])
    with pytest.raises(ScreenError,match='action_limit'):budget.command()
    saved=json.loads(path.read_text());assert saved['commands']==2 and len(saved['reservations'])==2


@pytest.mark.parametrize('negative',['pending','one_changed','same_ids','weak','header'])
def test_idle_reused_slots_require_four_changed_positions_and_two_new_maps(root,negative):
    from dataclasses import asdict
    from src.core.level_transition import idle_forward_transition
    from src.core.models import Analysis,Node,NodeType,ScreenState,RecognitionStatus
    from src.core.screen_manager import Rect,WindowInfo,CapturedFrame
    import numpy as np
    data=json.loads((root/'tests/fixtures/v56/idle29_to36.json').read_text());old=data['old_scene']
    rows=[]
    for saved in data['observations']:
        m=saved['frame'];w=dict(m['window']);w['client']=Rect(**w['client']);w['monitor']=Rect(**w['monitor'])
        width,height=m['image_size'];f=CapturedFrame(np.zeros((height,width,3),np.uint8),WindowInfo(**w),Rect(**m['roi']),m['captured_at'],level=36,level_confidence=m['level_confidence'])
        a=copy.deepcopy(saved['analysis']);a['screen_state']=ScreenState(a['screen_state'])
        for n in a['nodes']:n['state']=NodeState(n['state']);n['type']=NodeType(n['type']);n['recognition_status']=RecognitionStatus(n['recognition_status'])
        a['nodes']=[Node(**n) for n in a['nodes']];rows.append((f,Analysis(**a)))
    comparison=copy.deepcopy(old);comparison['window']=asdict(rows[0][0].window)
    if negative=='pending':comparison['pending']={'chain':{'physical_points':[[1,2],[3,4]]}}
    if negative in ('same_ids','one_changed'):
        comparison['nodes']=record_scene(*rows[0],[])['nodes']
        if negative=='one_changed':comparison['nodes'][1]['icon']='different'
    if negative=='weak':
        for f,r in rows:
            for n in r.nodes:n.confidence=.85
    if negative=='header':rows[1][1].level_confidence=.94
    if negative:
        assert not idle_forward_transition(comparison,*rows[-1]);return
    d=Device([]);e=engine(d,dry_run=False);e.signature=rows[0][0].window.signature;e.persisted_scene=old
    assert not e.recheck_saved_scene(*rows[0])
    assert e.recheck_saved_scene(*rows[1])
    assert not e.attempted and not e.persisted_scene and e.actions==0 and not d.presses


@pytest.mark.parametrize('index',[0,1])
def test_actual_nested_event_header_bounds_keep_full_4000_price(root,index):
    import hashlib
    from tests.test_v55_native_price import locator
    from src.utils.images import read_image
    row=json.loads((root/'tests/fixtures/v56/nested_price.json').read_text())[index]
    path=root/row['path'];assert hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256']
    reader,_=locator(root)
    assert reader.locate_region(read_image(path),tuple(row['box'][:2]),row['tooltips'])['value']==4000
    conflicting=copy.deepcopy(row['tooltips']);conflicting[1][2]+=100
    assert reader.locate_region(read_image(path),tuple(row['box'][:2]),conflicting) is None


@pytest.mark.parametrize('shift',[6,15])
def test_single_journal_uses_existing_purchase_evidence_radius_not_chain_tolerance(tmp_path,shift):
    d=Device([web()]);f=d.capture();before=web();before.level=20
    scene=record_scene(f,before,[f.to_screen(230,200)],dict(point=list(f.to_screen(230,200)),radius=20,icon='target'))
    after=web(NodeState.PURCHASED);after.level=20;after.nodes[1].x+=shift
    observations=[(replace(f,captured_at=f.captured_at+.1+i*.1),copy.deepcopy(after)) for i in range(2)]
    journal=ActionOutcomes(tmp_path/'outcomes.json')
    if shift==6:assert journal.reconcile(scene,observations,'worker')['confirmed_nodes']==1
    else:
        with pytest.raises(ScreenError):journal.reconcile(scene,observations,'worker')


@pytest.mark.parametrize('changes',[1,4])
def test_preinput_plan_change_recovers_without_extra_mouse_and_is_bounded(changes):
    class Dynamic(Device):
        def analyze(self,image,source):return web(NodeState.PURCHASED if self.presses else NodeState.AVAILABLE)
    d=Dynamic([web()]);e=engine(d,dry_run=False,max_actions=1,purchase_mode='repeat');original=e.buy;calls=[0]
    def buy(*args,**kwargs):
        calls[0]+=1
        if calls[0]<=changes:
            e.preparation_changed_without_input=True
            raise ScreenError('purchase_preparation_changed')
        return original(*args,**kwargs)
    e.buy=buy;e.run()
    assert len(d.presses)==(1 if changes==1 else 0)
    assert e.actions==(1 if changes==1 else 0) and e.preparation_recoveries==min(changes,3)


@pytest.mark.parametrize('shift',[4,9])
def test_previously_owned_ring_jitter_uses_same_radius_as_chain_evidence(tmp_path,shift):
    from src.core.chain_plan import ChainEvidence
    from src.core.models import Node,NodeType
    e,plan,permit,previous,latest=trial_setup(tmp_path);f,r=latest
    r.nodes.append(Node(3,500,150,40,NodeState.PURCHASED,NodeType.ADDON,state_confidence=.93))
    evidence=ChainEvidence(f,r,plan)
    after=bought(bought(r,1),2);after.nodes[-1].state=NodeState.PURCHASED
    after.nodes[-1].x+=shift;after.nodes[-1].y+=4
    if shift==9:
        with pytest.raises(ValueError,match='chain_unexpected_purchase'):evidence.observe(replace(f,captured_at=f.captured_at+.1),after)
    else:
        assert not evidence.observe(replace(f,captured_at=f.captured_at+.1),after)
        assert evidence.observe(replace(f,captured_at=f.captured_at+.2),after)
        assert evidence.summary()['confirmed']==[1,2] and not evidence.unexpected


def test_blocked_chain_cannot_be_cleared_by_next_level_header(tmp_path):
    journal,scene,observations=outcome_case(tmp_path)
    scene['pending']['chain_blocked_reason']='chain_unexpected_purchase'
    d=Device([]);e=engine(d,dry_run=False);e.persisted_scene=scene
    f,r=observations[-1];r.level=scene['level']+1
    with pytest.raises(ScreenError,match='chain_unexpected_purchase'):e.recheck_saved_scene(f,r)
    assert e.persisted_scene['pending'] and not d.presses


def test_stale_gui_save_preserves_external_late_resolution_and_local_other_scene(tmp_path):
    from src.core.preferences import Preferences
    path=tmp_path/'preferences.json'
    pending={'pending':{'operation_id':'old'},'attempted':[[1,2]]}
    path.write_text(json.dumps({'schema':2,'purchase_scenes':{'killer/dark_lord':pending}}))
    gui=Preferences(path,[])
    resolved={'pending':None,'attempted':[[1,2]],'last_outcome':{'operation_id':'old'}}
    disk=json.loads(path.read_text());disk['purchase_scenes']['killer/dark_lord']=resolved
    path.write_text(json.dumps(disk))
    gui.data['purchase_scenes']['survivor/']={'pending':{'operation_id':'new'}}
    gui.save('ru','killer')
    scenes=json.loads(path.read_text())['purchase_scenes']
    assert scenes['killer/dark_lord']==resolved
    assert scenes['survivor/']['pending']['operation_id']=='new'
    # The next normal Start sees new external pending, not a cached clear.
    disk=json.loads(path.read_text());disk['purchase_scenes']['killer/dark_lord']=pending
    path.write_text(json.dumps(disk));gui.refresh_purchase_scenes()
    assert gui.data['purchase_scenes']['killer/dark_lord']==pending


def test_local_new_pending_is_not_erased_by_external_other_scene_save(tmp_path):
    from src.core.preferences import Preferences
    path=tmp_path/'preferences.json';path.write_text('{}')
    gui=Preferences(path,[]);gui.data['purchase_scenes']={'killer/dark_lord':{'pending':{'operation_id':'new'}}}
    path.write_text(json.dumps({'purchase_scenes':{'survivor/':{'pending':None}}}))
    gui.save('ru','killer')
    scenes=json.loads(path.read_text())['purchase_scenes']
    assert scenes['killer/dark_lord']['pending']['operation_id']=='new'
    assert scenes['survivor/']['pending'] is None
