"""Prestige-ready visual evidence from the user's original emblem, not redness."""
from functools import lru_cache
import hashlib
import cv2
import numpy as np
from src.utils.images import read_image
from src.core.click_lifecycle import transaction


# Wiki (Prestige, Bloodweb) and the game's own card both show 20,000 BP.
PRESTIGE_COST=20000
PRESTIGE_HOLD_SECONDS=.5


@lru_cache(maxsize=4)
def native_reference(root):
    import json
    folder=root/'references/bloodweb/v56_dark_lord_prestige'
    try:
        record=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        path=root/record['source']
        if not record['reviewed'] or hashlib.sha256(path.read_bytes()).hexdigest()!=record['source_sha256']:return None
        image=read_image(path);x,y,w,h=record['box']
        patch=image[y:y+h,x:x+w]
        hsv=cv2.cvtColor(patch,cv2.COLOR_BGR2HSV)
        white=((hsv[:,:,1]<100)&(hsv[:,:,2]>165)).astype(np.uint8)*255
        yy,xx=np.ogrid[-60:61,-60:61]
        mask=(((xx*xx+yy*yy)>20**2)&((xx*xx+yy*yy)<55**2)).astype(np.uint8)*255
        return white,mask,record['source_sha256']
    except (OSError,KeyError,ValueError):return None


def native_ready_center(image,root,hint=None):
    """Locate the reviewed wreath in fresh pixels; its numeral is masked out."""
    if hint is None:
        from src.core.bloodweb_header import read_level
        level,confidence=read_level(image,root)
        if level!=50 or confidence<.95:return None
    reference=native_reference(root)
    if reference is None:return None
    white,mask,sha=reference;h,w=image.shape[:2]
    hsv=cv2.cvtColor(image,cv2.COLOR_BGR2HSV)
    observed=((hsv[:,:,1]<100)&(hsv[:,:,2]>165)).astype(np.uint8)*255
    if hint is not None:points=[hint[:2]]
    else:
        count,labels,stats,centroids=cv2.connectedComponentsWithStats(observed)
        points=[point for stat,point in zip(stats[1:],centroids[1:]) if
            40<=stat[2]<=180 and 40<=stat[3]<=180 and stat[4]>=300
            and point[0]<w*.64 and h*.14<point[1]<h*.91]
    for cx,cy in points:
        half=max(96,round(h*.1));left=max(0,round(cx)-half);top=max(0,round(cy)-half)
        local=observed[top:min(h,round(cy)+half),left:min(w,round(cx)+half)]
        for scale in dict.fromkeys((h/1080,1.,.75,1.25,1.5)):
            size=round(121*scale)
            if size>=min(local.shape):continue
            tile=cv2.resize(white,(size,size),interpolation=cv2.INTER_NEAREST)
            weight=cv2.resize(mask,(size,size),interpolation=cv2.INTER_NEAREST)
            scores=cv2.matchTemplate(local,tile,cv2.TM_CCORR_NORMED,mask=weight)
            scores=np.nan_to_num(scores,nan=0.,posinf=0.,neginf=0.)
            _,score,_,point=cv2.minMaxLoc(scores)
            if score<.97:continue
            x=round(left+point[0]+(size-1)/2);y=round(top+point[1]+(size-1)/2)
            if hint is not None and np.hypot(x-hint[0],y-hint[1])>hint[2]*.25:continue
            q=hsv[max(0,y-round(65*scale)):min(h,y+round(65*scale)+1),max(0,x-round(65*scale)):min(w,x+round(65*scale)+1)]
            red=float(np.mean(((q[:,:,0]<10)|(q[:,:,0]>172))&(q[:,:,1]>150)&(q[:,:,2]>55)))
            if red>=.15:return dict(score=float(score),center=[x,y,round(40*scale)],reference_sha256=sha,
                red_fraction=red,price=None,input_duration=None,source='native_reviewed_wreath')
    return None


def ready_center(image,root):
    """The prestige emblem on a level-50 screen, hovered (native wreath) or not (video wreath).

    The emblem carries the next prestige number. Live check 14.11: with two digits
    (P10-P99) or P100 the not-hovered emblem no longer matched the central-glyph
    templates well enough, the centre search settled on another spot and the
    prestige screen went unrecognised. Here the wreath itself is found first."""
    native=native_ready_center(image,root)
    if native:return native
    from src.core.bloodweb_header import read_level
    level,confidence=read_level(image,root)
    if level!=50 or confidence<.95:return None
    h,w=image.shape[:2];s=h/1080
    hsv=cv2.cvtColor(image,cv2.COLOR_BGR2HSV)
    observed=((hsv[:,:,1]<100)&(hsv[:,:,2]>130)).astype(np.uint8)
    count,labels,stats,centroids=cv2.connectedComponentsWithStats(observed)
    points=[point for stat,point in zip(stats[1:],centroids[1:]) if
        25*s<=stat[2]<=180*s and 25*s<=stat[3]<=180*s and stat[4]>=150*s*s
        and point[0]<w*.64 and h*.14<point[1]<h*.91]
    for x,y in sorted(points,key=lambda p:abs(p[0]-w*.356)+abs(p[1]-h*.52))[:12]:
        found=video_ready_evidence(image,root,(round(x),round(y),round(40*s)))
        if found:return found
    return None


