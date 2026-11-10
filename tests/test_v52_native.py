"""Actual UI pixels and injected contracts; no desktop input or purchases."""
import copy
import hashlib
import json
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
import numpy as np
import pytest
from src.core.models import Analysis,Node,NodeState,NodeType,RecognitionStatus,ScreenState
from src.core.screen_manager import CapturedFrame,WindowInfo,Rect,ScreenError
from src.core.preview_capture import capture_preview
from src.core.chain_plan import PriceRegion,read_preview
from src.core.input_control import GlobalHotkeys
from src.core.prestige import ready_evidence,activate_ready
from tests.test_v51_hybrid import scene


def test_preview_crop_uses_client_roi_even_on_negative_monitor():
    clean,_=scene(2)
    clean=replace(clean,image=np.zeros((40,60,3),np.uint8),roi=Rect(10,20,120,80),
        window=replace(clean.window,client=Rect(-1920,0,640,240)))
    pixels=np.arange(240*640*3,dtype=np.uint8).reshape(240,640,3)
    full=replace(clean,image=pixels,roi=Rect(0,0,640,240),captured_at=clean.captured_at+.1,probe_only=True)
    screen=SimpleNamespace(capture_overlay=lambda _:full)
    actual=capture_preview(screen,clean)
    assert actual.roi==clean.roi and actual.image.shape==clean.image.shape
    assert actual.raw_image is pixels and actual.probe_only
    assert actual.to_screen(0,0)==(-1910,20)


@pytest.mark.parametrize('change',[{'probe_only':False},{'captured_at':0}, {'window':WindowInfo(99,'other',Rect(0,0,640,240),Rect(0,0,640,240))}])
def test_preview_rejects_nonprobe_old_or_other_context(change):
    clean,_=scene(2)
    full=replace(clean,probe_only=True,captured_at=clean.captured_at+.1,**{})
    full=replace(full,**change)
    with pytest.raises(ScreenError,match='preview_context_unproven'):
        capture_preview(SimpleNamespace(capture_overlay=lambda _:full),clean)


def test_actual_native_hover_price_is_not_a_proven_path(root):
    folder=root/'debug/v52/hover_02';data=json.loads((folder/'status.json').read_text(encoding='utf-8'))
    from src.utils.images import read_image
    from src.core.resources import ResourceReader
    a=data['clean_analysis'];a=copy.deepcopy(a);a['screen_state']=ScreenState(a['screen_state'])
    for n in a['nodes']:
        n['state']=NodeState(n['state']);n['type']=NodeType(n['type']);n['recognition_status']=RecognitionStatus(n['recognition_status'])
    a['nodes']=[Node(**n) for n in a['nodes']];clean=Analysis(**a)
    meta=data['clean_frame'];w=meta['window'];roi=Rect(**meta['roi'])
    window=WindowInfo(w['hwnd'],w['title'],Rect(**w['client']),Rect(**w['monitor']),w['dpi'],True,w['pid'])
    base=CapturedFrame(np.zeros((roi.height,roi.width,3),np.uint8),window,roi,meta['captured_at'],level=49)
    reader=ResourceReader(root)
    # Visually reviewed: native chain icon + right-hand 6000; left 4000 is item.
    region=PriceRegion((947,695,60,23),data['observations'][1]['sha256'],True,'client')
    for record in data['observations']:
        path=root/record['source'];assert hashlib.sha256(path.read_bytes()).hexdigest()==record['sha256']
        raw=read_image(path);frame=replace(base,image=raw[roi.y:roi.y+roi.height,roi.x:roi.x+roi.width],
            raw_image=raw,probe_only=True,captured_at=record['frame']['captured_at'])
        observation=read_preview(frame,clean,data['target']['id'],'native_hover',reader,region,base)
        assert observation.total_bp==6000 and observation.ordered_nodes==[]
        assert observation.reason=='preview_path_unknown_or_ambiguous'
        assert reader.decode(raw[695:718,769:827])==4000
    assert not data['purchases'] and not data['keyboard']
    assert not any(e['stage']=='SendInput' for e in data['native_events'])


def test_user_mastermind_emblem_has_ready_certificate_without_fee_or_duration(root,library):
    from src.utils.images import read_image
    from src.core.bloodweb_detector import BloodwebDetector
    image=read_image(root/'references/bloodweb/prestige_node_example.png')
    result=BloodwebDetector(library,'killer','mastermind').analyze(image,'user reference')
    assert result.screen_state==ScreenState.PRESTIGE_READY
    proof=result.details['prestige_ready']
    assert proof['score']>=.97 and proof['price'] is None and proof['input_duration'] is None
    assert len(result.nodes)==1 and result.nodes[0].type==NodeType.PRESTIGE
    ordinary=read_image(root/'references/bloodweb/bloodweb_level1_after_prestige.png')
    assert ready_evidence(ordinary,root,(684,562,40)) is None


