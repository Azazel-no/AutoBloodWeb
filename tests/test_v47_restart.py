import copy,time
from dataclasses import replace
import pytest
from tests.test_automation import Device,web,engine
from src.core.models import Node,NodeType,NodeState
from src.core.scene_memory import record_scene
from src.core.screen_manager import ScreenError

def scene():
    d=Device([web()]);f=d.capture();old=web(NodeState.PURCHASED);new=web()
    for i in range(2,5):
        old.nodes.append(Node(i,40*i,80,20,NodeState.PURCHASED,NodeType.ITEM,'old'+str(i)))
        new.nodes.append(Node(i,40*i,80,20,NodeState.LOCKED,NodeType.ITEM,'new'+str(i)))
    old.nodes[1].state=NodeState.AVAILABLE;old.nodes[1].icon_id='pending_old'
    new.nodes[1].icon_id=None;new.nodes[1].type=NodeType.UNKNOWN
    memory=record_scene(f,old,[(n.x,n.y) for n in old.nodes[1:]],dict(point=list(f.to_screen(old.nodes[1].x,old.nodes[1].y)),radius=20,icon='pending_old'))
    fresh=replace(f,window=replace(f.window,hwnd=2,pid=99),captured_at=f.captured_at+1)
    return d,old,new,memory,fresh

def test_new_process_requires_two_fresh_scenes_with_several_changed_known_ids():
    d,old,new,memory,f=scene();r=engine(d,dry_run=False);r.signature=f.window.signature;r.persisted_scene=copy.deepcopy(memory);r.attempted=[(n.x,n.y) for n in old.nodes[1:]]
    assert not r.recheck_saved_scene(f,new) and r.attempted
    assert not r.recheck_saved_scene(f,new) and r.attempted
    assert r.recheck_saved_scene(replace(f,captured_at=f.captured_at+1),new)
    assert not r.attempted and r.scene_record['pending'] is None and not d.presses
    assert new.nodes[1].icon_id is None and r.actions==0
    assert r.scene_record['window']['pid']==99

def test_restart_same_scene_cannot_resolve_old_purchase_even_if_red():
    d,old,new,memory,f=scene();r=engine(d,dry_run=False);r.signature=f.window.signature;r.persisted_scene=memory
    old.nodes[1].state=NodeState.PURCHASED
    assert not r.recheck_saved_scene(f,old)
    with pytest.raises(ScreenError,match='window_changed'):r.recheck_saved_scene(replace(f,captured_at=f.captured_at+1),old)
    assert r.persisted_scene['pending'] and not d.presses

def test_restart_changed_geometry_or_unapproved_context_refuses():
    d,old,new,memory,f=scene();r=engine(d,dry_run=False);r.signature=f.window.signature;r.persisted_scene=memory
    with pytest.raises(ScreenError,match='window_changed'):r.recheck_saved_scene(replace(f,window=replace(f.window,dpi=192)),new)
    r.approved=False
    with pytest.raises(ScreenError,match='window_changed'):r.recheck_saved_scene(f,new)


def calibration_restart():
    d=Device([web()]);old=web();old.level=6;old.level_confidence=.99
    f=d.capture();memory=record_scene(f,old,[(230,200)])
    f=replace(f,window=replace(f.window,hwnd=2,pid=99))
    new=web();new.level=7;new.level_confidence=.99
    r=engine(d,dry_run=False);r.approved=False;r.calibrating=True
    r.signature=f.window.signature;r.persisted_scene=memory;r.attempted=[(230,200)]
    r.calibration_target=(230,200,f.window.signature,f.roi)
    r.calibration_window_proof=dict(target=r.calibration_target,created=time.monotonic())
    r.input_ready_check=lambda:True
    return d,r,f,new


def test_armed_new_calibration_retires_nonpending_next_level_after_two_captures_only():
    d,r,f,new=calibration_restart()
    assert not r.recheck_saved_scene(f,new) and r.attempted
    assert not r.recheck_saved_scene(f,new) and r.attempted
    assert r.recheck_saved_scene(replace(f,captured_at=f.captured_at+.1),new)
    assert r.persisted_scene is None and not r.attempted and not r.approved
    assert r.scene_record['level']==7 and r.scene_record['window']['pid']==99
    assert r.actions==r.attempts==0 and not d.presses


@pytest.mark.parametrize('failure',['no_proof','expired','future','target','roi','not_calibrating',
    'hotkeys','heartbeat','geometry','offline','probe','focus'])