def reviewed_price_region(root,record):
    """Load an explicitly reviewed native price crop; never infer one from HUD."""
    from src.core.chain_plan import PriceRegion
    if not isinstance(record,dict) or record.get('kind')!='prestige_total_price' or record.get('reviewed') is not True:return None
    try:
        source=(root/record['source']).resolve()
        if not source.is_relative_to(root.resolve()) or not source.is_file():return None
        if hashlib.sha256(source.read_bytes()).hexdigest()!=record['sha256']:return None
        image=read_image(source);height,width=image.shape[:2]
        box=record['box']
        if not isinstance(box,list) or len(box)!=4 or any(type(v) is not int for v in box):return None
        x,y,w,h=box
        if not (0<=x<x+w<=width and 0<=y<y+h<=height):return None
        if record.get('client_size')!=[width,height]:return None
        return PriceRegion(tuple(box),record['sha256'],True,'client')
    except (KeyError,TypeError,ValueError,OSError):return None


def default_video_price_calibration(root):
    """Reviewed layout only; callers still require two new native price frames."""
    import json
    try:
        record=json.loads((root/'assets/ui/video_v54/manifest.json').read_text(encoding='utf-8'))['prestige_price_calibration']
        return record if reviewed_price_region(root,record) else None
    except (OSError,ValueError,KeyError):return None


@lru_cache(maxsize=4)
def reference(root):
    path=root/'references/bloodweb/prestige_node_example.png'
    image=read_image(path);patch=image[478:599,395:516]
    if patch.shape[:2]!=(121,121):return None
    hsv=cv2.cvtColor(patch,cv2.COLOR_BGR2HSV)
    white=((hsv[:,:,1]<100)&(hsv[:,:,2]>165)).astype(np.uint8)*255
    yy,xx=np.ogrid[-60:61,-60:61]
    mask=(((xx*xx+yy*yy)>15**2)&((xx*xx+yy*yy)<58**2)).astype(np.uint8)*255
    return white,mask,hashlib.sha256(path.read_bytes()).hexdigest()


def ready_evidence(image,root,center):
    """A local wreath certificate; unknown price/duration remain unknown."""
    if center is not None:
        native=native_ready_center(image,root,center)
        if native:return native
    video=video_ready_evidence(image,root,center)
    if video:return video
    try:record=reference(root)
    except (OSError,ValueError):return None
    if record is None or center is None:return None
    white,mask,sha=record;cx,cy,r=center
    hsv=cv2.cvtColor(image,cv2.COLOR_BGR2HSV)
    observed=((hsv[:,:,1]<100)&(hsv[:,:,2]>165)).astype(np.uint8)*255
    half=max(84,round(r*2.2));left=max(0,cx-half);top=max(0,cy-half)
    local=observed[top:min(image.shape[0],cy+half+1),left:min(image.shape[1],cx+half+1)]
    best=None
    for scale in (.65,.75,.8,.9,1.,1.1,1.25,1.5):
        size=round(121*scale)
        if size>=min(local.shape):continue
        tile=cv2.resize(white,(size,size),interpolation=cv2.INTER_NEAREST)
        weight=cv2.resize(mask,(size,size),interpolation=cv2.INTER_NEAREST)
        scores=cv2.matchTemplate(local,tile,cv2.TM_CCORR_NORMED,mask=weight)
        scores=np.nan_to_num(scores,nan=0.,posinf=0.,neginf=0.)
        _,score,_,point=cv2.minMaxLoc(scores)
        x=round(left+point[0]+(size-1)/2);y=round(top+point[1]+(size-1)/2)
        if abs(x-cx)>r*.25 or abs(y-cy)>r*.25:continue
        # Red four-sided star belongs to this emblem, not a white offering rim.
        q=image[max(0,y-round(65*scale)):y+round(65*scale)+1,max(0,x-round(65*scale)):x+round(65*scale)+1]
        h=cv2.cvtColor(q,cv2.COLOR_BGR2HSV)
        red=float(np.mean(((h[:,:,0]<10)|(h[:,:,0]>172))&(h[:,:,1]>150)&(h[:,:,2]>55)))
        if red<.15:continue
        if best is None or score>best['score']:best=dict(score=float(score),center=[x,y,r],reference_sha256=sha,
            red_fraction=red,price=None,input_duration=None,source='user_prestige_wreath')
    return best if best and best['score']>=.97 else None


