"""Hotfix 1.0.1 B: a saved scene from another web (another survivor) must not block this one.

All survivors share one saved purchase scene. Live 11.11: the clicks of a level 28
survivor were restored onto Quentin Smith's level 1 web, landed on its real nodes and
held back the centre auto-buy (automatic_unlock_unproven four times, nothing bought).
"""
import copy
from dataclasses import replace
from tests.test_automation import Device,web,engine
from src.core.models import Node,NodeType,NodeState
from src.core.scene_memory import record_scene,foreign_web


def saved(level=28,pending=None):
    """A finished survivor level: node 1 and node 2 bought, no unresolved click."""
    d=Device([web()]);f=d.capture();old=web(NodeState.PURCHASED);old.level=level;old.level_confidence=.98
    old.nodes.append(Node(2,300,200,20,NodeState.PURCHASED,NodeType.ADDON,None,state_confidence=.95,geometry_confidence=.95))
    old.nodes.append(Node(3,230,300,20,NodeState.AVAILABLE,NodeType.OFFERING,'old_offering',state_confidence=.95,geometry_confidence=.95))
    points=[f.to_screen(n.x,n.y) for n in old.nodes[1:3]]
    return d,f,old,record_scene(f,old,points,pending),points


def other_web(level=1):
    """Another survivor's web: the same physical slots, nothing bought."""
    new=web();new.level=level;new.level_confidence=.98
    new.nodes.append(Node(2,300,200,20,NodeState.AVAILABLE,NodeType.ADDON,'other_addon',state_confidence=.95,geometry_confidence=.95))
    return new


def restored(d,f,memory,points):
    r=engine(d,dry_run=False);r.signature=f.window.signature
    r.persisted_scene=copy.deepcopy(memory);r.scene_record=copy.deepcopy(memory);r.attempted=list(points)
    return r


def test_bought_node_buyable_again_proves_another_web():
    d,f,old,memory,_=saved()
    assert foreign_web(memory,f,old) is None
    new=other_web(level=28)  # same level: only the bought slots tell the webs apart
    assert foreign_web(memory,f,new)=='owned_node_buyable_again'
    assert foreign_web(memory,f,other_web(level=1))=='level_differs'


def test_other_survivor_scene_is_dropped_after_two_fresh_reads():
    d,f,old,memory,points=saved()
    r=restored(d,f,memory,points);new=other_web(level=28)
    assert not r.recheck_saved_scene(f,new) and r.attempted
    assert r.recheck_saved_scene(replace(f,captured_at=f.captured_at+.1),new)
    assert not r.attempted and r.persisted_scene is None and r.scene_record is None and not r.pending_action
    assert not d.presses and r.actions==0
    assert any(e.get('stage')=='saved_scene_other_web_retired' for e in r.report.entries)


def test_same_web_keeps_its_scene():
    d,f,old,memory,points=saved()
    r=restored(d,f,memory,points)
    r.recheck_saved_scene(f,old);r.recheck_saved_scene(replace(f,captured_at=f.captured_at+.1),old)
    assert not any(e.get('stage')=='saved_scene_other_web_retired' for e in r.report.entries)


def test_pending_click_is_never_dropped_by_this_rule():
    d,f,old,memory,points=saved(pending=dict(point=[1,1],radius=20,icon=None))
    r=restored(d,f,memory,points);new=other_web(level=28)
    for i in range(3):
        try:r.recheck_saved_scene(replace(f,captured_at=f.captured_at+.1*i),new)
        except Exception:pass
    assert not any(e.get('stage')=='saved_scene_other_web_retired' for e in r.report.entries)


def test_far_level_pending_click_of_another_survivor_is_retired_without_credit():
    """Live 11.11: survivor 2 stopped after a level 50 centre click; survivor 3 opened at level 17."""
    d,f,old,memory,points=saved(level=50,pending=dict(point=list(f_point()),radius=20,icon=None,automatic=True))
    r=restored(d,f,memory,points);new=other_web(level=17)
    assert not r.recheck_saved_scene(f,new) and r.persisted_scene
    assert r.recheck_saved_scene(replace(f,captured_at=f.captured_at+.1),new)
    assert r.persisted_scene is None and not r.attempted and r.actions==0 and not d.presses
    retired=[e for e in r.report.entries if e.get('stage')=='saved_scene_other_level_retired']
    assert retired and retired[0]['purchase_credit']==0


def test_near_level_pending_click_still_needs_evidence():
    d,f,old,memory,points=saved(level=18,pending=dict(point=list(f_point()),radius=20,icon=None,automatic=True))
    r=restored(d,f,memory,points);new=other_web(level=17)
    for i in range(3):
        try:r.recheck_saved_scene(replace(f,captured_at=f.captured_at+.1*i),new)
        except Exception:pass
    assert not any(e.get('stage')=='saved_scene_other_level_retired' for e in r.report.entries)


def f_point():
    d=Device([web()]);f=d.capture();return f.to_screen(150,200)
