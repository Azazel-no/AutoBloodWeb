import json
from pathlib import Path
import cv2
import numpy as np
import pytest

from src.core.models import ScreenState, NodeType
from src.utils.images import read_image

ANNOTATIONS=json.loads((Path(__file__).parent/'fixtures/bloodweb_annotations.json').read_text())
ANNOTATIONS.update(json.loads((Path(__file__).parent/'fixtures/bloodweb_v2_annotations.json').read_text()))


@pytest.mark.parametrize('name',list(ANNOTATIONS))
def test_nodes_states_and_real_icon_matches(name,root,detector):
    annotation=ANNOTATIONS[name]
    image=read_image(root/'references/bloodweb'/name)
    result=detector.analyze(image,source='arbitrary_filename.png')
    assert result.screen_state==annotation['state']
    assert len(result.nodes)==len(annotation['nodes'])
    used=set()
    for x,y,state in annotation['nodes']:
        node=min(result.nodes,key=lambda n:np.hypot(n.x-x,n.y-y))
        assert np.hypot(node.x-x,node.y-y)<20,(name,x,y)
        assert node.id not in used
        used.add(node.id)
        assert node.state==state,(name,x,y,node.state)
    for x,y,ident in annotation['icons']:
        node=min(result.nodes,key=lambda n:np.hypot(n.x-x,n.y-y))
        assert node.icon_id==ident,(name,ident,node)
    assert not result.safe_to_automate
    def edge_ids(pair):
        return tuple(sorted(min(result.nodes,key=lambda n:np.hypot(n.x-x,n.y-y)).id for x,y in pair))
    edges={tuple(sorted(edge)) for edge in result.edges}
    for pair in annotation.get('edges',[]): assert edge_ids(pair) in edges,(name,pair)
    for pair in annotation.get('absent_edges',[]): assert edge_ids(pair) not in edges,(name,pair)


@pytest.mark.parametrize('name,state,smaller_state',[
    ('bloodweb_prestige_node.png',ScreenState.PRESTIGE_READY,ScreenState.PRESTIGE),
    ('bloodweb_prestige_reward_ok.png',ScreenState.REWARD,ScreenState.REWARD),
    ('bloodweb_level43_finished_waiting_refresh.png',ScreenState.REFRESH,ScreenState.REFRESH),
])
def test_special_states_from_pixels(name,state,smaller_state,root,detector):
    image=read_image(root/'references/bloodweb'/name)
    assert detector.analyze(image).screen_state==state
    smaller=cv2.resize(image,None,fx=.8,fy=.8,interpolation=cv2.INTER_AREA)
    # The resized wreath lacks the .97 certificate; retain conservative manual
    # PRESTIGE classification instead of authorizing the paid ready action.
    assert detector.analyze(smaller,source='unrelated.png').screen_state==smaller_state


def test_unknown_image_is_safe(detector):
    result=detector.analyze(np.zeros((800,900,3),np.uint8),source='bloodweb_level43_start.png')
    assert result.screen_state==ScreenState.UNKNOWN
    assert not result.nodes and not result.safe_to_automate
    result=detector.analyze(np.random.default_rng(3).integers(0,255,(800,900,3),dtype=np.uint8))
    assert result.screen_state==ScreenState.UNKNOWN
    assert not result.safe_to_automate


def test_resized_screenshot_has_actual_matches(root,detector):
    image=read_image(root/'references/bloodweb/bloodweb_level43_start.png')
    smaller=cv2.resize(image,None,fx=.8,fy=.8,interpolation=cv2.INTER_AREA)
    result=detector.analyze(smaller)
    assert result.screen_state==ScreenState.BLOODWEB
    assert 17<=len(result.nodes)<=22
    assert sum(n.icon_id is not None for n in result.nodes)>=3
    assert any(n.type==NodeType.PERK for n in result.nodes)


def test_debug_output(detector,root):
    image=read_image(root/'references/bloodweb/bloodweb_level43_start.png')
    result=detector.analyze(image)
    output=root/'debug/tests/analysis.png'
    detector.save_debug(image,result,output)
    assert read_image(output).shape==image.shape
    payload=json.loads(output.with_suffix('.json').read_text(encoding='utf-8'))
    assert len(payload['nodes'])==20 and not payload['safe_to_automate']


def test_actual_available_target_route(detector,root):
    from src.core.path_planner import plan_route
    image=read_image(root/'references/bloodweb/bloodweb_level43_start.png')
    result=detector.analyze(image)
    target=next(n for n in result.nodes if n.icon_id=='survivor_offering_044')
    assert (0,target.id) in result.edges
    plan=plan_route(result,{'survivor_offering_044'})
    assert plan.reason=='priority' and plan.next_node==target.id


def test_hexagonal_offerings_are_not_perks(root,detector):
    result=detector.analyze(read_image(root/'references/bloodweb/bloodweb_no_perks_example.png'))
    assert not any(n.type==NodeType.PERK for n in result.nodes)
    result=detector.analyze(read_image(root/'references/bloodweb/bloodweb_level44_start.png'))
    hand=min(result.nodes,key=lambda n:np.hypot(n.x-403,n.y-327))
    assert hand.type==NodeType.OFFERING and hand.icon_id=='survivor_offering_006'
