"""Read only a purchased target; this partial result never authorizes input."""
import copy,time
import cv2
from src.core.models import Analysis,NodeState,ScreenState
from src.core.path_planner import RoutePlan
from src.core.fast_opening import OpeningFallback,FastOpening
from src.core.bloodweb_detector import ring_fractions


def target_observation(engine,before,node):
    opening=engine.opening
    if (not opening.active or node.id not in opening.features or
            not hasattr(engine.detector,'library')):return None
    frame=engine.capture_frame(confirmation_of=before) if hasattr(getattr(engine,'screen',None),'capture_confirmation') else engine.capture_frame()
    def fallback(reason,wait=False):
        if frame.confirmation_only and wait and time.monotonic()<getattr(engine,'target_probe_until',0):
            engine.trace('target_confirmation_pending',reason=reason,frame_stamp=frame.captured_at,paid_inputs=0)
            return frame,None,None
        if not frame.confirmation_only:engine.confirmation_capture=frame
        return None
    compatible=(not frame.probe_only and frame.window.signature==before.window.signature and frame.roi==before.roi
        and frame.image.shape==before.image.shape and frame.level==before.level and frame.level_confidence>=.95
        and (not frame.confirmation_only or (frame.visibility or {}).get('confirmation_anchor')))
    if not compatible:
        return fallback('context_changed')
    target=copy.deepcopy(node);hsv=cv2.cvtColor(frame.image,cv2.COLOR_BGR2HSV)
    red,radius=max((ring_fractions(hsv,target.x,target.y,round(target.radius*s))[0],round(target.radius*s))
        for s in (.65,.75,.85,.95,1.))
    gold=ring_fractions(hsv,target.x,target.y,target.radius)[1]
    if red<=.42 and gold<=.42:
        return fallback('ring_animation_unresolved',wait=True)
    target.state=NodeState.PURCHASED if red>.42 else NodeState.AVAILABLE
    target.state_confidence=.93 if red>.42 else .9
    verifier=copy.copy(opening);verifier.local_updates={};verifier.certificates=[];verifier.refreshed_matches={};verifier.frame_library_matches=0
    try:
        # Reuse the existing global-margin/contour certificate, not the full
        # library, and do not promote a lost or conflicting identity.
        FastOpening.verify_local(verifier,frame,target,engine.detector)
        if node.icon_id and target.icon_id!=node.icon_id:raise OpeningFallback('target_identity_unconfirmed')
    except OpeningFallback as exc:
        return fallback(str(exc),wait=str(exc)!='target_identity_unconfirmed')
    result=Analysis(frame.image.shape[1],frame.image.shape[0],ScreenState.BLOODWEB,[target],[],
        level=frame.level,level_confidence=frame.level_confidence,source='live_game')
    result.details={'confirmation_only':True,'scan_kind':'TARGET_CONFIRM','red_support':red,'local_certificates':verifier.certificates}
    result.confidence=.93;plan=RoutePlan(reason='confirmation_only')
    engine.recent_frames.append((frame,result,plan))
    engine.performance.add('target_confirmation',time.monotonic()-frame.captured_at,action=engine.actions)
    engine.trace('target_confirmation',frame=frame.metadata(),point=frame.to_screen(target.x,target.y),red_support=red)
    return frame,result,plan


def local_target_check(engine,before,node):
    """Small fresh capture of one bought node; never an input frame.

    Returns (frame,result,plan) when the ring is red, (frame,None,None) to poll
    again, or None when only a full read can decide. The second confirmation is
    always a normal map read, which also plans the next purchase.
    """
    if not hasattr(getattr(engine,'screen',None),'capture_confirmation'):return None
    frame=engine.capture_frame(confirmation_of=before)
    checks=dict(not_probe=not frame.probe_only,window=frame.window.signature==before.window.signature,roi=frame.roi==before.roi,
        shape=frame.image.shape==before.image.shape,level=frame.level==before.level,
        level_confidence=(frame.level_confidence or 0)>=.95,anchor=bool((frame.visibility or {}).get('confirmation_anchor')),
        confirmation_only=bool(frame.confirmation_only))
    failed=[k for k,v in checks.items() if not v and k!='confirmation_only']
    if failed==['anchor']:
        # The centre pulses for a moment after each purchase; poll again
        # rather than fall back to a full read. No red is counted meanwhile.
        return frame,None,None
    if failed:
        engine.trace('local_confirmation_rejected',failed=failed,
            confirmation_only=checks['confirmation_only'],level=(frame.level,before.level),roi=(str(frame.roi),str(before.roi)),paid_inputs=0)
        return None
    hsv=cv2.cvtColor(frame.image,cv2.COLOR_BGR2HSV)
    red=max(ring_fractions(hsv,node.x,node.y,round(node.radius*s))[0] for s in (.65,.75,.85,.95,1.))
    if red<=.42:return frame,None,None
    target=copy.deepcopy(node);target.state=NodeState.PURCHASED;target.state_confidence=.93
    result=Analysis(frame.image.shape[1],frame.image.shape[0],ScreenState.BLOODWEB,[target],[],
        level=frame.level,level_confidence=frame.level_confidence,source='live_game')
    result.details={'confirmation_only':True,'scan_kind':'TARGET_CONFIRM','red_support':red}
    result.confidence=.93;plan=RoutePlan(reason='confirmation_only')
    engine.recent_frames.append((frame,result,plan))
    engine.trace('target_confirmation',frame=frame.metadata(),point=frame.to_screen(target.x,target.y),red_support=red,local=True)
    return frame,result,plan


def save_proof(engine,observations):
    if not engine.report.path or not any(r.details.get('confirmation_only') for f,r in observations):return
    import hashlib,json
    from src.utils.images import write_image
    folder=engine.report.path.with_suffix('')/('target_'+str(engine.attempts));folder.mkdir(parents=True,exist_ok=True)
    records=[]
    for index,(frame,result) in enumerate(observations):
        node=result.nodes[0];x,y,r=node.x,node.y,node.radius+8
        box=[max(0,x-r),max(0,y-r),min(frame.image.shape[1],x+r+1),min(frame.image.shape[0],y+r+1)]
        path=folder/f'{index}.png';write_image(path,frame.image[box[1]:box[3],box[0]:box[2]])
        records.append(dict(frame=frame.metadata(),crop_box=box,path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            analysis=result.to_dict()))
    (folder/'proof.json').write_text(json.dumps(records,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
