"""Reward notifications with an OK button that hide the Bloodweb (TASKS 2026-11-08, D).

After a prestige the game can show an item-granted or perk-upgraded notice whose title has
no reviewed template. Such a notice is positively identified by the reviewed OK word alone,
and only while the web's centre is not visible. The button is pressed after two captures
agree; it is never a purchase, so a pending purchase stays pending.
"""
import hashlib,math,time
from pathlib import Path

import cv2

from src.core.bloodweb_header import text_mask
from src.core.runtime_paths import project_root
from src.core.video_ui import references
from src.core.visual_search import binary_match

MAX_DISMISSALS=5
DISMISS_WINDOW=30.
SAME_BOX_PIXELS=6
FRAME_MAX_AGE=1.5
MISSES_BEFORE_SAMPLE=6
MAX_SAMPLES=30


def find_ok_button(image,root=None):
    """The (x,y,w,h) of a single reviewed OK word in the lower 60% of the client, or None."""
    h,w=image.shape[:2]
    if w/max(1,h)<1.5:return None
    try:record,tiles=references(str(root or project_root()),'reward')
    except (OSError,ValueError,KeyError):return None
    scale=h/record['client_size'][1];template=tiles['button']
    if scale!=1:template=cv2.resize(template,None,fx=scale,fy=scale,interpolation=cv2.INTER_NEAREST)
    top=round(h*.4);mask=text_mask(image)[top:h,:]
    th,tw=template.shape
    if mask.shape[0]<th or mask.shape[1]<tw:return None
    score,point,separate=binary_match(mask,template)
    if score<.96 or separate:return None
    return point[0],top+point[1],tw,th


def _save_sample(engine,image,label):
    """One screenshot per visually different popup, for the library."""
    small=cv2.resize(cv2.cvtColor(image[:,:,:3],cv2.COLOR_BGR2GRAY),(32,18),interpolation=cv2.INTER_AREA)
    key=hashlib.sha256((small//32).tobytes()).hexdigest()[:12]
    seen=getattr(engine,'popup_samples',None)
    if seen is None:seen=engine.popup_samples=set()
    if key in seen or len(seen)>=MAX_SAMPLES:return
    seen.add(key)
    folder=Path(project_root())/'debug'/'popups';path=folder/f'{label}_{key}.png'
    picture=image.copy()
    def write():
        from src.utils.images import write_image
        folder.mkdir(parents=True,exist_ok=True)
        if not path.exists():write_image(path,picture)
    writer=getattr(engine,'evidence_writer',None)
    if writer is not None:writer.submit(('popup_sample',key),write)
    else:write()
    engine.trace('popup_sample_saved',path=str(path),label=label)


def dismiss_if_present(engine):
    """Called after a read found no web. True when an OK press was just sent."""
    if engine.settings.dry_run or not engine.approved or getattr(engine,'burst_inputs_active',False):return False
    scan=getattr(engine.screen,'popup_scan',None)
    engine.screen.popup_scan=None
    if not scan:return False
    frame,box=scan
    if box is None:
        engine.popup_misses=getattr(engine,'popup_misses',0)+1
        if engine.popup_misses==MISSES_BEFORE_SAMPLE:_save_sample(engine,frame.image,'no_button')
        engine.popup_previous=None
        return False
    engine.popup_misses=0
    if not 0<=time.monotonic()-frame.captured_at<=FRAME_MAX_AGE:
        engine.popup_previous=None;return False
    previous=getattr(engine,'popup_previous',None)
    engine.popup_previous=(frame.captured_at,box)
    if (previous is None or previous[0]>=frame.captured_at or
            math.dist(previous[1][:2],box[:2])>SAME_BOX_PIXELS):return False
    now=time.monotonic()
    recent=[t for t in getattr(engine,'popup_clicks',[]) if now-t<DISMISS_WINDOW]
    if len(recent)>=MAX_DISMISSALS:
        engine.popup_clicks=recent
        engine.trace('popup_dismiss_limit',clicks=len(recent),paid_inputs=0)
        return False
    _save_sample(engine,frame.image,'ok_button')
    engine.check_input_budget()
    x,y,w,h=box
    point=engine.check(frame,x+w/2,y+h/2)
    from src.core.automation import State
    engine.emit(State.RECOVERING,'popup_dismissing')
    old_region=getattr(engine.mouse,'target_region',None)
    engine.mouse.release()
    try:
        engine.mouse.target_region=(point,min(w,h)*.35)
        engine.attempts+=1;engine.popup_clicks=recent+[now];engine.popup_previous=None
        engine.performance.levels.input()
        engine.trace('modal_input',kind='reward_popup',attempt=len(recent)+1,point=point,purchase_credit=0)
        engine.mouse.purchase(point,.05,engine.stop,lambda:engine.check_held(frame,x+w/2,y+h/2))
        engine.mouse.release()
        if hasattr(engine.screen,'parking_point') and hasattr(engine.mouse,'park'):
            parked=engine.screen.parking_point(frame)
            if parked:engine.mouse.park(parked,engine.stop,lambda:engine.check_held(frame,x+w/2,y+h/2))
    finally:
        engine.mouse.release();engine.mouse.target_region=old_region
    return True
