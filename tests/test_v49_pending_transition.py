"""Saved Unknown47 pending point versus actual two independent level48 frames."""
import copy,json
import time
from dataclasses import replace
import pytest
from src.core.automation import Automation,Settings
from src.core.level_transition import pending_target_on_proven_next_level
from src.core.models import NodeState
from src.core.screen_manager import ScreenError
from scripts.inspect_opening_failure_v49 import stored_frame,stored_analysis


class NoInput:
    def purchase(self,*args):raise AssertionError('no input in saved-scene tests')
    def release(self):pass


def evidence(root):
    folder=root/'debug/v49/live_session_b7911136'
    old=json.loads((folder/'preferences_before.json').read_text(encoding='utf-8'))['purchase_scenes']['killer/unknown']
    records=json.loads((folder/'frames.json').read_text(encoding='utf-8'))[:2]
    return old,[(stored_frame(r),stored_analysis(r['analysis_id'])) for r in records]


def engine_for(old,frame):
    device=NoInput();engine=Automation(device,device,device,[],Settings(),approved=True,hotkeys_ready=True)
    engine.signature=frame.window.signature
    engine.persisted_scene=copy.deepcopy(old);engine.attempted=[tuple(p) for p in old['attempted']]
    return engine


def test_two_actual_next_level_frames_retire_unknown_pending_without_purchase(root):
    old,pairs=evidence(root);assert old['level']==47 and old['pending']['icon'] is None
    assert all(pending_target_on_proven_next_level(old,f,a) for f,a in pairs)
    engine=engine_for(old,pairs[0][0])
    assert not engine.recheck_saved_scene(*pairs[0])
    assert engine.persisted_scene and engine.persisted_scene['pending']
    assert not engine.recheck_saved_scene(*pairs[0])  # same capture cannot count twice
    assert engine.recheck_saved_scene(*pairs[1])
    assert engine.persisted_scene is None and engine.scene_record['pending'] is None
    assert engine.actions==engine.attempts==0 and not engine.attempted and not engine.pending_action
    assert old['pending'] is not None  # caller/profile evidence was not modified


def test_scene_proof_reuses_first_full_map_but_cannot_authorize_early_buy(root,library,monkeypatch):
    from src.core.bloodweb_detector import BloodwebDetector
    from tests.test_automation import Device
    old,pairs=evidence(root)
    frames=iter(f for f,_ in pairs)
    device=Device([])
    device.capture=lambda:replace(next(frames),captured_at=time.monotonic())
    detector=BloodwebDetector(library,'killer','unknown')
    auth=json.loads((root/'debug/v49/live_permission_20261014_unknown_opening5.json').read_text(encoding='utf-8'))
    engine=Automation(device,detector,device,auth['priorities'],Settings(dry_run=False,startup_delay=0),approved=True,hotkeys_ready=True)
    engine.persisted_scene=copy.deepcopy(old);engine.attempted=[tuple(p) for p in old['attempted']]
    first,result,plan=engine.read()
    assert engine.opening.active and engine.opening.full_scans==1
    assert not engine.recheck_saved_scene(first,result)
    target=next(n for n in result.nodes if n.id==plan.next_node)
    with pytest.raises(ScreenError,match='purchase_result_unknown'):
        engine._buy_once(first,result,node=target)
    assert engine.attempts==0 and not engine.pending_action and not device.presses
    def forbidden(*args,**kwargs):raise AssertionError('full work during second scene proof')
    monkeypatch.setattr(detector,'analyze_live',forbidden)
    monkeypatch.setattr(detector.matcher,'match',forbidden)
    monkeypatch.setattr('cv2.HoughCircles',forbidden)
    monkeypatch.setattr('src.core.automation.plan_route',forbidden)
    monkeypatch.setattr('src.core.fast_opening.plan_route',forbidden)
    second,compact,_=engine.read()
    assert engine.recheck_saved_scene(second,compact)
    plan=engine.opening.plan(compact,engine.actions,engine.attempted_nodes(second,compact))
    assert plan.next_node is not None and engine.persisted_scene is None
    assert engine.opening.full_scans==1 and engine.opening.quick_checks==1
    assert engine.attempts==engine.actions==0 and not device.presses


@pytest.mark.parametrize('contradiction',['same_level','poor_header','one_changed_glyph','red_target','uncertain','window','probe','focus','weak_glyph'])
def test_unknown_pending_is_not_cleared_by_insufficient_next_level_evidence(root,contradiction):
    from src.core.purchase_outcome import physical_node
    old,pairs=evidence(root);frame,result=copy.deepcopy(pairs[0]);memory=copy.deepcopy(old)
    if contradiction=='same_level':result.level=47
    if contradiction=='poor_header':result.level_confidence=.94
    if contradiction=='one_changed_glyph':
        first=next(node for node in memory['nodes'] if node.get('icon'))
        for node in memory['nodes']:
            if node is not first:node['icon']=None
    if contradiction=='red_target':physical_node(frame,result,memory['pending']['point'],memory['pending']['radius']).state=NodeState.PURCHASED
    if contradiction=='uncertain':result.nodes[-1].state=NodeState.UNCERTAIN
    if contradiction=='window':frame.window=replace(frame.window,pid=frame.window.pid+1)
    if contradiction=='probe':frame.probe_only=True
    if contradiction=='focus':frame.window=replace(frame.window,foreground=False)
    if contradiction=='weak_glyph':next(n for n in result.nodes if n.icon_id).confidence=.85
    assert not pending_target_on_proven_next_level(memory,frame,result)