def video_ready_evidence(image,root,center):
    """Same annular glyph contract, using the user's additional video original."""
    if center is None:return None
    from src.core.video_ui import references
    try:record,tiles=references(str(root),'prestige_ready')
    except (OSError,ValueError,KeyError):return None
    white=tiles['wreath'];yy,xx=np.ogrid[-60:61,-60:61]
    mask=(((xx*xx+yy*yy)>19**2)&((xx*xx+yy*yy)<55**2)).astype(np.uint8)*255
    hsv=cv2.cvtColor(image,cv2.COLOR_BGR2HSV)
    observed=((hsv[:,:,1]<100)&(hsv[:,:,2]>130)).astype(np.uint8)*255
    cx,cy,r=center;half=max(84,round(r*2.2));left=max(0,cx-half);top=max(0,cy-half)
    local=observed[top:min(image.shape[0],cy+half+1),left:min(image.shape[1],cx+half+1)]
    for scale in (1.,.75,.9,1.1,1.25,1.5):
        size=round(121*scale)
        if size>=min(local.shape):continue
        tile=cv2.resize(white,(size,size),interpolation=cv2.INTER_NEAREST)
        weight=cv2.resize(mask,(size,size),interpolation=cv2.INTER_NEAREST)
        scores=cv2.matchTemplate(local,tile,cv2.TM_CCORR_NORMED,mask=weight)
        scores=np.nan_to_num(scores,nan=0.,posinf=0.,neginf=0.)
        _,score,_,point=cv2.minMaxLoc(scores)
        x=round(left+point[0]+(size-1)/2);y=round(top+point[1]+(size-1)/2)
        if score<.97 or abs(x-cx)>r*.25 or abs(y-cy)>r*.25:continue
        q=hsv[max(0,y-round(65*scale)):y+round(65*scale)+1,max(0,x-round(65*scale)):x+round(65*scale)+1]
        red=float(np.mean(((q[:,:,0]<10)|(q[:,:,0]>172))&(q[:,:,1]>150)&(q[:,:,2]>55)))
        if red>=.15:return dict(score=float(score),center=[x,y,r],reference_sha256=record['source_sha256'],
            red_fraction=red,price=None,input_duration=None,source='user_video_prestige_wreath')
    return None


