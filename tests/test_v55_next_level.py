"""Injected next-level contract, never Windows input or purchase evidence."""
import copy
from dataclasses import replace
import pytest
from tests.test_automation import web,Device,engine
from src.core.models import Node,NodeType,NodeState
from src.core.scene_memory import record_scene
from src.core.level_transition import pristine_next_level


def scenes():
    old=web();old.level=17;old.level_confidence=.99
    old.nodes.append(replace(old.nodes[1],id=2,x=290,state=NodeState.LOCKED))
    new=web();new.level=18;new.level_confidence=.99
    new.nodes=[new.nodes[0]]+[Node(i,30+i*20,60,12,NodeState.AVAILABLE,NodeType.ITEM,'fresh'+str(i),
        confidence=.9,contour_score=.8,icon_margin=.1,geometry_confidence=.8,state_confidence=.9) for i in range(1,5)]
    return old,new


@pytest.mark.parametrize('change',[None,'header','same_level','glyph','geometry','state','layout','probe','focus','window'])
def test_new_layout_and_pristine_glyphs_are_required_to_retire_nonfinal_action(change):
    old,new=scenes();d=Device([new]);f=d.capture();memory=record_scene(f,old,[])
    if change=='header':new.level_confidence=.94
    if change=='same_level':new.level=17
    if change=='glyph':new.nodes[-1].confidence=.85
    if change=='geometry':new.nodes[-1].geometry_confidence=.64
    if change=='state':new.nodes[-1].state=NodeState.PURCHASED
    if change=='layout':new.nodes=copy.deepcopy(old.nodes)
    if change=='probe':f.probe_only=True
    if change=='focus':f.window=replace(f.window,foreground=False)
    if change=='window':f.window=replace(f.window,pid=9)
    assert pristine_next_level(memory,f,new)==(change is None)


def test_two_new_maps_retire_action_without_old_purchase_credit_or_retry():
    old,new=scenes();d=Device([new,new]);r=engine(d,dry_run=False)
    frame=d.capture();d.index=0;r.signature=frame.window.signature
    calls=[]
    def retired(*args):
        calls.append(d.index)
        return 'retired'
    r.finish_level_attempt=retired
    assert r._buy_once(frame,old,old.nodes[1])=='retired'
    assert calls==[2] and len(d.presses)==1 and r.actions==0


def test_absent_single_uses_same_live_and_resume_contract_with_unrelated_weak_new_rim():
    old,new=scenes();new.nodes[-1].geometry_confidence=.629
    old.nodes[1].icon_id=None
    d=Device([new,new]);r=engine(d,dry_run=False)
    frame=d.capture();d.index=0;r.signature=frame.window.signature
    retired=[];r.finish_level_attempt=lambda *args:retired.append(d.index) or 'retired'
    assert r._buy_once(frame,old,old.nodes[1])=='retired'
    assert retired==[2] and len(d.presses)==1 and r.actions==0
    assert r.persisted_scene is None


@pytest.mark.parametrize('submitted',[False,True])
def test_preinput_failure_does_not_create_phantom_pending_but_down_attempt_stays_blocked(submitted):
    old,_=scenes();d=Device([old]);d.down_attempt_sequence=0;r=engine(d,dry_run=False)
    frame=d.capture();r.signature=frame.window.signature
    def fail(*args):
        if submitted:d.down_attempt_sequence+=1
        raise PermissionError('ledger commit denied before native input')
    d.purchase=fail
    with pytest.raises(PermissionError):r._buy_once(frame,old,old.nodes[1])
    assert r.pending_action==submitted
    assert bool(r.scene_record['pending'])==submitted
    assert bool(r.attempted)==submitted and r.actions==0


def test_wait_next_discards_stale_frame_then_requires_two_new_captures(monkeypatch):
    from src.core.screen_manager import ScreenError
    old,new=scenes();d=Device([new,new,new]);r=engine(d,dry_run=False)
    before=d.capture();d.index=0;r.signature=before.window.signature
    original=r.read;reads=[]
    def read():
        reads.append(True)
        if len(reads)==1:raise ScreenError('stale_frame')
        return original()
    r.read=read;r.wait=lambda _:None
    frame,result,_=r.wait_next_bloodweb(before,old)
    assert result.level==18 and len(reads)==3 and not d.presses
    assert sum(e['stage']=='new_level_confirmed' for e in r.report.entries)==1


def test_stop_at_new_level_keeps_a_saveable_scene_instead_of_old_pending():
    old,new=scenes();d=Device([new,new,new]);r=engine(d,dry_run=False)
    before=d.capture();d.index=0;r.signature=before.window.signature
    r.scene_record=record_scene(before,old,[(1,2)],dict(point=[1,2],radius=20,icon='old'))
    outcome=dict(operation_id='old_confirmed',authorizes_input=False)
    r.scene_record['last_outcome']=outcome
    r.wait=lambda _:None
    r.wait_next_bloodweb(before,old)
    r.halt()
    assert r.scene_record['level']==18 and r.scene_record['pending'] is None
    assert r.scene_record['attempted']==[] and not r.attempted
    assert r.scene_record['last_outcome']==outcome and not d.presses
