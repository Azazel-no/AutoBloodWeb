import copy
from dataclasses import replace
import pytest
from types import SimpleNamespace
from tests.test_automation import Device, engine, web
from src.core.models import Node, NodeState, NodeType
from src.core.scene_memory import record_scene
from src.core.screen_manager import Rect, ScreenError


def resolved():
    result = web(NodeState.PURCHASED)
    result.level = 9
    result.level_confidence = .99
    for node in result.nodes:
        node.geometry_confidence=.95;node.confidence=.95
        node.contour_score=.9;node.icon_margin=.1
    for i, (x, y) in enumerate(((100, 100), (250, 100), (300, 250)), 2):
        result.nodes.append(Node(i, x, y, 20, NodeState.AVAILABLE, NodeType.ITEM,
                                 'known'+str(i), confidence=.95, contour_score=.9,
                                 icon_margin=.1, geometry_confidence=.95, state_confidence=.95))
    d = Device([result])
    f = d.capture()
    memory = record_scene(f, result, [f.to_screen(230, 200)])
    return d, f, result, memory


@pytest.mark.parametrize('client,dpi', [(Rect(-400,0,400,400),144),
    (Rect(80,60,400,400),96), (Rect(1920,0,800,800),192), (Rect(0,0,600,400),120)])
def test_resolved_same_web_rebinds_current_window_after_two_native_maps(client,dpi):
    d, f, result, memory = resolved()
    fresh = replace(f, window=replace(f.window, hwnd=99, pid=77, client=client,
                    monitor=Rect(1920,0,1920,1080), dpi=dpi), roi=Rect(0,0,client.width,client.height))
    r=engine(d,dry_run=False);r.signature=fresh.window.signature;r.persisted_scene=copy.deepcopy(memory)
    r.attempted=[tuple(p) for p in memory['attempted']]
    assert not r.recheck_saved_scene(fresh,result)
    assert not r.recheck_saved_scene(fresh,result)
    assert r.recheck_saved_scene(replace(fresh,captured_at=fresh.captured_at+.1),result)
    assert r.actions==r.attempts==0 and not d.presses
    assert r.attempted==[fresh.to_screen(230,200)] and not r.persisted_scene
    assert r.scene_record['window']['pid']==77 and memory['window']['pid']!=77


@pytest.mark.parametrize('defect', ['pending','blocked','probe','focus'])
def test_rebind_never_clears_unproven_attempts_or_pending(defect):
    d,f,result,memory=resolved()
    fresh=replace(f,window=replace(f.window,hwnd=99,pid=77))
    if defect=='attempt_unowned':result.nodes[1].state=NodeState.AVAILABLE
    if defect=='glyph_conflict':result.nodes[2].icon_id='different'
    if defect=='weak':result.nodes[2].geometry_confidence=.64
    if defect in ('pending','blocked'):
        memory['pending']=dict(point=list(f.to_screen(230,200)),radius=20,icon='target')
        if defect=='blocked':memory['pending']['chain_blocked_reason']='chain_map_changed'
    if defect=='probe':fresh=replace(fresh,probe_only=True)
    if defect=='focus':fresh=replace(fresh,window=replace(fresh.window,foreground=False))
    r=engine(d,dry_run=False);r.signature=fresh.window.signature;r.persisted_scene=memory
    try:
        assert not r.recheck_saved_scene(fresh,result)
        assert not r.recheck_saved_scene(replace(fresh,captured_at=fresh.captured_at+.1),result)
    except ScreenError:pass
    assert r.persisted_scene and r.actions==r.attempts==0 and not d.presses


def test_runtime_move_invalidates_prepared_map_and_waits_for_two_observations():
    d,f,result,memory=resolved();r=engine(d,dry_run=False)
    r.signature=f.window.signature;r.scene_record=copy.deepcopy(memory);r.attempted=[tuple(p) for p in memory['attempted']]
    d.window=replace(d.window,client=Rect(200,100,400,400))
    d.backend=SimpleNamespace(info=lambda:d.window)
    r.direct_price_observations={'target':{'value':5000}}
    fresh=r.capture_frame()
    assert r.persisted_scene==memory and r.direct_price_observations=={}
    assert r.signature==fresh.window.signature and not r.recheck_saved_scene(fresh,result)
    assert r.recheck_saved_scene(replace(fresh,captured_at=fresh.captured_at+.1),result)
    assert r.attempted==[fresh.to_screen(230,200)] and not d.presses


def test_runtime_geometry_change_during_paid_operation_never_rebinds_or_retries():
    d,f,result,memory=resolved();r=engine(d,dry_run=False);r.signature=f.window.signature
    r.scene_record=memory;r.pending_action=True
    d.window=replace(d.window,dpi=192);d.backend=SimpleNamespace(info=lambda:d.window)
    with pytest.raises(ScreenError,match='window_changed'):r.capture_frame()
    assert r.signature==f.window.signature and r.pending_action and not d.presses


def test_rebind_requires_two_matching_state_observations():
    d,f,result,memory=resolved();fresh=replace(f,window=replace(f.window,hwnd=99))
    r=engine(d,dry_run=False);r.signature=fresh.window.signature;r.persisted_scene=memory
    assert not r.recheck_saved_scene(fresh,result)
    other=copy.deepcopy(result);other.nodes[2].state=NodeState.PURCHASED
    assert not r.recheck_saved_scene(replace(fresh,captured_at=fresh.captured_at+.1),other)
    assert r.recheck_saved_scene(replace(fresh,captured_at=fresh.captured_at+.2),other)
