import copy
import json
from dataclasses import fields,replace
import numpy as np
import pytest
from src.core.models import Node,NodeState,NodeType,Analysis,ScreenState
from src.core.screen_manager import CapturedFrame,WindowInfo,Rect,ScreenError
from src.core.level_transition import terminal_single_on_next_level,pending_target_on_proven_next_level
from tests.test_automation import Device,engine,web


def observations(root):
    data=json.loads((root/'tests/fixtures/rc1/terminal15.json').read_text(encoding='utf-8'))
    rows=[]
    for row in data['observations']:
        m=row['frame'];w=dict(m['window']);w['client']=Rect(**w['client']);w['monitor']=Rect(**w['monitor'])
        frame=CapturedFrame(np.zeros((m['image_size'][1],m['image_size'][0],3),np.uint8),WindowInfo(**w),Rect(**m['roi']),
                            m['captured_at'],bloodpoints=m['bloodpoints'],level=m['level'],level_confidence=m['level_confidence'])
        raw=copy.deepcopy(row['analysis']);raw['screen_state']=ScreenState(raw['screen_state'])
        raw['nodes']=[Node(**dict(n,state=NodeState(n['state']),type=NodeType(n['type']))) for n in raw['nodes']]
        result=Analysis(**{k:v for k,v in raw.items() if k in {f.name for f in fields(Analysis)}})
        rows.append((frame,result))
    return data['previous'],rows


def test_native_terminal15_reused_unknown_slot_needs_two_full_new_maps_and_no_credit(root):
    previous,rows=observations(root);d=Device([web()]);r=engine(d,dry_run=False)
    r.signature=rows[0][0].window.signature;r.persisted_scene=copy.deepcopy(previous);r.attempted=[tuple(p) for p in previous['attempted']]
    assert terminal_single_on_next_level(previous,*rows[0])
    assert pending_target_on_proven_next_level(previous,*rows[0])
    assert not r.recheck_saved_scene(*rows[0]) and r.attempted
    assert not r.recheck_saved_scene(*rows[0])
    assert r.recheck_saved_scene(*rows[1])
    assert not r.attempted and r.persisted_scene is None and not r.scene_record['pending']
    assert r.actions==r.attempts==0 and not d.presses
    assert any(n.icon_id is None and n.state==NodeState.LOCKED for n in rows[1][1].nodes)
    assert previous['pending']['operation_id']=='f3b1a7cfc8c0d55ffcc654836a304ffa'


@pytest.mark.parametrize('defect',['nonterminal','weak_header','wrong_level','weak_map','owned','probe','partial','chain','block','automatic'])
def test_terminal_recovery_cannot_retire_nonterminal_or_weak_pending(root,defect):
    previous,rows=observations(root);frame,result=rows[0]
    if defect=='nonterminal':previous['nodes'][1]['state']='available'
    if defect=='weak_header':result.level_confidence=.94
    if defect=='wrong_level':result.level=17
    if defect=='weak_map':result.nodes[1].confidence=.85;result.nodes[2].confidence=.85
    if defect=='owned':result.nodes[1].state=NodeState.PURCHASED
    if defect=='probe':frame=replace(frame,probe_only=True)
    if defect=='partial':frame=replace(frame,confirmation_only=True)
    if defect in ('chain','block','automatic'):
        previous['pending'][{'chain':'chain','block':'chain_blocked_reason','automatic':'automatic'}[defect]]=True
    assert not terminal_single_on_next_level(previous,frame,result)
