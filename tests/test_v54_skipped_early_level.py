"""Retire an old centre after manually advanced early levels; never credit it."""
import copy,time
from dataclasses import replace
import pytest
from src.core.models import NodeState,Node,NodeType
from src.core.scene_memory import record_scene
from src.core.level_transition import pending_automatic_on_next_level
from tests.test_v54_tutorial import setup,advanced_web
from tests.test_automation import web


@pytest.mark.parametrize('negative',[None,'few_known','few_new_ids','owned','level_unknown','regression','same_layout','probe','single','chain','low_geometry'])
def test_manually_skipped_early_level_requires_independent_new_map(monkeypatch,negative):
    clock,d,r=setup(monkeypatch,[advanced_web()]);frame=d.capture()
    before=web();before.level=5;before.level_confidence=.99
    pending=dict(point=list(frame.to_screen(150,200)),radius=20,icon=None,automatic=True)
    saved=record_scene(frame,before,[(1,2)],pending)
    current=advanced_web();current.level=7
    for i,(x,y) in enumerate([(60,300),(330,60)],3):
        current.nodes.append(Node(i,x,y,18,NodeState.LOCKED,NodeType.ADDON,f'new_{i}',
            confidence=.99,contour_score=.9,icon_margin=.1,geometry_confidence=.67,state_confidence=.95))
    if negative=='few_known':current.nodes[-1].icon_id=None
    elif negative=='few_new_ids':current.nodes[1].icon_id=before.nodes[1].icon_id;current.nodes[2].icon_id=before.nodes[1].icon_id
    elif negative=='owned':current.nodes[-1].state=NodeState.PURCHASED
    elif negative=='level_unknown':current.level_confidence=.9
    elif negative=='regression':current.level=4
    elif negative=='same_layout':saved['nodes']=record_scene(frame,current,[])['nodes']
    elif negative=='probe':frame=replace(frame,probe_only=True)
    elif negative=='single':pending.pop('automatic')
    elif negative=='chain':pending['chain']={'physical_points':[(1,2),(3,4)]}
    elif negative=='low_geometry':current.nodes[-1].geometry_confidence=.6
    assert pending_automatic_on_next_level(saved,frame,current)==(negative is None)
    if negative:return
    r.persisted_scene=copy.deepcopy(saved);r.attempted=[(1,2)]
    assert not r.recheck_saved_scene(frame,current)
    later=replace(frame,captured_at=frame.captured_at+.1)
    assert r.recheck_saved_scene(later,current)
    assert r.actions==0 and r.unconfirmed_level_actions==1 and not r.attempted and not d.presses
