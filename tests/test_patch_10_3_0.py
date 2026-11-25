"""Patch 10.3.0 Bloodweb (TASKS 2026-11-25, A): smaller nodes and centre glyph, web ~32 px higher,
purchased tiles dimmed inside a glowing red ring. Four real 1920x1080 frames, HUD 100 %
(the player's own name and portrait at the top right are blacked out in the fixtures)."""
import math

import pytest

from scripts.analyze_patch_10_3_0 import analyze
from src.core.run_summary import RunSummary

FIXTURES='tests/fixtures/patch_10_3_0'
# Full-frame positions of every node, by state; measured by hand on the frames.
EXPECTED={
    'terrifier_level5.png':dict(purchased=[(620,328),(468,492),(585,495),(590,580)],available=[(680,640)],locked=[],absorbed=[],next=(680,640)),
    'terrifier_level6.png':dict(purchased=[],available=[(775,492),(680,640),(590,580)],locked=[(898,597)],absorbed=[],next=(775,492)),
    'cheryl_level34.png':dict(purchased=[],available=[(680,440),(773,492),(585,495),(588,580),(770,580),(680,640)],
        locked=[(390,368),(518,386),(467,492),(517,686),(845,686),(390,725),(620,740),(988,715),(508,845),(857,838)],absorbed=[],next=None),
    'dwight_level43.png':dict(purchased=[(680,440),(773,493),(898,488),(770,580),(588,580)],
        available=[(620,328),(735,327),(520,388),(467,490),(585,495),(467,590),(680,640),(1030,537)],
        locked=[(513,242),(860,233),(333,563),(520,688),(390,727)],absorbed=[(680,190)],next=None),
}


@pytest.fixture(scope='module')
def rows(library,root):
    found=analyze(root/FIXTURES,library,priorities_for=lambda role,character:['any_perk'])
    return {r['frame']:r for r in found}


@pytest.mark.parametrize('frame',sorted(EXPECTED))
def test_patch_10_3_0_web_is_read_completely(rows,frame):
    row=rows[frame];expected=EXPECTED[frame]
    assert row['screen']=='bloodweb' and row['center'][:2]==(680,530) and row['center'][2]==33
    nodes=[n for n in row['node_list'] if n['state']!='center']
    assert len(nodes)==sum(len(expected[s]) for s in ('purchased','available','locked','absorbed'))
    for state in ('purchased','available','locked','absorbed'):
        for x,y in expected[state]:
            near=[n for n in nodes if math.dist((x,y),n['xy'])<12]
            assert near and near[0]['state']==state,(frame,x,y,state,near)
    # Scenery (chat box, balloon, black spikes, buttons) is never a node.
    assert all(n['xy'][0]<1100 and n['xy'][1]>150 for n in nodes)
    if expected['next']:assert math.dist(row['next']['xy'],expected['next'])<12


def test_level_header_reads_on_10_3_0(rows):
    assert {f:rows[f]['level'] for f in rows}=={'terrifier_level5.png':5,'terrifier_level6.png':6,'cheryl_level34.png':34,'dwight_level43.png':43}


def test_art_the_clown_addons_come_from_the_library(rows):
    art=[n for r in ('terrifier_level5.png','terrifier_level6.png') for n in rows[r]['node_list'] if n['state']=='available' and n['type']=='addon']
    assert art and all(n['icon'].startswith('wiki_killer_terrifier_addons_') and n['conf']>=.9 for n in art)


def test_run_summary_counts_levels_pauses_and_unknown_nodes(tmp_path):
    summary=RunSummary('killer','terrifier')
    summary.observe('new_level_confirmed',dict(level=6))
    summary.observe('new_level_confirmed',dict(level=6))      # the same level confirmed twice counts once
    summary.observe('new_level_confirmed',dict(level=7))
    summary.observe('unknown_node',dict(level=7,node=3,rarity='blue',category='addons',crop='x.png'))
    summary.observe('supervisor_recovery',dict(reason='stale_frame',attempt=1))
    summary.observe('modal_input',dict(kind='reward_popup'))
    summary.observe('state',dict(message='purchase_result_unknown'),'paused')
    summary.observe('state',dict(message='interrupted'),'stopped')
    data=summary.data('stopped')
    assert data['levels_done']==2 and len(data['unknown_nodes'])==1 and len(data['recoveries'])==1
    assert data['popups_handled']==1 and [p['reason'] for p in data['pauses']]==['purchase_result_unknown']
    assert data['stop_reason']=='purchase_result_unknown'
    path=summary.write(tmp_path,'stopped')
    assert path.read_text(encoding='utf-8').startswith('AutoBloodWeb run') and path.with_suffix('.json').is_file()


def test_unknown_node_is_logged_once_with_a_crop_and_never_pauses(monkeypatch,tmp_path):
    from types import SimpleNamespace
    import numpy as np
    from src.core.automation import Automation
    from src.core.models import Analysis,Node,NodeState,NodeType,ScreenState
    monkeypatch.setattr('src.core.runtime_paths.project_root',lambda:tmp_path)
    traces=[]
    engine=SimpleNamespace(evidence_writer=None,UNKNOWN_NODE_LIMIT=200,report=SimpleNamespace(path=tmp_path/'run.json'),trace=lambda stage,**d:traces.append((stage,d)))
    result=Analysis(400,400,ScreenState.BLOODWEB,level=12,nodes=[Node(0,200,200,33,NodeState.CENTER,NodeType.CENTER),
        Node(1,300,200,30,NodeState.AVAILABLE,NodeType.UNKNOWN,rarity='blue'),
        Node(2,100,200,30,NodeState.AVAILABLE,NodeType.ADDON,icon_id='survivor_addon_001'),
        Node(3,200,100,30,NodeState.LOCKED,NodeType.PERK)])
    frame=SimpleNamespace(image=np.zeros((400,400,3),np.uint8))
    for _ in range(2):Automation.note_unknown_nodes(engine,frame,result)
    assert [d['node'] for s,d in traces if s=='unknown_node']==[1]
    assert list((tmp_path/'debug/unknown_nodes').glob('*_L12_01.png'))


def test_red_ringed_centre_is_found_locally_at_its_own_place_and_size(root):
    """Live 25.11 (Cheryl, P10 level 48): a centre ringed by purchased nodes scores 0.87, under the
    local 0.94 threshold; every read fell to the 0.6 s global search and aged past 2 s."""
    import cv2,time
    from src.core.bloodweb_detector import find_center
    from src.core.bloodweb_header import read_level
    image=cv2.imread(str(root/'tests/fixtures/patch_10_3_0_center/cheryl_level48_red_center.png'))
    level=read_level(image,root);assert level[0]==48
    started=time.perf_counter()
    assert find_center(image,root,hint=(680,530,33),local_only=True,observed_level=level)==(680,530,33)
    assert time.perf_counter()-started<.5
    assert find_center(image,root,hint=(680,530,40),local_only=True,observed_level=level) is None   # another size is not this glyph
