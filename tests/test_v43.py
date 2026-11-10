"""Pinned user captures and bounded post-action recovery; never real input."""
import copy
import hashlib
import json
import math
import threading
import time
from pathlib import Path
import numpy as np
import pytest
from src.core.bloodweb_detector import find_center
from src.core.models import Analysis,Node,NodeState,NodeType,ScreenState
from src.core.automation import State
from src.core.screen_manager import ScreenManager,Rect,WindowInfo,ScreenError
from src.core.diagnostics import DiagnosticStore
from src.utils.images import read_image
from tests.test_automation import Device,web,engine
from tests.test_screen_manager import Backend
from src.core.geometry import purchasable


def test_pinned_user_captures_are_unchanged(root):
    manifest=json.loads((root/'tests/fixtures/v43/manifest.json').read_text(encoding='utf-8'))
    assert len(manifest['sources'])==9
    for item in manifest['sources']:
        assert hashlib.sha256((root/item['path']).read_bytes()).hexdigest()==item['sha256']


def test_red_branch_merging_cannot_select_sidebar_badge(root,detector):
    image=read_image(root/'tests/fixtures/v43/raw_1791793636036233900.png')
    center=find_center(image)
    assert math.dist(center[:2],(684,562))<4
    result=detector.analyze(image)
    assert result.screen_state==ScreenState.BLOODWEB
    assert math.dist((result.nodes[0].x,result.nodes[0].y),(684,562))<4
    assert not any(n.x<260 for n in result.nodes)


def test_wrong_cached_roi_is_reacquired_and_right_overlay_is_allowed(root,detector):
    image=read_image(root/'tests/fixtures/v43/raw_1791793636036233900.png')
    window=WindowInfo(1,'DBD',Rect(0,0,1920,1080),Rect(0,0,1920,1080),96,True)
    manager=ScreenManager(Backend(window,image,[Rect(1324,20,596,1040)]),detector)
    manager._region=(window.signature,Rect(0,61,749,1019))
    frame=manager.capture()
    assert frame.roi.x>250 and frame.roi.x+frame.roi.width<1200
    result=detector.analyze(frame.image)
    assert result.screen_state==ScreenState.BLOODWEB
    assert 'bloodweb_occluded' not in result.diagnostics


def test_low_icon_count_does_not_imply_occlusion():
    window=WindowInfo(1,'DBD',Rect(0,0,1920,1080),Rect(0,0,1920,1080),96,True)
    manager=ScreenManager(Backend(window,np.full((1080,1920,3),40,np.uint8),[Rect(1400,0,520,1080)]))
    with pytest.raises(ScreenError,match='bloodweb_not_found'):manager.capture()


def test_failed_new_capture_cannot_relabel_an_old_raw_frame(root):
    image=read_image(root/'references/bloodweb/bloodweb_level43_start.png')
    window=WindowInfo(1,'DBD',Rect(0,0,916,840),Rect(0,0,1920,1080),96,True)
    backend=Backend(window,image);manager=ScreenManager(backend);manager.capture()
    assert manager.last_raw is not None
    def missing():raise ScreenError('game_not_found')
    backend.info=missing
    with pytest.raises(ScreenError,match='game_not_found'):manager.capture()
    assert manager.last_raw is None


def test_bad_crop_and_random_circles_cannot_become_a_clickable_web(root,detector):
    raw=read_image(root/'tests/fixtures/v43/raw_1791793636036233900.png')
    result=detector.analyze(raw[61:1080,:749])
    assert result.screen_state==ScreenState.UNKNOWN and 'roi_invalid' in result.diagnostics
    import cv2
    random=np.full((840,916,3),45,np.uint8)
    for x,y in [(100,200),(400,500),(700,600)]:cv2.circle(random,(x,y),35,(0,0,255),8)
    result=detector.analyze(random)
    assert result.screen_state==ScreenState.UNKNOWN and not result.nodes


def test_real_item_tooltip_is_distinct_from_windows_occlusion(root,detector):
    image=read_image(root/'tests/fixtures/v43/purchase_after_latest_003.png')
    result=detector.analyze(image)
    assert result.details['transient_overlay']=='item_tooltip'
    assert 'item_tooltip' in result.diagnostics and 'bloodweb_occluded' not in result.diagnostics
    assert not any(n.x>523 and n.y>205 for n in result.nodes)
    overlay=web();overlay.details['transient_overlay']='item_tooltip'
    d=Device([overlay,web(),web(),web(NodeState.PURCHASED),web(NodeState.PURCHASED)])
    e=engine(d,dry_run=False);e.run(calibrate=True)
    assert e.actions==1 and len(d.presses)==1