@transaction('prestige')
def activate_ready(engine,frame,result):
    import math,time
    from src.core.models import ScreenState,NodeType
    from src.core.screen_manager import ScreenError
    from src.core.scene_memory import record_scene
    engine.check_input_budget()
    if (not engine.approved or not engine.settings.auto_prestige or not engine.prestige_approved or engine.settings.dry_run
            or engine.pending_action or engine.persisted_scene
            or result.screen_state!=ScreenState.PRESTIGE_READY or not result.details.get('prestige_ready')
            or len(result.nodes)!=1 or result.nodes[0].type!=NodeType.PRESTIGE
            or result.details.get('transient_overlay')):raise ScreenError('prestige_manual')
    if engine.settings.bloodpoint_checks:
        if type(frame.bloodpoints) is not int:raise ScreenError('prestige_bp_unreadable')
        if frame.bloodpoints<PRESTIGE_COST:raise ScreenError('insufficient_bp')
    center=result.nodes[0];point=engine.check(frame,center.x,center.y)
    if any(math.dist(point,p)<8 for p in engine.attempted):raise ScreenError('target_already_attempted')
    if (engine.settings.max_actions is not None and
            engine.actions+engine.unconfirmed_level_actions+engine.prestige_actions>=engine.settings.max_actions):
        raise ScreenError('action_limit')
    inner=center.radius*min(frame.roi.width/frame.image.shape[1],frame.roi.height/frame.image.shape[0])*.35
    if inner<5 or center.geometry_confidence<.7:raise ScreenError('unsafe_node')
    engine.mouse.target_region=(point,inner)
    engine.attempted.append(point);engine.pending_action=True;engine.attempts+=1
    engine.performance.levels.input()
    engine.scene_record=record_scene(frame,result,engine.attempted,dict(point=list(point),radius=center.radius,icon=None,
        prestige=True,price=PRESTIGE_COST))
    engine.trace('prestige_before',price=PRESTIGE_COST,hold_seconds=PRESTIGE_HOLD_SECONDS,bloodpoints=frame.bloodpoints)
    from src.core.automation import State
    engine.emit(State.PRESTIGE_ACTIVATION,'prestige_activating')
    try:
        if hasattr(getattr(engine.screen,'backend',None),'close_probe'):engine.screen.backend.close_probe()
        down_before=getattr(engine.mouse,'down_attempt_sequence',None)
        try:engine.mouse.purchase(point,PRESTIGE_HOLD_SECONDS,engine.stop,lambda:engine.check_held(frame,center.x,center.y))
        except Exception:
            if type(down_before) is int and engine.mouse.down_attempt_sequence==down_before:
                # The button never went down, so nothing is unresolved.
                engine.pending_action=False;engine.scene_record=None
                if point in engine.attempted:engine.attempted.remove(point)
                engine.trace('input_not_submitted',point=point,down_api_attempted=False,prestige=True)
            raise
        engine.mouse.release();parked=engine.screen.parking_point(frame)
        if parked:engine.mouse.park(parked,engine.stop,lambda:engine.check_held(frame,center.x,center.y))
        engine.emit(State.PRESTIGE_ANIMATION,'prestige_animation')
        if hasattr(engine.screen,'fast_center_retry'):engine.screen.fast_center_retry=True
        deadline=min(time.monotonic()+engine.settings.refresh_timeout,getattr(engine,'runtime_deadline',float('inf')))
        previous=None;count=0;last=frame.captured_at
        unchanged=None;ready_count=0;left_ready=False;ordinal=1
        while time.monotonic()<deadline:
            engine.gate();engine.poll();fresh,web,plan=engine.recover_read(deadline,retry_stale=True)
            if fresh.captured_at<=last:continue
            last=fresh.captured_at
            if fresh.window.signature!=frame.window.signature:raise ScreenError('window_changed')
            current=(fresh,web,None)
            next_level=web.screen_state==ScreenState.BLOODWEB and web.level==1 and web.level_confidence>=.95
            known_reward=web.screen_state==ScreenState.REWARD and web.ok_box is not None
            valid=next_level or known_reward
            if valid:left_ready=True
            count=count+1 if valid and engine.stable_scene(previous,current) else 1 if valid else 0
            previous=current if valid else None
            if count>=2:
                # Two new-screen observations prove it. The HUD may be hidden on Rewards.
                engine.pending_action=False;engine.scene_record=None;engine.attempted.clear()
                engine.prestige_actions+=1
                engine.trace('prestige_confirmed',price=PRESTIGE_COST,bp_after=fresh.bloodpoints,observations=count,level=web.level,
                    purchased_items=0,analysis_id=web.analysis_id)
                if next_level:
                    engine.trace('new_level_confirmed',analysis_id=web.analysis_id,source='prestige',observations=count,level=web.level)
                return fresh,web,plan
            # A still-visible, unchanged prestige button can have ignored the
            # first press. Re-read it twice before another command. A debit,
            # animation/unknown screen or a newly visible web never permits it.
            same_ready=(not left_ready and web.screen_state==ScreenState.PRESTIGE_READY
                and web.level==50 and web.level_confidence>=.95
                and web.details.get('prestige_ready') and not web.details.get('transient_overlay')
                and fresh.bloodpoints==frame.bloodpoints and len(web.nodes)==1
                and web.nodes[0].type==NodeType.PRESTIGE and web.nodes[0].geometry_confidence>=.7
                and math.dist(fresh.to_screen(web.nodes[0].x,web.nodes[0].y),point)<=5)
            ready_count=ready_count+1 if same_ready and unchanged and engine.stable_scene(unchanged,current) else 1 if same_ready else 0
            unchanged=current if same_ready else None
            if ready_count>=2:
                engine.gate();engine.check_input_budget()
                target=web.nodes[0];engine.check(fresh,target.x,target.y)
                engine.mouse.target_region=(point,target.radius*min(fresh.roi.width/fresh.image.shape[1],fresh.roi.height/fresh.image.shape[0])*.35)
                ordinal+=1;engine.attempts+=1;engine.performance.levels.input()
                engine.scene_record['pending']['prestige_commands']=ordinal
                engine.trace('prestige_repeat',ordinal=ordinal,point=point,frame=fresh.metadata(),unchanged_bp=fresh.bloodpoints)
                engine.emit(State.PRESTIGE_ACTIVATION,'prestige_activating')
                if hasattr(getattr(engine.screen,'backend',None),'close_probe'):engine.screen.backend.close_probe()
                engine.mouse.purchase(point,PRESTIGE_HOLD_SECONDS,engine.stop,lambda:engine.check_held(fresh,target.x,target.y))
                engine.mouse.release();parked=engine.screen.parking_point(fresh)
                if parked:engine.mouse.park(parked,engine.stop,lambda:engine.check_held(fresh,target.x,target.y))
                engine.emit(State.PRESTIGE_ANIMATION,'prestige_animation')
                unchanged=None;ready_count=0
        raise ScreenError('prestige_result_unknown')
    finally:engine.mouse.release()
