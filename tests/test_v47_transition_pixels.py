import hashlib,json,time
from dataclasses import asdict
import numpy as np
from src.core.models import Analysis,Node,NodeState,NodeType,ScreenState
from src.core.screen_manager import CapturedFrame,Rect,WindowInfo
from src.core.scene_memory import record_scene
from src.core.level_transition import new_level
from src.core.bloodweb_detector import BloodwebDetector
from src.utils.images import read_image

def frame(metadata,pixels):
    w=metadata['window'];window=WindowInfo(w['hwnd'],w['title'],Rect(**w['client']),Rect(**w['monitor']),w['dpi'],True,pid=w['pid'])
    return CapturedFrame(pixels,window,Rect(**metadata['roi']),time.monotonic(),live=True)

def test_actual_last_old_graph_and_new_cheryl_pixels_are_distinct(root,library):
    data=json.loads((root/'tests/fixtures/v47/transition_evidence.json').read_text(encoding='utf-8'));old=data['before'];width,height=old['frame']['image_size']
    old_frame=frame(old['frame'],np.zeros((height,width,3),np.uint8))
    nodes=[Node(n['id'],n['x'],n['y'],32,NodeState(n['state']),NodeType(n['type']),n['icon']) for n in old['nodes']]
    before=Analysis(width,height,ScreenState.BLOODWEB,nodes)
    record=record_scene(old_frame,before,[])
    results=[]
    for item in data['frames']:
        path=root/item['source'];assert hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256']
        raw=read_image(path);roi=item['frame']['roi'];pixels=raw[roi['y']:roi['y']+roi['height'],roi['x']:roi['x']+roi['width']]
        fresh=frame(item['frame'],pixels);result=BloodwebDetector(library).analyze_live(fresh);results.append(result)
        assert new_level(record,fresh,result) and result.screen_state==ScreenState.BLOODWEB
    assert len(results)==2

def test_level39_after_action_with_residual_locked_unknown_is_not_old_target(root,library):
    from src.core.models import RecognitionStatus
    from src.core.level_transition import final_target,known_glyph_transition
    from src.core.path_planner import plan_route
    from tests.test_automation import Device,engine
    data=json.loads((root/'tests/fixtures/v47/level39_transition_evidence.json').read_text(encoding='utf-8'))
    a=data['before_analysis'];nodes=[]
    for stored in a['nodes']:
        values=dict(stored);values['state']=NodeState(values['state']);values['type']=NodeType(values['type'])
        values['recognition_status']=RecognitionStatus(values['recognition_status']);nodes.append(Node(**values))
    before=Analysis(a['width'],a['height'],ScreenState.BLOODWEB,nodes,[tuple(e) for e in a['edges']])
    old_frame=frame(data['before']['frame'],np.zeros((a['height'],a['width'],3),np.uint8))
    target=next(n for n in nodes if n.id==12)
    assert not final_target(before,target) and any(n.state==NodeState.LOCKED for n in nodes)
    detector=BloodwebDetector(library);observations=[]
    for item in data['frames']:
        path=root/item['source'];assert hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256']
        raw=read_image(path);roi=item['frame']['roi'];pixels=raw[roi['y']:roi['y']+roi['height'],roi['x']:roi['x']+roi['width']]
        fresh=frame(item['frame'],pixels);web=detector.analyze_live(fresh)
        assert known_glyph_transition(record_scene(old_frame,before,[]),fresh,web)
        observations.append((fresh,web,plan_route(web,[])))
    d=Device([before]);d.window=old_frame.window;r=engine(d,dry_run=False)
    r.signature=old_frame.window.signature
    sequence=iter([observations[0],*observations]);r.read=lambda:next(sequence)
    r.buy(old_frame,before,target)
    assert len(d.presses)==1 and r.actions==0 and r.unconfirmed_level_actions==1
    assert not r.pending_action and not r.attempted