def test_jitter_and_temporary_unknown_recover_before_input():
    first=web(); shifted=copy.deepcopy(first)
    for node in shifted.nodes:node.x+=2;node.y-=2
    device=Device([Analysis(400,400,ScreenState.UNKNOWN),first,shifted,web(NodeState.PURCHASED),web(NodeState.PURCHASED)])
    e=engine(device,dry_run=False);e.run(calibrate=True)
    assert e.actions==1 and e.receipt['verified'] and len(device.presses)==1


def test_startup_delay_does_not_consume_frame_stabilization_budget(monkeypatch):
    clock=[100.]
    monkeypatch.setattr(time,'monotonic',lambda:clock[0])
    d=Device([web(),web(),web(NodeState.PURCHASED),web(NodeState.PURCHASED)])
    e=engine(d,dry_run=False);e.settings.startup_delay=8
    def wait(seconds):clock[0]+=seconds
    e.wait=wait;e.run(calibrate=True)
    assert e.receipt['verified'] and len(d.presses)==1


def test_poll_does_not_add_sleep_when_processing_already_took_the_interval(monkeypatch):
    monkeypatch.setattr(time,'monotonic',lambda:20.)
    d=Device([web()]);e=engine(d);e.settings.poll_interval=.3;e.last_capture_at=19.
    waits=[];e.wait=waits.append;e.poll();assert waits==[0]
    e.last_capture_at=19.9;e.poll();assert waits[-1]==pytest.approx(.2)


def test_runtime_limit_applies_to_pause_and_before_input(monkeypatch):
    d=Device([web()]);e=engine(d,dry_run=False)
    e.runtime_deadline=1.;monkeypatch.setattr(time,'monotonic',lambda:2.)
    e.pause.set()
    with pytest.raises(ScreenError,match='runtime_limit'):e.gate()
    e.pause.clear()
    with pytest.raises(ScreenError,match='runtime_limit'):e.check(d.capture(),230,200)
    assert not d.presses


def test_capture_anchor_loss_is_retried_without_an_extra_press():
    class Recovering(Device):
        failures=2
        def capture(self):
            if self.failures:
                self.failures-=1;raise ScreenError('bloodweb_not_found')
            return super().capture()
        def reset_region(self):self.reset_called=True
    device=Recovering([web(),web(),web(NodeState.PURCHASED),web(NodeState.PURCHASED)])
    e=engine(device,dry_run=False);e.run(calibrate=True)
    assert device.reset_called and e.actions==1 and len(device.presses)==1


def test_stale_and_unrecoverable_frames_never_send_input():
    class Stale(Device):
        def capture(self):
            f=super().capture();f.captured_at-=10;return f
    d=Stale([web()]);e=engine(d,dry_run=False);e.run()
    assert not d.presses and any(x.get('reason')=='stale_frame' for x in e.report.entries)
    d=Device([Analysis(400,400,ScreenState.UNKNOWN)]);e=engine(d,dry_run=False);e.run()
    assert not d.presses and any(x.get('reason')=='recovery_timeout' for x in e.report.entries)


def test_two_purchases_use_new_frames_and_replanned_targets():
    a=web();a.nodes.append(Node(2,150,300,20,NodeState.AVAILABLE,NodeType.ITEM,'second',state_confidence=.95,geometry_confidence=.95));a.edges.append((0,2))
    b=copy.deepcopy(a);b.nodes[1].state=NodeState.PURCHASED
    c=copy.deepcopy(b);c.nodes[2].state=NodeState.PURCHASED
    d=Device([a,a,b,b,b,b,c,c]);e=engine(d,dry_run=False,max_actions=2);e.run()
    assert e.actions==2 and len(d.presses)==2 and d.presses[0][0]!=d.presses[1][0]


def test_confirmed_target_cannot_be_pressed_again_after_false_gold_state():
    d=Device([web(),web(),web(NodeState.PURCHASED),web(NodeState.PURCHASED),web(),web()])
    e=engine(d,dry_run=False,max_actions=2);e.run()
    assert len(d.presses)==1 and any(x.get('reason')=='next_bloodweb_timeout' for x in e.report.entries)