def test_registration_health_does_not_claim_physical_key():
    keys=object.__new__(GlobalHotkeys);keys.ready=True;keys.initialized=threading.Event();keys.initialized.set()
    keys.thread=SimpleNamespace(is_alive=lambda:True);keys.done=threading.Event();keys.last_pump=time.monotonic()
    keys.bindings={'stop':'F9'};keys.registered_shortcuts={'F9'}
    assert keys.native_health()['ready'] and not keys.native_health()['physical_key_verified']
    keys.last_pump-=2
    assert not keys.native_health()['ready']
    keys.last_pump=time.monotonic();keys.registered_shortcuts.clear()
    assert not keys.native_health()['ready']


def test_actual_visible_native_path_and_total_price_agree_on_two_frames(root):
    from scripts.inspect_preview_v52 import load
    from src.core.resources import ResourceReader
    from src.core.chain_plan import ChainPlan
    data,clean,result,frames=load(root/'debug/v52/hover_05')
    region=PriceRegion((1365,736,63,24),data['observations'][1]['sha256'],True,'client')
    reader=ResourceReader(root)
    observations=[read_preview(f,result,data['target']['id'],'real_hover',reader,region,clean) for f in frames[1:]]
    assert [o.ordered_nodes for o in observations]==[[3,9],[3,9]]
    assert [o.total_bp for o in observations]==[4500,4500]
    plan=ChainPlan('real_hover',1,3,9,[3,9]);plan.bind_preview(observations)
    assert not plan.blockers and plan.game_preview==[3,9] and plan.total_bp==4500
    # Without new red pixels only the graph and the exact standard total can prove it.
    already_highlighted=replace(clean,image=frames[1].image)
    proven=read_preview(frames[2],result,9,'real_hover',reader,region,already_highlighted)
    assert proven.ordered_nodes==[3,9] and proven.edges==[]
    altered=copy.deepcopy(result);altered.edges.remove(next(e for e in altered.edges if set(e)=={3,9}))
    assert read_preview(frames[2],altered,9,'real_hover',reader,region,clean).ordered_nodes==[]
    assert result.nodes[3].state!=NodeState.PURCHASED and result.nodes[9].state!=NodeState.PURCHASED


def test_one_full_live_scope_stops_before_another_input():
    from scripts.live_test_v48 import check_opening_scope
    e=SimpleNamespace(opening=SimpleNamespace(active=True,full_scans=1))
    check_opening_scope(dict(require_one_full=True,require_fast_opening=True),e)
    e.opening.full_scans=2
    with pytest.raises(ScreenError,match='one_full_required'):
        check_opening_scope(dict(require_one_full=True),e)


def test_actual_restarted_scene_can_seed_compact_but_not_skip_two_observations(root,library):
    from scripts.inspect_opening_failure_v49 import stored_frame
    from src.core.bloodweb_detector import BloodwebDetector
    from src.core.automation import Automation,Settings
    from tests.test_automation import Device
    folder=root/'debug/v52/live_session_de3ee07f'
    records={r['analysis_id']:r for r in json.loads((folder/'frames.json').read_text(encoding='utf-8'))}
    ids=['1792047982728349900_24cf8a71','1792047983095564600_47a61472']
    frames=iter(stored_frame(records[i]) for i in ids)
    data=json.loads((folder/'preferences_before.json').read_text(encoding='utf-8'))
    device=Device([]);device.capture=lambda:replace(next(frames),captured_at=time.monotonic())
    detector=BloodwebDetector(library,'killer','unknown')
    runner=Automation(device,detector,device,data['killer_priorities']['unknown'],
        Settings(dry_run=False,experimental_persistent=True,startup_delay=0),approved=True,hotkeys_ready=True)
    old=copy.deepcopy(data['purchase_scenes']['killer/unknown']);runner.persisted_scene=old
    runner.attempted=[tuple(p) for p in old['attempted']]
    first,web,plan=runner.read()
    assert runner.opening.active and runner.opening.full_scans==1
    assert not runner.recheck_saved_scene(first,web)
    with pytest.raises(ScreenError,match='purchase_result_unknown'):
        runner._buy_once(first,web,node=next(n for n in web.nodes if n.id==plan.next_node))
    second,web,_=runner.read()
    assert runner.recheck_saved_scene(second,web)
    assert runner.scene_record['level']==49 and runner.scene_record['window']['pid']==second.window.pid
    assert runner.scene_record['pending'] is None and runner.scene_record['attempted']==[]
    assert runner.opening.full_scans==1 and runner.opening.quick_checks==1
    assert not device.presses and runner.actions==runner.attempts==0


