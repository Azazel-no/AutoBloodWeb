import copy,json,hashlib
from dataclasses import replace
import pytest
from src.core.level_transition import restarted_incomplete_scene
from src.core.bloodweb_detector import BloodwebDetector
from src.core.screen_manager import ScreenError
from src.utils.images import read_image
from tests.test_automation import Device,web,engine
from tests.test_v47_transition_pixels import frame as make_frame

def evidence(root,library):
    data=json.loads((root/'tests/fixtures/v48/restarted_incomplete_scene.json').read_text(encoding='utf-8'))
    detector=BloodwebDetector(library,'killer','unknown');observations=[]
    for item in data['frames']:
        path=root/item['source'];assert hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256']
        raw=read_image(path);roi=item['frame']['roi'];pixels=raw[roi['y']:roi['y']+roi['height'],roi['x']:roi['x']+roi['width']]
        fresh=make_frame(item['frame'],pixels)
        fresh.level=item['frame']['level'];fresh.level_confidence=item['frame']['level_confidence']
        observations.append((fresh,detector.analyze_live(fresh)))
    return data['prior_scene'],observations


@pytest.mark.parametrize('negative',['no_glyph_changes','two_changes','no_layout_change','unknown_level','same_level','owned_states','pending_old_target','wrong_window'])
def test_restart_geometry_and_pid_alone_cannot_authorize(negative,root,library):
    from dataclasses import asdict
    from src.core.models import NodeState
    memory,observations=evidence(root,library);fresh,result=observations[0]
    memory=copy.deepcopy(memory);memory['window']=asdict(fresh.window)
    if negative in ('no_glyph_changes','two_changes'):
        changed=0
        for old in memory['nodes']:
            if old['state'] not in ('purchased','absorbed') or not old['icon']:continue
            candidates=[n for n in result.nodes if __import__('math').dist(old['point'],fresh.to_screen(n.x,n.y))<max(6,old['radius']*.4)]
            if len(candidates)==1 and candidates[0].icon_id:
                if negative=='two_changes' and changed<2:changed+=1
                else:candidates[0].icon_id=old['icon']
    elif negative=='no_layout_change':
        from src.core.scene_memory import record_scene
        memory['nodes']=record_scene(fresh,result,[])['nodes']
    elif negative=='unknown_level':result.level=None;result.level_confidence=0
    elif negative=='same_level':memory['level']=result.level;memory['level_confidence']=.99
    elif negative=='owned_states':
        for n in result.nodes[1:]:n.state=NodeState.PURCHASED
    elif negative=='pending_old_target':
        node=next(n for n in result.nodes if n.icon_id)
        memory['pending']=dict(point=list(fresh.to_screen(node.x,node.y)),radius=node.radius,icon=node.icon_id)
    elif negative=='wrong_window':memory['window']['pid']+=1
    assert not restarted_incomplete_scene(memory,fresh,result)