def test_calibration_restart_context_cannot_be_inferred_or_reused(failure):
    d,r,f,new=calibration_restart()
    if failure=='no_proof':r.calibration_window_proof=None
    elif failure=='expired':r.calibration_window_proof['created']-=61
    elif failure=='future':r.calibration_window_proof['created']+=61
    elif failure=='target':r.calibration_target=(231,200,f.window.signature,f.roi)
    elif failure=='roi':f=replace(f,roi=replace(f.roi,x=1))
    elif failure=='not_calibrating':r.calibrating=False
    elif failure=='hotkeys':r.hotkeys_ready=False
    elif failure=='heartbeat':r.input_ready_check=lambda:False
    elif failure=='geometry':f=replace(f,window=replace(f.window,dpi=192));r.signature=f.window.signature
    elif failure=='offline':f=replace(f,live=False)
    elif failure=='probe':f=replace(f,probe_only=True)
    elif failure=='focus':f=replace(f,window=replace(f.window,foreground=False))
    with pytest.raises(ScreenError,match='window_changed'):r.recheck_saved_scene(f,new)
    assert r.persisted_scene and r.attempted and not d.presses


@pytest.mark.parametrize('failure',['same_level','low_confidence','skip_level','pending','chain'])
def test_restart_header_cannot_clear_unproven_or_pending_scene(failure):
    d,r,f,new=calibration_restart()
    if failure=='same_level':new.level=6
    elif failure=='low_confidence':new.level_confidence=.94
    elif failure=='skip_level':new.level=8
    else:
        r.persisted_scene['pending']=dict(point=list(f.to_screen(230,200)),radius=20,icon='target')
        if failure=='chain':r.persisted_scene['pending']['chain']={'nodes':[]}
    assert not r.recheck_saved_scene(f,new)
    with pytest.raises(ScreenError,match='window_changed'):r.recheck_saved_scene(replace(f,captured_at=f.captured_at+.1),new)
    assert r.persisted_scene and r.attempted and not d.presses


def test_both_restart_header_observations_must_prove_next_level():
    d,r,f,new=calibration_restart();weak=copy.deepcopy(new);weak.level_confidence=.94
    assert not r.recheck_saved_scene(f,weak)
    assert not r.recheck_saved_scene(replace(f,captured_at=f.captured_at+.1),new)
    assert r.attempted and r.persisted_scene
    assert r.recheck_saved_scene(replace(f,captured_at=f.captured_at+.2),new)
    assert not r.attempted and not d.presses


def test_new_calibration_run_can_reach_purchase_after_restart_without_old_method_approval():
    d,r,f,new=calibration_restart()
    purchased=copy.deepcopy(new);purchased.nodes[1].state=NodeState.PURCHASED
    d.window=f.window;d.results=[new,new,new,purchased,purchased];d.index=0
    r.run(calibrate=True)
    assert r.receipt and r.receipt['verified'] and not r.receipt['user_confirmed']
    assert r.actions==1 and len(d.presses)==1 and not r.approved
    assert r.persisted_scene is None and not r.scene_record['pending']

def test_actual_old_scene_and_new_cheryl37_pixels_require_fresh_evidence(root,library):
    import json,hashlib
    from src.utils.images import read_image
    from src.core.bloodweb_detector import BloodwebDetector
    from tests.test_v47_transition_pixels import frame as make_frame
    evidence=json.loads((root/'tests/fixtures/v47/restart_evidence.json').read_text(encoding='utf-8'))
    path=root/evidence['source'];assert hashlib.sha256(path.read_bytes()).hexdigest()==evidence['sha256']
    raw=read_image(path);roi=evidence['frame']['roi'];pixels=raw[roi['y']:roi['y']+roi['height'],roi['x']:roi['x']+roi['width']]
    f=make_frame(evidence['frame'],pixels);d=Device([web()]);r=engine(d,dry_run=False)
    r.signature=f.window.signature;r.persisted_scene=copy.deepcopy(evidence['prior_scene']);r.attempted=[tuple(p) for p in r.persisted_scene['attempted']]
    detector=BloodwebDetector(library);result=detector.analyze_live(f)
    assert not r.recheck_saved_scene(f,result) and r.attempted
    assert r.recheck_saved_scene(replace(f,captured_at=f.captured_at+1),result)
    assert not r.attempted and not d.presses and r.actions==0 and r.scene_record['pending'] is None

def test_saved_click_from_another_level_is_retired_without_credit():
    d,old,new,memory,f=scene();memory['level']=50;old.level=17;old.level_confidence=.99
    r=engine(d,dry_run=False);r.signature=f.window.signature;r.persisted_scene=memory;r.attempted=[(1,2)]
    assert not r.recheck_saved_scene(f,old)
    assert r.recheck_saved_scene(replace(f,captured_at=f.captured_at+1),old)
    assert r.persisted_scene is None and r.scene_record is None and not r.attempted and r.actions==0 and not d.presses