def test_actual_oversized_center_crop_failure_uses_item_radius_without_full_matcher(root,library,monkeypatch):
    from scripts.inspect_opening_failure_v49 import stored_frame
    from src.core.bloodweb_detector import BloodwebDetector
    from src.core.persistent_route import PersistentVision
    folder=root/'debug/v52/live_session_de3ee07f'
    records={r['analysis_id']:r for r in json.loads((folder/'frames.json').read_text(encoding='utf-8'))}
    frames=[stored_frame(records[i]) for i in ['1792048027960138600_708db1f6','1792048029795410300_5c270cb1']]
    detector=BloodwebDetector(library,'killer','unknown');first=detector.analyze_live(frames[0])
    known={n.id:n.icon_id for n in first.nodes if n.icon_id};vision=PersistentVision()
    assert vision.begin(frames[0],first,detector,[],'smart',0)
    cap=first.details['opening_crop_ceiling']
    assert cap['content_radius']<round(cap['center_radius']*.9)
    assert all(next(n for n in first.nodes if n.id==i).icon_id==ident for i,ident in known.items())
    def forbidden(*a,**k):raise AssertionError('full detector/matcher after seed')
    monkeypatch.setattr(detector,'analyze_live',forbidden);monkeypatch.setattr(detector.matcher,'match',forbidden)
    second=vision.analyze(frames[1],detector,[],'smart',0)
    assert vision.quick_checks==1
    green=next(n for n in second.nodes if n.icon_id=='killer_offering_040')
    assert green.confidence>=.86 and green.contour_score>=.68


def test_actual_small_gold_rims_do_not_shrink_normal_center_crops(root,library):
    from scripts.inspect_opening_failure_v49 import stored_frame
    from src.core.bloodweb_detector import BloodwebDetector
    from src.core.persistent_route import PersistentVision
    folder=root/'debug/v52/live_session_d60792db'
    records={r['analysis_id']:r for r in json.loads((folder/'frames.json').read_text(encoding='utf-8'))}
    frames=[stored_frame(records[i]) for i in ['1792048713890294000_ed93265b','1792048715960328500_88efacc6']]
    detector=BloodwebDetector(library,'killer','unknown');first=detector.analyze_live(frames[0]);vision=PersistentVision()
    assert vision.begin(frames[0],first,detector,[],'smart',0)
    ceiling=first.details['opening_crop_ceiling']
    assert ceiling['content_radius']==round(ceiling['center_radius']*.9)
    assert vision.analyze(frames[1],detector,[],'smart',0).screen_state==ScreenState.BLOODWEB
    assert vision.quick_checks==1


def test_elevation_is_only_requested_for_integrity_mismatch():
    from src.core.elevation import needs_elevation
    backend=SimpleNamespace(info=lambda:object(),access_status=lambda w:dict(reason='ok'))
    assert not needs_elevation(backend)
    backend.access_status=lambda w:dict(reason='input_integrity_mismatch')
    assert needs_elevation(backend)
    def absent():raise ScreenError('game_not_found')
    backend.info=absent
    assert not needs_elevation(backend)




def test_prestige_holds_half_a_second_and_requires_two_new_level_frames():
    from tests.test_automation import Device,engine,web
    next_web=web();next_web.level=1;next_web.level_confidence=1
    d=Device([next_web,next_web]);d.parking_point=lambda f:(-392,8);d.park=lambda *a:None
    original=d.capture
    def capture():return replace(original(),bloodpoints=30000,level=1,level_confidence=1)
    d.capture=capture
    e=engine(d,dry_run=False,auto_prestige=True,hold_seconds=.25);e.prestige_approved=True
    before=CapturedFrame(np.zeros((400,400,3),np.uint8),d.window,Rect(0,0,400,400),time.monotonic(),bloodpoints=50000,level=50)
    ready=Analysis(400,400,ScreenState.PRESTIGE_READY,
        [Node(0,200,200,35,NodeState.CENTER,NodeType.PRESTIGE,geometry_confidence=.95)],level=50,details={'prestige_ready':{'score':1}})
    fresh,result,_=activate_ready(e,before,ready)
    assert d.presses==[((-200,200),.5)] and d.index==2
    assert result.level==1 and fresh.bloodpoints==30000 and e.prestige_actions==1
    assert not e.pending_action and e.scene_record is None and e.actions==0


def test_automatic_session_check_requires_two_independent_clean_frames(app,library,tmp_path,monkeypatch):
    from src.gui.main_window import MainWindow
    from src.gui.session_check_dialog import SessionCheckDialog
    class Keys:
        ready=True
        def __init__(self,*a,**k):pass
        def close(self):pass
        def native_health(self):return dict(ready=True,physical_key_verified=False)
    monkeypatch.setattr('src.gui.main_window.GlobalHotkeys',Keys)
    owner=MainWindow(library,tmp_path/'preferences.json');owner.physical_f9_check.setChecked(False)
    dialog=SessionCheckDialog(owner,auto_start=True);frame,result=scene(2)
    result.live_frame=frame;result.capture_manager=SimpleNamespace(validate_click=lambda *a,**k:None)
    result.analysis_id='injected_no_input'
    try:
        dialog.checked_result(result)
        assert not dialog.waiting and dialog.receipt['check_kind']=='registration' and not dialog.succeeded
        fresh=copy.copy(result);fresh.live_frame=replace(frame,captured_at=frame.captured_at+.1)
        dialog.checked_result(fresh)
        assert dialog.succeeded and owner.current_session_check_kind=='registration'
    finally:dialog.close();owner.close()


@pytest.fixture(scope='module')
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])