def test_emergency_stop_during_recovery_is_prompt_and_releases():
    d=Device([Analysis(400,400,ScreenState.UNKNOWN)]);e=engine(d,dry_run=False)
    t=threading.Thread(target=e.run);t.start();time.sleep(.05);e.halt();t.join(1)
    assert not t.is_alive() and not d.presses and d.releases>=1


def test_diagnostic_retention_never_removes_pinned_or_foreign_data(tmp_path):
    store=DiagnosticStore(tmp_path,max_runs=1)
    for name,pinned,owner in [('first',False,'autobloodweb_v43'),('second',False,'autobloodweb_v43'),('pinned',True,'autobloodweb_v43'),('foreign',False,'other')]:
        folder=tmp_path/name;folder.mkdir();(folder/'manifest.json').write_text(json.dumps(dict(owner=owner,pinned=pinned)))
    store.prune()
    assert (tmp_path/'pinned').is_dir() and (tmp_path/'foreign').is_dir()
    assert sum((tmp_path/n).is_dir() for n in ('first','second'))==1


def test_retention_cache_notices_new_pin_and_missing_addon_sources(library,tmp_path):
    store=DiagnosticStore(tmp_path,max_runs=3)
    for name in ('a','b','c'):
        folder=tmp_path/name;folder.mkdir();(folder/'manifest.json').write_text(json.dumps(dict(owner='autobloodweb_v43',pinned=False,total_bytes=1)))
    store.prune()
    pinned=tmp_path/'a'/'manifest.json';pinned.write_text(json.dumps(dict(owner='autobloodweb_v43',pinned=True,total_bytes=1)))
    store.max_runs=1;store.prune()
    assert pinned.is_file()
    extras=[e for e in library.entries if e.id.startswith('wiki_survivor_common_addons_')]
    assert len(extras)==22 and len({e.fingerprint for e in extras})==22
    assert all(e.id in library.display_images for e in extras)


def test_every_analysis_gets_immutable_evidence(tmp_path,root,detector):
    image=read_image(root/'references/bloodweb/bloodweb_level1_after_prestige.png')
    store=DiagnosticStore(tmp_path)
    folders=[store.record(image,detector.analyze(image),detector) for _ in range(2)]
    assert folders[0]!=folders[1]
    for folder in folders:
        d=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        for name,digest in d['files'].items():assert hashlib.sha256((folder/name).read_bytes()).hexdigest()==digest
        assert all((folder/name).is_file() for name in ('raw.png','input.png','gray.png','red_mask.png','candidates.png','accepted_route.png'))


def test_live_replay_is_deterministic_without_detector_state_leak(root,detector):
    image=read_image(root/'tests/fixtures/v43/raw_1791793636036233900.png')[140:983,262:1105]
    def signature():
        r=detector.analyze(image)
        return (r.screen_state,[(n.x,n.y,n.state,n.icon_id) for n in r.nodes],r.edges)
    assert signature()==signature()==signature()


def test_followup_capture_hashes_and_outer_calibration_target(root,detector):
    folder=root/'tests/fixtures/v43/followup'
    for source in json.loads((folder/'manifest.json').read_text())['sources']:
        assert hashlib.sha256((root/source['path']).read_bytes()).hexdigest()==source['sha256']
    result=detector.analyze(read_image(folder/'outer_before.png'))
    outer=next(n for n in result.nodes if n.icon_id=='wiki_survivor_common_addons_69cf520f0aa9')
    assert outer.state==NodeState.AVAILABLE
    assert not purchasable(outer,result.nodes,result.edges)
    # The actual first attempt had no purchased intermediate node. A gold
    # preview alone cannot authorize a one-node press in any controller path.
    d=Device([result]);e=engine(d,dry_run=False)
    with pytest.raises(ScreenError,match='unsafe_node'):e.buy(d.capture(),result,outer)
    assert not d.presses
    neighbor=next(n for n in result.nodes if (outer.id,n.id) in result.edges or (n.id,outer.id) in result.edges)
    neighbor.state=NodeState.PURCHASED
    assert purchasable(outer,result.nodes,result.edges)


