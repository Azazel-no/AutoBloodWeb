"""Real serialized native map regression; array is never detection evidence."""
import copy,json,time
from dataclasses import replace
import numpy as np
import pytest
from src.core.models import Analysis,Node,NodeState,NodeType,ScreenState
from src.core.screen_manager import CapturedFrame,WindowInfo,Rect
from src.core.level_transition import idle_forward_transition
from tests.test_automation import Device,engine


def native_case(root):
    data=json.loads((root/'tests/fixtures/v55/dark_lord_idle_7_to_15.json').read_text(encoding='utf-8'))
    values=copy.deepcopy(data['analysis'])
    for node in values['nodes']:node['state']=NodeState(node['state']);node['type']=NodeType(node['type'])
    values['nodes']=[Node(**n) for n in values['nodes']]
    values['screen_state']=ScreenState(values['screen_state']);scene=Analysis(**values)
    meta=data['frame'];window=dict(meta['window']);window['client']=Rect(**window['client']);window['monitor']=Rect(**window['monitor'])
    w,h=meta['image_size'];frame=CapturedFrame(np.zeros((h,w,3),np.uint8),WindowInfo(**window),Rect(**meta['roi']),time.monotonic(),
        bloodpoints=meta['bloodpoints'],level=meta['level'],level_confidence=meta['level_confidence'])
    return data['old_scene'],frame,scene


@pytest.mark.parametrize('negative',[None,'pending','chain','header','old_header','few_new_ids','owned','weak_glyph','geometry','probe','regression','same_layout'])
def test_resolved_level7_to_15_restart_needs_new_header_layout_and_known_ids(root,negative):
    old,frame,scene=native_case(root);comparison=copy.deepcopy(old);comparison['window']=vars(frame.window)
    # Use the same serialization as the native scene-memory contract.
    from dataclasses import asdict
    comparison['window']=asdict(frame.window)
    if negative=='pending':comparison['pending']={'point':[1,2],'radius':20,'icon':'old'}
    elif negative=='chain':comparison['pending']={'chain':{'physical_points':[[1,2],[3,4]]}}
    elif negative=='header':scene.level_confidence=.94
    elif negative=='old_header':comparison['level_confidence']=.94
    elif negative=='few_new_ids':
        for n in scene.nodes:
            if n.icon_id:n.icon_id='wiki_killer_dark_lord_addons_91a238e129fd'
    elif negative=='owned':scene.nodes[1].state=NodeState.PURCHASED
    elif negative=='weak_glyph':
        for n in scene.nodes:n.confidence=.85
    elif negative=='geometry':scene.nodes[1].geometry_confidence=.64
    elif negative=='probe':frame=replace(frame,probe_only=True)
    elif negative=='regression':scene.level=6
    elif negative=='same_layout':
        from src.core.scene_memory import record_scene
        comparison['nodes']=record_scene(frame,scene,[])['nodes']
    assert idle_forward_transition(comparison,frame,scene)==(negative is None)