@pytest.mark.parametrize('name',['purple_tooltip','brown_tooltip'])
def test_real_tooltip_covering_anchor_skips_impossible_template_search(root,detector,monkeypatch,name):
    image=read_image(root/'tests/fixtures/v43/followup'/f'{name}.png')
    window=WindowInfo(1,'DBD',Rect(0,0,1920,1080),Rect(0,0,1920,1080),96,True)
    manager=ScreenManager(Backend(window,image,[Rect(1272,12,656,1047)]),detector)
    def forbidden(*args,**kwargs):raise AssertionError('Tooltip must be classified before anchor search')
    monkeypatch.setattr('src.core.bloodweb_detector.find_center',forbidden)
    with pytest.raises(ScreenError,match='item_tooltip'):manager.capture()
    assert np.array_equal(manager.last_raw,image) and manager.last_capture['tooltip_regions']


def test_real_red_connection_keeps_both_fresh_purchase_observations(root,detector):
    results=[detector.analyze(read_image(root/'tests/fixtures/v43/followup'/f'red_connection_{i}.png')) for i in (1,2)]
    for result in results:
        target=next(n for n in result.nodes if math.dist((n.x,n.y),(732,247))<5)
        assert target.state==NodeState.PURCHASED and target.state_confidence>=.8
        assert target.icon_id=='survivor_addon_009'
        neighbor=next(n for n in result.nodes if math.dist((n.x,n.y),(598,248))<6)
        assert (neighbor.id,target.id) in result.edges or (target.id,neighbor.id) in result.edges
    before=copy.deepcopy(results[0]);target=next(n for n in before.nodes if n.icon_id=='survivor_addon_009')
    target.state=NodeState.AVAILABLE
    # A replay of the two independent real captures supplies the two required
    # red observations within the original timeout, without a second press.
    class Replay(Device):
        def capture(self):
            frame=super().capture()
            frame.image=np.full((840,840,3),self.last,np.uint8)
            frame.roi=Rect(0,0,840,840)
            frame.window=WindowInfo(1,'Test replay',Rect(-840,0,840,840),Rect(-840,0,840,840),96,True)
            return frame
    d=Replay([before,before,*results]);e=engine(d,dry_run=False)
    frame=d.capture();d.index=0;e.calibration_target=(target.x,target.y,frame.window.signature,frame.roi)
    e.run(calibrate=True)
    assert e.receipt['verified'] and e.actions==1 and len(d.presses)==1


def test_red_background_is_not_a_purchased_connection(detector):
    # Continuous red needs non-red surroundings, not merely red pixels along
    # an imagined edge across a notification or broad visual effect.
    image=np.zeros((400,400,3),np.uint8);image[:]=(0,0,220)
    result=web(NodeState.PURCHASED)
    assert detector._edges(image,result.nodes)==[]


def test_recheck_cannot_fall_back_past_later_physical_actions(tmp_path):
    import os
    from src.core.calibration_recheck import previous_attempt
    frame=Device([web()]).capture();snapshot=tmp_path/'before.png';snapshot.write_bytes(b'original-evidence')
    events=[dict(stage='analysis',time='2026-10-12T11:00:00+00:00',frame=frame.metadata(),nodes=[dict(id=1,icon='target',state='available')]),
        dict(stage='before_purchase',time='2026-10-12T11:00:01+00:00',node=1,position=[230,200]),
        dict(stage='GetPhysicalCursorPos',result=1,actual=[-170,200]),
        dict(stage='SendInput',flag=2,result=1,time='2026-10-12T11:00:02+00:00'),
        dict(stage='SendInput',flag=4,result=1,time='2026-10-12T11:00:02.050000+00:00'),
        dict(stage='snapshot',path=str(snapshot)),dict(stage='failure',reason='purchase_not_confirmed')]
    old=tmp_path/'old.json';old.write_text(json.dumps(dict(entries=events)));os.utime(old,(100,100))
    assert previous_attempt(tmp_path)['target']['icon']=='target'
    later=tmp_path/'later.json'
    # A later success cannot be attributed to the old failed single purchase.
    later.write_text(json.dumps(dict(entries=events[:-1])));os.utime(later,(200,200))
    assert previous_attempt(tmp_path) is None
    # A failed multi-purchase session also cannot authorize single calibration.
    later.write_text(json.dumps(dict(entries=events+events)));os.utime(later,(201,201))
    assert previous_attempt(tmp_path) is None
    later.write_text(json.dumps(dict(entries=events[3:])));os.utime(later,(202,202))
    assert previous_attempt(tmp_path) is None  # Incomplete newer input evidence.
