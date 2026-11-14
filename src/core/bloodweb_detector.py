"""Screenshot detection. No mouse control or filename-based state inference."""
import json
import time
from pathlib import Path

import cv2
import numpy as np

from src.core.icon_library import IconLibrary,rarity_of
from src.core.models import Analysis, Node, NodeState, NodeType, ScreenState,Match
from src.core.node_classifier import IconMatcher, node_mask,glyph
from src.core.geometry import organize_rings
from src.core.node_category import classify_category
from src.utils.images import read_image, write_image
from src.core.runtime_paths import project_root


def red_mask(hsv: np.ndarray) -> np.ndarray:
    return (((hsv[:,:,0]<10)|(hsv[:,:,0]>172))&(hsv[:,:,1]>150)&(hsv[:,:,2]>55)).astype(np.uint8)*255


def _component_center(image: np.ndarray) -> tuple[int,int,int] | None:
    h,w=image.shape[:2]
    mask=red_mask(cv2.cvtColor(image,cv2.COLOR_BGR2HSV))
    mask[:int(h*.16)]=0
    mask[int(h*.90):]=0
    if w/h>1.5:
        mask[:,int(w*.64):]=0  # Bloodweb is in the left panel of a full game screenshot.
    mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
    count,labels,stats,centroids=cv2.connectedComponentsWithStats(mask)
    candidates=[]
    pane_x=w*(.355 if w/h>1.5 else .5)
    for stat,centroid in zip(stats[1:],centroids[1:]):
        bx,by,bw,bh,area=stat
        if not (h*.025<min(bw,bh) and max(bw,bh)<h*.25 and .65<bw/bh<1.45
                and area>h*h*.0003):
            continue
        distance=np.hypot(centroid[0]-pane_x,centroid[1]-h*.55)
        candidates.append((distance,centroid,bw,bh))
    if not candidates:
        return None
    _,centroid,bw,bh=min(candidates,key=lambda c:c[0])
    x,y=map(lambda v:int(round(v)),centroid)
    radius=round(h*.047)
    return x,y,radius



from functools import lru_cache

last_center_native=False   # the last accepted glyph came from the client's own scale
FOCUS_SCORE=.86   # a native-scale match already acceptable on its own fixes where the other scales are searched
FOCUS_REACH=2.    # ...within this many glyph sizes of it


@lru_cache(maxsize=4)
def _center_templates(root):
    # Visual anchors from the user's original references. Test annotations are
    # never read here; connected components are used only on these fixed sources.
    templates=[]
    for name in ('bloodweb_level43_start.png','bloodweb_prestige_node.png','bloodweb_level43_finished_waiting_refresh.png'):
        path=Path(root)/'references/bloodweb'/name
        if not path.is_file(): continue
        source=read_image(path); found=_component_center(source)
        if found is None: continue
        x,y,r=found; half=24
        patch=source[y-half:y+half+1,x-half:x+half+1]
        if patch.shape[:2]!=(49,49): continue
        templates.append((cv2.cvtColor(patch,cv2.COLOR_BGR2GRAY),r,name))
    # Additional real recording, same glyph threshold and scale search. The
    # old sources remain available for their clients and video negatives.
    from src.core.video_ui import references
    for kind in ('web_center','early_center'):
        try:
            record,_=references(str(root),kind)
            region=record['regions']['glyph'];tile=read_image(Path(root)/region['path'])
            templates.append((cv2.cvtColor(tile,cv2.COLOR_BGR2GRAY),40,'user_onryo_video_'+kind))
        except (OSError,ValueError,KeyError):pass
    # Additional current-client anchor, copied from reviewed MSS pixels.
    # Its independent native holdouts are tested; historical anchors remain.
    import hashlib
    folder=Path(root)/'references/bloodweb/v56_early_center'
    try:
        record=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        source=Path(root)/record['source'];glyph_path=Path(root)/record['glyph']
        if (record['reviewed'] is True and hashlib.sha256(source.read_bytes()).hexdigest()==record['source_sha256'] and
                hashlib.sha256(glyph_path.read_bytes()).hexdigest()==record['glyph_sha256']):
            x,y,w,h=record['box'];original=read_image(source)[y:y+h,x:x+w];tile=read_image(glyph_path)
            if tile.shape[:2]==(49,49) and np.array_equal(original,tile):
                templates.append((cv2.cvtColor(tile,cv2.COLOR_BGR2GRAY),record['radius'],'reviewed_native_early_center'))
    except (OSError,ValueError,KeyError):pass
    # Native centre glyphs at DBD's 90 % UI scale, with that scale's smaller radius.
    folder=Path(root)/'references/bloodweb/ui90'
    try:
        for record in json.loads((folder/'manifest.json').read_text(encoding='utf-8'))['centers']:
            path=folder/record['file']
            if hashlib.sha256(path.read_bytes()).hexdigest()==record['sha256']:
                templates.append((cv2.cvtColor(read_image(path),cv2.COLOR_BGR2GRAY),record['radius'],'ui90_'+record['file']))
    except (OSError,ValueError,KeyError):pass
    return templates


def find_center(image: np.ndarray, root=None, hint=None, local_only=False,observed_level=None,native_only=False) -> tuple[int,int,int] | None:
    """Find the central glyph even when red branches merge with its border.

    A red notification or purchased ring cannot replace the anchor. No stale
    position or absolute game coordinate is used as a fallback.
    """
    root=Path(root) if root else project_root()
    from src.core.prestige import native_ready_center,ready_center
    # A confident header read from these same pixels rules out a prestige
    # wreath on levels 1–49. Unknown/50 retain the original wreath search.
    ordinary=(observed_level is not None and type(observed_level[0]) is int
              and 1<=observed_level[0]<50 and observed_level[1]>=.95)
    native=None if ordinary else (native_ready_center(image,root,hint) if hint is not None else ready_center(image,root))
    if native:return tuple(native['center'])
    h,w=image.shape[:2]; shrink=min(1.,1080/h)
    gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    if shrink<1: gray=cv2.resize(gray,None,fx=shrink,fy=shrink,interpolation=cv2.INTER_AREA)
    best=None
    def radius(scale,name=''):
        if name.startswith('ui90_'):return next(r for _,r,n in _center_templates(str(root)) if n==name)
        nominal=round(h*(.037 if w/h>1.5 else .047))
        # A cropped landscape screenshot may retain the original game pixels.
        # Its height cannot shrink the known visual scale of the central glyph.
        templates=_center_templates(str(root))
        return max(nominal,round(templates[0][1]*scale/shrink)) if w/h>1.5 and templates else nominal
    scales=list(dict.fromkeys((1.,h/1080*shrink,.5,.625,.75,.8,.8333,.9,1.,1.1111,1.25,1.3333,1.5,1.667,2.,2.5,h/1080*shrink)))
    if hint is not None:
        hx,hy,hr=hint;half=max(72,round(hr*2.2));sx,sy=round(hx*shrink),round(hy*shrink)
        left,top=max(0,sx-half),max(0,sy-half)
        local=gray[top:min(gray.shape[0],sy+half),left:min(gray.shape[1],sx+half)]
        # A hint narrows the search only after the original central glyph is
        # independently found in today's pixels at the stricter threshold.
        for template,r,name in _center_templates(str(root)):
            for scale in scales:
                size=round(49*scale)
                if size>=min(local.shape):continue
                tile=cv2.resize(template,(size,size),interpolation=cv2.INTER_AREA if scale<1 else cv2.INTER_LINEAR)
                scores=cv2.matchTemplate(local,tile,cv2.TM_CCOEFF_NORMED)
                _,score,_,point=cv2.minMaxLoc(scores)
                x,y=(left+point[0]+(size-1)/2)/shrink,(top+point[1]+(size-1)/2)/shrink
                if score>=.94 and np.hypot(x-hx,y-hy)<hr*.25:
                    return round(x),round(y),radius(scale,name)
        if local_only:return None
    # Try the native scale of every genuine reference before costly resize
    # sweeps of the first historical reference. Early-web glyphs have their
    # own reference; the former order could age a fresh capture before scan.
    # Speed (live 13.11, The Mastermind): a hovered or red centre scores 0.86-0.93,
    # below the early exit, and the full sweep of every scale over the whole web
    # took 2.7-3.1 s, so every capture aged past the 2 s freshness limit
    # (stale_frame, 558 s lost in one evening). Once the native scales have found
    # the glyph, the remaining scales are matched only around that point: the
    # same templates, scales and threshold, a smaller search window.
    native_count=len(dict.fromkeys(scales[:2]));focus=None
    global last_center_native
    last_center_native=False
    for index,scale in enumerate(scales):
        if index==native_count and native_only and (best is None or best[0]<.86):
            # The caller already knows this window's glyph scale (a live session): no glyph at
            # the native scales is no glyph at all, e.g. between two levels (live 14.11: 30
            # such reads cost 102 s at 2.7-3 s each). The caller still runs a full search now and then.
            return None
        if index==native_count and best is not None and best[0]>=FOCUS_SCORE:
            focus=(best[1][0]+(best[2]-1)/2,best[1][1]+(best[2]-1)/2,max(best[2],49)*FOCUS_REACH)
        for template,r,name in _center_templates(str(root)):
            size=round(49*scale)
            if size>=min(gray.shape): continue
            tile=cv2.resize(template,(size,size),interpolation=cv2.INTER_AREA if scale<1 else cv2.INTER_LINEAR)
            # Match only the already-permitted top-left positions. Include the
            # template's trailing pixels so this has the same search domain as
            # the former full-image match followed by masking its score map.
            top=round(gray.shape[0]*.14)
            bottom=min(gray.shape[0],round(gray.shape[0]*.91)+size-1)
            right=min(gray.shape[1],round(gray.shape[1]*.64)+size-1) if w/h>1.5 else gray.shape[1]
            left=0
            if focus is not None:
                fx,fy,reach=focus
                left=max(0,round(fx-(size-1)/2-reach));right=min(right,round(fx-(size-1)/2+reach)+size)
                top=max(top,round(fy-(size-1)/2-reach));bottom=min(bottom,round(fy-(size-1)/2+reach)+size)
            search=gray[top:bottom,left:right]
            if min(search.shape)<size:continue
            scores=cv2.matchTemplate(search,tile,cv2.TM_CCOEFF_NORMED)
            _,score,_,point=cv2.minMaxLoc(scores)
            point=(point[0]+left,point[1]+top)
            if best is None or score>best[0]:best=(score,point,size,r,scale,name)
            if score>=.94:break
        if best is not None and best[0]>=.94:break
    if best is None or best[0]<.86:return None
    score,point,size,r,scale,name=best
    last_center_native=scale in scales[:native_count]
    return (round((point[0]+(size-1)/2)/shrink),round((point[1]+(size-1)/2)/shrink),radius(scale,name))

def ring_fractions(hsv: np.ndarray, x: int,y: int,r: int) -> tuple[float,float]:
    angles=np.linspace(0,2*np.pi,96,endpoint=False)
    red_votes=[]; gold_votes=[]
    h,w=hsv.shape[:2]
    for scale in (.86,.95,1.04,1.13,1.22):
        xs=np.clip(np.round(x+np.cos(angles)*r*scale).astype(int),0,w-1)
        ys=np.clip(np.round(y+np.sin(angles)*r*scale).astype(int),0,h-1)
        p=hsv[ys,xs]
        red_votes.append(((p[:,0]<10)|(p[:,0]>172))&(p[:,1]>150)&(p[:,2]>75))
        gold_votes.append((p[:,0]>15)&(p[:,0]<40)&(p[:,1]>35)&(p[:,1]<180)&(p[:,2]>125))
    red=float(np.mean(np.any(red_votes,axis=0)));gold=float(np.mean(np.any(gold_votes,axis=0)))
    # Orange scenery around a consumed tile can satisfy almost every old hue
    # vote. A usable gold ring must also be a narrow bright radial ridge.
    # Each angle may use its own radius, preserving modest Hough offsets.
    # This adds evidence; color alone never promotes background to Available.
    if gold>.42 and gold_ring_support(hsv,x,y,r)<=.75:gold=0.
    return red,gold


def gold_ring_support(hsv,x,y,r):
    angles=np.linspace(0,2*np.pi,96,endpoint=False)
    radii=r*np.arange(.86,1.23,.04)
    distances=radii[None,:,None]+np.array([-4.,0.,4.])[:,None,None]
    xs=np.clip(np.rint(x+distances*np.cos(angles)).astype(int),0,hsv.shape[1]-1)
    ys=np.clip(np.rint(y+distances*np.sin(angles)).astype(int),0,hsv.shape[0]-1)
    samples=hsv[ys,xs]
    gray=cv2.cvtColor(cv2.cvtColor(samples.reshape(-1,1,3),cv2.COLOR_HSV2BGR),cv2.COLOR_BGR2GRAY).reshape(samples.shape[:3]).astype(np.int16)
    p=samples[1]
    gold=(p[:,:,0]>15)&(p[:,:,0]<40)&(p[:,:,1]>35)&(p[:,:,1]<180)&(p[:,:,2]>125)
    ridge=(gray[1]-gray[0]>15)&(gray[1]-gray[2]>15)
    return float(np.mean(np.any(gold&ridge,axis=0)))


def fresh_gold_radius(hsv,x,y,r,base_r):
    """Correct an inner proposal only with three fresh strong gold annuli."""
    red,gold=ring_fractions(hsv,x,y,r)
    if r>=base_r*.8 or red>.42 or gold>.42:return r
    candidates=range(round(base_r*.90),round(base_r*1.05)+1)
    for candidate in sorted(candidates,key=lambda value:abs(value-base_r)):
        if candidate>r*1.5:continue
        if all(ring_fractions(hsv,x,y,value)[1]>.65 for value in (candidate-1,candidate,candidate+1)):
            return candidate
    return r


def fresh_dark_radius(hsv,gray,x,y,r,base_r):
    """An oversized scenery halo may surround a fresh smaller consumed rim."""
    if r<=base_r*1.1 or max(ring_fractions(hsv,x,y,r))>.42:return r
    from src.core.node_validation import framing
    candidates=[]
    for candidate in range(round(base_r*.65),round(base_r*.90)+1):
        rim=framing(gray,x,y,candidate)
        if (rim['dark_ring']>=.8 and rim['opposite_dark']>=.6 and rim['regular']>=.85
                and BloodwebDetector._dark_rim(gray,x,y,candidate)>.6):
            candidates.append((rim['dark_ring'],rim['regular'],-abs(candidate-base_r*.75),candidate))
    return max(candidates)[-1] if candidates else r


def framed_locked_confidence(raw,state,evidence,gray=None,x=None,y=None,radius=None):
    """A proven dim rim supports observation, below the .70 input floor."""
    if state==NodeState.LOCKED and raw<.65 and evidence and evidence.get('accepted') and evidence.get('connected'):
        proofs=[evidence]
        if gray is not None:
            from src.core.node_validation import framing
            # A one-pixel Hough offset aliases a thin dark rim. Confirm it in
            # current pixels, without moving a target or broadening its hitbox.
            proofs.extend(framing(gray,x+dx,y+dy,radius) for dx in (-1,0,1) for dy in (-1,0,1) if dx or dy)
        strong=[e for e in proofs if e.get('valley',0)>=.42 and e.get('regular',0)>=.65]
        if strong:
            return round(max(raw,min(.69,max(.55+.2*e['valley']+.15*e['regular'] for e in strong))),3)
    return raw


def same_capture_center(frame):
    """Full-client glyph scale, certified by this exact native capture."""
    capture=getattr(frame,'visibility',None) or {}
    if (not frame.live or frame.probe_only or capture.get('captured_at')!=frame.captured_at or
            not capture.get('center') or capture.get('window',{}).get('hwnd')!=frame.window.hwnd or
            capture.get('window',{}).get('pid')!=frame.window.pid):return None
    h,w=frame.image.shape[:2];sx=w/frame.roi.width;sy=h/frame.roi.height
    if abs(sx-sy)>=.01:return None
    px,py,pr=capture['center']
    return ((px-frame.roi.x)*sx,(py-frame.roi.y)*sy,round(pr*sx))


class BloodwebDetector:
    frame_pipeline_version=1
    def __init__(self, library: IconLibrary, role: str='survivor',character='blight'):
        self.library=library
        self.role,self.character=role,character
        self._matcher=None
        from src.core.bloodweb_memory import BloodwebMemory
        self.memory=BloodwebMemory(role,character);self._live_frame=None

    @property
    def matcher(self):
        if self._matcher is None:self._matcher=IconMatcher(self.library,self.role,character=self.character)
        return self._matcher

    def prepare(self):
        return self.matcher

    def invalidate(self):
        self._previous_structure=None;self._hough_cache={};self._center_hint=None
        if self._matcher is not None:self._matcher.clear_matches()
        self.memory.clear()

    def analyze_live(self,frame):
        context=(frame.window.signature,frame.roi,frame.image.shape,getattr(frame,'level',None))
        if getattr(self,'_capture_context',None)!=context:
            self.invalidate()
        self._capture_context=context
        self._live_frame=frame
        result=self.analyze(frame.image,'live_game')
        result.level=getattr(frame,'level',None);result.level_confidence=getattr(frame,'level_confidence',0.)
        return result

    def tutorial_certificate(self,image,context=None):
        from src.core.video_ui import certificate
        proof=context.certificate('continue') if context else certificate(image,self.library.root,'continue')
        if proof:
            proof['kind']='continue'
            return proof
        return context.certificate('tutorial_unpredictability') if context else certificate(image,self.library.root,'tutorial_unpredictability')

    def reward_box(self,image: np.ndarray,context=None) -> tuple[int,int,int,int] | None:
        """Strict UI template check using the user's reward reference.

        Two independent text regions must match. These are reference crops,
        never recorded game click coordinates; every location is searched.
        """
        from src.core.video_ui import certificate
        proof=context.certificate('reward') if context else certificate(image,self.library.root,'reward')
        if proof:return proof['regions']['button']
        path=self.library.root/'references/bloodweb/bloodweb_prestige_reward_ok.png'
        if not path.is_file():
            return None
        h,w=image.shape[:2]
        if w/h<1.5:
            return None
        reference=getattr(self,'_reward_reference',None)
        if reference is None:
            reference=read_image(path); self._reward_reference=reference
        gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
        ref_gray=getattr(self,'_reward_gray',None)
        if ref_gray is None:ref_gray=cv2.cvtColor(reference,cv2.COLOR_BGR2GRAY);self._reward_gray=ref_gray
        ref_h,ref_w=ref_gray.shape; scale=h/ref_h
        found=[]
        # The small button rejects ordinary web pixels before the much larger
        # legacy title template. Both independent regions remain mandatory.
        for rx,ry,rw,rh in ((.867,.838,.020,.023),(.300,.236,.401,.033)):
            x,y,tw,th=round(rx*ref_w),round(ry*ref_h),round(rw*ref_w),round(rh*ref_h)
            template=cv2.resize(ref_gray[y:y+th,x:x+tw],None,fx=scale,fy=scale,interpolation=cv2.INTER_AREA)
            th,tw=template.shape
            if tw>w or th>h: return None
            result=cv2.matchTemplate(gray,template,cv2.TM_CCOEFF_NORMED)
            _,score,_,loc=cv2.minMaxLoc(result)
            if score<.89:
                return None
            found.append((loc[0],loc[1],tw,th))
        return found[0]

    def analyze(self, image: np.ndarray, source: str | None=None) -> Analysis:
        started=time.perf_counter();self._timings={}
        result=self._analyze(image,source)
        if source not in ('live_game','game_window'):
            from src.core.bloodweb_header import read_level
            started_header=time.perf_counter();result.level,result.level_confidence=read_level(image,self.library.root)
            self._timings['level_ocr']=time.perf_counter()-started_header
        total=time.perf_counter()-started
        accounted=sum(v for k,v in self._timings.items() if k not in ('geometry_cache_check','recognition_stage1','recognition_stage2','memory_update'))
        result.details['timings']=dict(self._timings,detector_other=max(0,total-accounted),detector_total=total)
        return result

    def _analyze(self, image: np.ndarray, source: str | None=None) -> Analysis:
        h,w=image.shape[:2]
        analysis=Analysis(w,h,ScreenState.UNKNOWN,source=source)
        analysis.details['candidates']=[]
        from src.core.frame_pipeline import visuals_for
        frame=getattr(self,'_live_frame',None)
        context=visuals_for(frame) if source in ('live_game','game_window') and frame is not None and frame.image is image else None
        tutorial=context.tutorial if context else self.tutorial_certificate(image)
        if context:context.stats['detector_ui_reuses']=context.stats.get('detector_ui_reuses',0)+1
        if tutorial:
            analysis.screen_state=ScreenState.TUTORIAL
            analysis.ok_box=tutorial['regions']['continue']
            analysis.confidence=min(tutorial['scores'].values())
            analysis.details['tutorial']=tutorial
            analysis.diagnostics.append('continue_matched' if tutorial.get('kind')=='continue' else 'tutorial_unpredictability_matched')
            return analysis
        ok_box=context.reward if context else self.reward_box(image)
        if ok_box:
            analysis.screen_state=ScreenState.REWARD
            analysis.ok_box=ok_box
            analysis.confidence=.94
            analysis.diagnostics.append('reward_reference_matched')
            return analysis
        live=source in ('live_game','game_window')
        started=time.perf_counter()
        frame=getattr(self,'_live_frame',None)
        native_center=same_capture_center(frame) if live and frame is not None and frame.image is image else None
        center=context.center_for(frame) if context else None
        if center is not None:context.stats['center_reuses']=context.stats.get('center_reuses',0)+1
        else:center=find_center(image,hint=native_center or (getattr(self,'_center_hint',None) if live else None))
        if center is not None and native_center:
            if np.hypot(center[0]-native_center[0],center[1]-native_center[1])>max(5,native_center[2]*.25):center=None
            else:
                center=(center[0],center[1],round(native_center[2]))
                analysis.details['center_scale_source']='same_native_capture'
        self._timings['detector_center']=time.perf_counter()-started
        if center is None:
            self.invalidate()
            analysis.diagnostics.extend(['center_not_found','bloodweb_not_found'])
            return analysis
        cx,cy,base_r=center
        self._center_hint=center if live else None
        analysis.details['center']=list(center)
        if min(cx,w-cx,cy,h-cy)<base_r*2:
            analysis.diagnostics.append('roi_invalid')
            return analysis
        hsv=cv2.cvtColor(image,cv2.COLOR_BGR2HSV)
        gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
        started=time.perf_counter();tooltips=self._tooltip_regions(image,base_r,(cx,cy))
        self._timings['detector_tooltip']=time.perf_counter()-started
        if tooltips:
            self.invalidate()
            analysis.details['transient_overlay']='item_tooltip'
            analysis.details['tooltip_regions']=tooltips
            analysis.diagnostics.append('item_tooltip')
        from src.core.prestige import ready_evidence
        prestige=ready_evidence(image,self.library.root,center)
        if prestige:
            self.invalidate()
            px,py,pr=prestige['center']
            analysis.details['center']=list(prestige['center'])
            analysis.details['prestige_ready']=prestige
            analysis.screen_state=ScreenState.PRESTIGE_READY
            analysis.nodes=[Node(0,px,py,pr,NodeState.CENTER,NodeType.PRESTIGE,
                                 state_confidence=.95,geometry_confidence=.95)]
            analysis.confidence=prestige['score']
            analysis.diagnostics.append('prestige_ready')
            return analysis
        central_patch=image[max(0,cy-base_r):cy+base_r+1,max(0,cx-base_r):cx+base_r+1]
        central_white=float(np.mean(cv2.cvtColor(central_patch,cv2.COLOR_BGR2GRAY)>165))
        region=red_mask(hsv[max(0,cy-base_r*2):cy+base_r*2+1,max(0,cx-base_r*2):cx+base_r*2+1])
        red_extent=cv2.boundingRect(cv2.findNonZero(region)) if np.any(region) else (0,0,0,0)
        if central_white>.09 and red_extent[2]>h*.09:
            self.invalidate()
            analysis.screen_state=ScreenState.PRESTIGE
            analysis.nodes=[Node(0,cx,cy,base_r,NodeState.CENTER,NodeType.PRESTIGE,state_confidence=.9)]
            analysis.confidence=.9
            analysis.diagnostics.append('static_read_only')
            return analysis
        if max(red_extent[2:])<h*.055 and central_white>.02:
            self.invalidate()
            analysis.screen_state=ScreenState.REFRESH
            analysis.nodes=[Node(0,cx,cy,round(max(red_extent[2:])*.6),NodeState.CENTER,NodeType.CENTER,state_confidence=.86)]
            analysis.confidence=.86
            analysis.diagnostics.append('static_read_only')
            return analysis
        started=time.perf_counter();smooth=cv2.GaussianBlur(gray,(5,5),1.2)
        candidates=[]
        self._timings['geometry_cache_check']=0.
        # Gray rims work before the Entity; red rims work after purchases.
        for channel,threshold in ((smooth,22),(red_mask(hsv),13)):
            started_check=time.perf_counter()
            cache=getattr(self,'_hough_cache',{});previous=cache.get(threshold)
            reuse=live and not tooltips and previous is not None and previous[0]==base_r and np.array_equal(previous[1],channel)
            self._timings['geometry_cache_check']+=time.perf_counter()-started_check
            if reuse:circles=previous[2]
            else:
                circles=cv2.HoughCircles(channel,cv2.HOUGH_GRADIENT,1.1,base_r*1.25,
                                        param1=70,param2=threshold,
                                        minRadius=max(12,round(base_r*.65)),maxRadius=round(base_r*1.25))
            if live and not tooltips:
                cache[threshold]=(base_r,channel.copy(),circles);self._hough_cache=cache
            analysis.details.setdefault('geometry_channels_reused',0)
            analysis.details['geometry_channels_reused']+=int(reuse)
            if circles is not None:
                candidates.extend(np.round(circles[0]).astype(int).tolist())
        # Early automatic purchase affects the entire web. Hough's sampling
        # phase can omit a faint locked rim in a crop while the same native
        # client pixels detect it. Add proposals from that exact capture;
        # every proposal still passes the ordinary crop framing/graph rules.
        raw=getattr(frame,'raw_image',None) if native_center else None
        if (raw is not None and type(frame.level) is int and 1<=frame.level<=49 and
                raw.shape[:2]==(frame.window.client.height,frame.window.client.width) and
                0<=frame.roi.x and 0<=frame.roi.y and frame.roi.x+frame.roi.width<=raw.shape[1] and
                frame.roi.y+frame.roi.height<=raw.shape[0]):
            sx=w/frame.roi.width;sy=h/frame.roi.height
            patch=raw[frame.roi.y:frame.roi.y+frame.roi.height,frame.roi.x:frame.roi.x+frame.roi.width]
            same_pixels=(patch.shape==image.shape and np.array_equal(patch,image))
            if same_pixels:
                native_r=round(base_r/sx)
                native_hsv=cv2.cvtColor(raw,cv2.COLOR_BGR2HSV)
                channels=[(red_mask(native_hsv),13)]
                if frame.level<=9:channels.insert(0,(cv2.GaussianBlur(cv2.cvtColor(raw,cv2.COLOR_BGR2GRAY),(5,5),1.2),22))
                native_candidates=[]
                for channel,threshold in channels:
                    extra=cv2.HoughCircles(channel,cv2.HOUGH_GRADIENT,1.1,native_r*1.25,param1=70,param2=threshold,
                        minRadius=max(12,round(native_r*.65)),maxRadius=round(native_r*1.25))
                    if extra is None:continue
                    for x,y,r in np.round(extra[0]).astype(int):
                        # Post-purchase crop Hough can miss a small red rim.
                        # These additional proposals come from exactly today's
                        # full client pixels and retain the normal red support,
                        # content, framing and graph validation below.
                        if threshold==13 and ring_fractions(native_hsv,x,y,r)[0]<=.42:continue
                        native_candidates.append([round((x-frame.roi.x)*sx),round((y-frame.roi.y)*sy),round(r*sx)])
                candidates.extend(native_candidates)
                analysis.details['same_capture_native_geometry_candidates']=len(native_candidates)
        accepted=[];faint=[];framing_cache={}
        for x,y,r in candidates:
            audit=dict(circle=[int(x),int(y),int(r)],accepted=False,reason='outside_web')
            analysis.details['candidates'].append(audit)
            if any(tx-r<x<tx+tw+r and ty-r<y<ty+th+r for tx,ty,tw,th in tooltips):
                audit['reason']='item_tooltip';continue
            distance=np.hypot(x-cx,y-cy)
            if distance<max(base_r*1.5,base_r+r) or distance>base_r*10.0 or x<r or y<r or x+r>=w or y+r>=h:
                continue
            patch=gray[y-r:y+r+1,x-r:x+r+1]
            yy,xx=np.ogrid[-r:r+1,-r:r+1]
            inner=xx*xx+yy*yy<(.65*r)**2
            contrast=float(np.percentile(patch[inner],90)-np.percentile(patch[inner],15))
            saturation=float(np.median(hsv[y-r:y+r+1,x-r:x+r+1,1][inner]))
            red,gold=ring_fractions(hsv,x,y,r)
            colored_tile=saturation>85 and np.mean(patch[inner])<65
            sparse_glyph=r<=base_r*1.1 and float(np.percentile(patch[inner],95)-np.percentile(patch[inner],15))>90
            audit.update(contrast=contrast,red=red,gold=gold,reason='insufficient_local_content')
            if contrast<53 and gold<.60 and not colored_tile and not sparse_glyph:
                from src.core.node_validation import framing
                key=(x,y,r)
                if key not in framing_cache:framing_cache[key]=framing(gray,x,y,r)
                rim=framing_cache[key]
                if not (rim['dark_ring']>=.6 and rim['regular']>=.65):
                    from src.core.node_validation import tile_polygon
                    if rim['valley']>=.28 and rim['regular']>=.45 and rim['dark_ring']>=.25 and tile_polygon(hsv,x,y,r):faint.append((x,y,r))
                    continue
            audit.update(accepted=True,reason='local_content_and_circle')
            quality=contrast+gold*100+red*80
            accepted.append((quality,x,y,r))
        accepted.sort(reverse=True)
        unique=[]
        for _,x,y,r in accepted:
            if not any(np.hypot(x-a,y-b)<base_r*1.1 for a,b,_ in unique):
                unique.append((x,y,r))
        unique.sort(key=lambda c:(round(c[1]/(base_r*1.5)),c[0]))
        self._timings['detector_nodes']=time.perf_counter()-started
        previous=getattr(self,'_previous_structure',None)
        # Validate game framing before category/identity. Hough and contrast
        # alone also find scenery and off-centre fragments of real rims.
        started=time.perf_counter()
        # Hough can select a small inner edge instead of the outer gold ring.
        # Enlarge only that proposal, with a continuous fresh gold annulus at
        # the normal node scale. The usual framing/graph validation follows;
        # no new centre, remembered node or lower state threshold is added.
        radius_corrections=[];dark_radius_corrections=[];corrected=[]
        for x,y,r in unique:
            candidate=fresh_gold_radius(hsv,x,y,r,base_r)
            if candidate!=r:radius_corrections.append(dict(x=x,y=y,before=r,after=candidate))
            r=candidate
            candidate=fresh_dark_radius(hsv,gray,x,y,r,base_r)
            if candidate!=r:dark_radius_corrections.append(dict(x=x,y=y,before=r,after=candidate))
            r=candidate
            corrected.append((x,y,r))
        unique=corrected
        analysis.details['gold_radius_corrections']=radius_corrections
        analysis.details['dark_radius_corrections']=dark_radius_corrections
        provisional=[Node(0,cx,cy,base_r,NodeState.CENTER,NodeType.CENTER)]
        for ident,(x,y,r) in enumerate(unique,1):
            red,gold=ring_fractions(hsv,x,y,r)
            provisional.append(Node(ident,x,y,r,NodeState.PURCHASED if red>.42 else NodeState.LOCKED))
        from src.core.node_validation import validate_candidates
        unique,evidence=validate_candidates(gray,unique,(cx,cy),self._edges(image,provisional),
            previous[2] if live and previous and previous[0]==image.shape and np.hypot(previous[1][0]-cx,previous[1][1]-cy)<base_r*.25 else (),hsv,framing_cache=framing_cache)
        analysis.details['node_validation']=evidence
        # Faint content must not obstruct the initial graph. Verify each
        # deferred proposal against the already accepted web independently.
        deferred=faint+[(e['x'],e['y'],e['radius']) for e in evidence if not e['accepted'] and
                       e['reason']=='unproven_node_frame' and e['regular']>=.5 and e['valley']>=.2]
        verified_edges=None
        for x,y,r in deferred:
            if any(np.hypot(x-a,y-b)<(r+c)*.9 for a,b,c in unique):continue
            proposals=[Node(0,cx,cy,base_r,NodeState.CENTER,NodeType.CENTER)]
            proposals.extend(Node(i,a,b,c,NodeState.LOCKED) for i,(a,b,c) in enumerate(unique+[(x,y,r)],1))
            if verified_edges is None:verified_edges=self._edges(image,proposals[:-1],structure_only=True)
            # Existing pairs use exactly these current pixels. Only the new
            # candidate's segments need another image test; it may obstruct
            # an existing segment, so filter that geometry explicitly.
            edges=[];candidate=proposals[-1]
            for ai,bi in verified_edges:
                a,b=proposals[ai],proposals[bi];dx,dy=b.x-a.x,b.y-a.y;distance=dx*dx+dy*dy
                position=((candidate.x-a.x)*dx+(candidate.y-a.y)*dy)/distance
                if not (0<position<1 and np.hypot(candidate.x-a.x-position*dx,candidate.y-a.y-position*dy)<candidate.radius*.9):edges.append((ai,bi))
            edges.extend(self._edges(image,proposals,structure_only=True,focus_node=candidate.id))
            trial,review=validate_candidates(gray,unique+[(x,y,r)],(cx,cy),edges,hsv=hsv,confirmed_graph=True,only_last=True,framing_cache=framing_cache)
            last=review[-1]
            if last['accepted'] and last['connected']:
                unique.append((last['x'],last['y'],last['radius']));analysis.details['node_validation'].append(last)
                verified_edges=None
        self._timings['node_validation']=time.perf_counter()-started
        consistent=(live and previous is not None and previous[0]==image.shape and
            np.hypot(previous[1][0]-cx,previous[1][1]-cy)<base_r*.25 and len(previous[2])==len(unique) and
            all(any(np.hypot(x-a,y-b)<max(6,base_r*.25) for a,b,_ in previous[2]) for x,y,r in unique))
        analysis.details['analysis_mode']='incremental_identity' if consistent else 'full'
        analysis.details['full_reason']=None if consistent else 'geometry_or_capture_changed' if previous else 'initial_or_file'
        if not consistent:self.matcher.clear_matches()
        hits=self.matcher.cache_hits
        misses=self.matcher.cache_misses
        self._timings['detector_states']=0.;self._timings['detector_icons']=0.
        self._timings['recognition_stage1']=0.;self._timings['recognition_stage2']=0.;self._timings['memory_update']=0.
        observations={}
        observed_frames={(e['x'],e['y'],e['radius']):e for e in analysis.details['node_validation'] if e.get('accepted')}
        if live:
            started_memory=time.perf_counter();self.memory.begin(getattr(self,'_capture_context',None) or (self.role,self.character,image.shape),(cx,cy),unique)
            self._timings['memory_update']+=time.perf_counter()-started_memory
        for i,(x,y,r) in enumerate(unique,1):
            started=time.perf_counter()
            red,gold=ring_fractions(hsv,x,y,r)
            patch=image[y-r:y+r+1,x-r:x+r+1]
            mask=node_mask(r,patch.shape[:2])
            pgray=cv2.cvtColor(patch,cv2.COLOR_BGR2GRAY)
            interior=float(np.percentile(pgray[mask>0],95))
            ring_dark=self._dark_rim(gray,x,y,r)
            if red>.42:
                state,certainty=NodeState.PURCHASED,.93
            elif gold>.42:
                state,certainty=NodeState.AVAILABLE,.90
            elif ring_dark>.40:
                state,certainty=NodeState.ABSORBED,.78
            elif interior>75:
                state,certainty=NodeState.LOCKED,.78
            else:
                state,certainty=NodeState.UNCERTAIN,.40
            content_r=max(r,round(base_r*.90))
            content_patch=image[y-content_r:y+content_r+1,x-content_r:x+content_r+1]
            category=classify_category(content_patch,content_r)
            diamond=self._is_perk(content_patch,content_r)
            is_perk=diamond and category.category!='offering' and self._perk_marker(content_patch,content_r)
            if diamond and not is_perk and category.category!='offering':
                category.candidates=list(dict.fromkeys(category.candidates+['perk']))
            self._timings['detector_states']+=time.perf_counter()-started
            started=time.perf_counter()
            if is_perk:
                match=Match(reason='suspected_perk',rarity=rarity_of(content_patch,node_mask(content_r,content_patch.shape[:2])))
                if live:observations[(x,y)]=(None,match,None)
            else:
                if live:
                    feature,fingerprint,saved,previous=self.memory.identity(x,y,content_patch,content_r,category)
                    match=saved or self.matcher.match(content_patch,content_r,use_cache=consistent,prepared_feature=feature,prepared_rarity=self.memory.last_rarity)
                    observations[(x,y)]=(fingerprint,match,previous)
                    if saved:self.matcher.last_timings={}
                else:match=self.matcher.match(content_patch,content_r,use_cache=consistent)
                for stage,value in self.matcher.last_timings.items():self._timings[stage]+=value
            self._timings['detector_icons']+=time.perf_counter()-started
            match.category=category.category;match.category_confidence=category.confidence;match.category_candidates=list(category.candidates)
            kind=NodeType.UNKNOWN
            if is_perk:
                kind=NodeType.PERK
                match.icon_id=None
                match.reason='suspected_perk'
                category.category='perk';category.confidence=.95;category.candidates=['perk']
            elif match.icon_id:
                entry=self.matcher.by_id[match.icon_id]
                kind=NodeType(entry.category[:-1])
            raw_geometry=round(min(.95,.55+max(red,gold)*.2+min(1,interior/180)*.15),3)
            geometry=framed_locked_confidence(raw_geometry,state,observed_frames.get((x,y,r)),gray,x,y,r)
            if geometry!=raw_geometry:
                analysis.details.setdefault('framed_locked_geometry',[]).append(dict(node=i,raw=raw_geometry,observed=geometry))
            analysis.nodes.append(Node(i,x,y,r,state,kind,match.icon_id,match.candidate_id,
                match.confidence,certainty,match.candidates,
                geometry_confidence=geometry,
                icon_margin=match.margin,match_reason=match.reason,rarity=match.rarity,
                glyph_score=match.glyph_score,content_radius=content_r,contour_score=match.contour_score,
                category_hint=category.category,category_confidence=category.confidence,category_candidates=category.candidates,
                event_offering=category.event_offering and kind not in (NodeType.PERK,NodeType.ITEM,NodeType.ADDON),
                event_confidence=category.confidence if category.event_offering else 0.,
                orange_background=category.orange_fraction>=.45,
                rarity_confidence=match.rarity_confidence,rarity_tie_break=match.rarity_tie_break,
                glyph_detail_score=match.glyph_detail_score,glyph_detail_tie_break=match.glyph_detail_tie_break,
                recognition_status=match.recognition_status,top10=match.top10,reranked=match.reranked,recognition_method=match.recognition_method))
            analysis.nodes[-1]._opening_overlaps=getattr(match,'_opening_overlaps',None)
        radius=min(base_r,max(18,int(h*.045)))
        central_patch=image[max(0,cy-radius):cy+radius+1,max(0,cx-radius):cx+radius+1]
        central_gray=cv2.cvtColor(central_patch,cv2.COLOR_BGR2GRAY)
        white=float(np.mean(central_gray>165))
        # A refresh spinner is much smaller than the prestige wreath.
        center_red=red_mask(hsv[max(0,cy-radius*2):cy+radius*2+1,max(0,cx-radius*2):cx+radius*2+1])
        red_extent=cv2.boundingRect(cv2.findNonZero(center_red)) if np.any(center_red) else (0,0,0,0)
        if not unique and white>.09 and red_extent[2]>h*.09:
            analysis.screen_state=ScreenState.PRESTIGE
            central_type=NodeType.PRESTIGE
        elif not unique:
            analysis.screen_state=ScreenState.REFRESH
            central_type=NodeType.CENTER
        else:
            analysis.screen_state=ScreenState.BLOODWEB
            central_type=NodeType.CENTER
        analysis.nodes.insert(0,Node(0,cx,cy,radius,NodeState.CENTER,central_type,
                                     state_confidence=.9,geometry_confidence=.9,match_reason='structural_center'))
        analysis.nodes=organize_rings(analysis.nodes)
        started=time.perf_counter();analysis.edges=self._edges(image,analysis.nodes)
        linked={n for edge in analysis.edges for n in edge}
        analysis.details['rejected_structure']=[e for e in evidence if not e['accepted']]
        self._timings['detector_graph']=time.perf_counter()-started
        if live:
            started_memory=time.perf_counter();self.memory.update(analysis.nodes,observations,analysis.edges,self._live_frame)
            analysis.details['bloodweb_memory']=self.memory.summary();self._timings['memory_update']+=time.perf_counter()-started_memory
        # Only identity calculations may be reused. Geometry, states and every
        # graph edge above always come from this fresh image.
        self._previous_structure=(image.shape,(cx,cy),unique) if live and not tooltips else None
        analysis.details['identity_cache_hits']=self.matcher.cache_hits-hits
        analysis.details['full_matcher_calls']=self.matcher.cache_misses-misses
        analysis.confidence=round(float(np.mean([n.state_confidence for n in analysis.nodes])),3)
        # Stage 1/2 is always read-only. Shape scores are not click authorization.
        analysis.diagnostics.extend(['static_read_only','level_ocr_unavailable'])
        unknown=sum(n.icon_id is None and n.type not in (NodeType.CENTER,NodeType.PRESTIGE,NodeType.PERK) for n in analysis.nodes)
        if unknown:
            analysis.diagnostics.append(f'uncertain_icons:{unknown}')
        return analysis

    @staticmethod
    def _tooltip_regions(image,radius,center):
        # Wide rarity-coloured header + dark body, unlike circular item tiles.
        # This is a visual overlay, not evidence of another Windows window.
        hsv=cv2.cvtColor(image,cv2.COLOR_BGR2HSV);h,w=image.shape[:2]
        mask=((hsv[:,:,1]>70)&(hsv[:,:,2]>25)).astype(np.uint8)*255
        mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,np.ones((3,max(9,round(radius*.65))),np.uint8))
        _,_,stats,_=cv2.connectedComponentsWithStats(mask)
        regions=[]
        for x,y,bw,bh,area in stats[1:]:
            if not (bw>radius*5 and radius*.35<bh<radius*2 and bw/bh>4):continue
            if abs(x-center[0])>radius*10 or abs(y-center[1])>radius*10:continue
            body=hsv[y+bh:min(h,y+bh+radius*4),x:x+bw,2]
            if body.size==0 or float(np.mean(body<40))<.5:continue
            if any(abs(int(x)-a)<radius and abs(int(y)-b)<radius*2 for a,b,_,_ in regions):continue
            regions.append((int(x),int(y),int(bw),h-int(y)))
        return regions

    @staticmethod
    def _rim_support(gray,x,y,r,consistent=False):
        angles=np.arange(96)*2*np.pi/96; votes=[]
        for scale in np.arange(.8,1.22,.05):
            def ring(offset):
                xs=np.clip(np.round(x+np.cos(angles)*(r*scale+offset)).astype(int),0,gray.shape[1]-1)
                ys=np.clip(np.round(y+np.sin(angles)*(r*scale+offset)).astype(int),0,gray.shape[0]-1)
                return gray[ys,xs].astype(float)
            votes.append(np.abs(ring(-2)-ring(2))>15)
        return float(max(np.mean(v) for v in votes) if consistent else np.mean(np.any(votes,axis=0)))

    @staticmethod
    def _dark_rim(gray: np.ndarray,x:int,y:int,r:int) -> float:
        a=np.linspace(0,2*np.pi,96,endpoint=False)
        fractions=[]
        for scale in (.8,.9,1.0):
            xs=np.clip(np.round(x+np.cos(a)*r*scale).astype(int),0,gray.shape[1]-1)
            ys=np.clip(np.round(y+np.sin(a)*r*scale).astype(int),0,gray.shape[0]-1)
            fractions.append(float(np.mean(gray[ys,xs]<20)))
        return max(fractions)

    @staticmethod
    def _perk_marker(patch,r):
        # Perk tier slashes are independent of the inner emblem and background
        # diamond. A partially visible blue offering must not become PERKS.
        hsv=cv2.cvtColor(patch,cv2.COLOR_BGR2HSV);h,w=patch.shape[:2];yy,xx=np.ogrid[:h,:w]
        dx=xx-w//2;dy=yy-h//2
        region=(dx>r*.25)&(dy<-r*.30)&(dx*dx+dy*dy>(r*.55)**2)
        threshold=max(70,float(np.percentile(hsv[:,:,2],95))*.72)
        mask=((hsv[:,:,1]<100)&(hsv[:,:,2]>threshold)&region).astype(np.uint8)
        count,labels,stats,_=cv2.connectedComponentsWithStats(mask);strokes=[]
        for i in range(1,count):
            if not 2<=stats[i,cv2.CC_STAT_AREA]<=r*r*.08:continue
            points=np.column_stack(np.where(labels==i))[:,::-1].astype(float)
            if len(points)<3:continue
            values,vectors=np.linalg.eigh(np.cov(points.T))
            if values[-1]<2 or values[-1]/max(.1,values[0])<2.5:continue
            vector=vectors[:,-1];angle=np.degrees(np.arctan2(vector[1],vector[0]))%180
            if 60<angle<120:strokes.append(angle)
        return bool(strokes) and max(strokes)-min(strokes)<30

    @staticmethod
    def _is_perk(patch: np.ndarray,r:int) -> bool:
        hsv=cv2.cvtColor(patch,cv2.COLOR_BGR2HSV)
        yy,xx=np.ogrid[:patch.shape[0],:patch.shape[1]]
        saturation_floor=np.where((hsv[:,:,0]>80)&(hsv[:,:,0]<125),90,60)
        core=((xx-r)**2+(yy-r)**2<(r*.72)**2)
        saturated=(hsv[:,:,1]>120)&(hsv[:,:,2]>25)&core
        hues=hsv[:,:,0][saturated]
        if len(hues)<r*r*.10:return False
        peak=int(np.argmax(np.bincount(hues,minlength=180)))
        if not 35<peak<170:return False
        purple=((hsv[:,:,0]>35)&(hsv[:,:,0]<170)&(hsv[:,:,1]>saturation_floor)&(hsv[:,:,2]>25)
                &(np.abs(hsv[:,:,0].astype(int)-peak)<12)
                &((xx-r)**2+(yy-r)**2<(r*.92)**2)).astype(np.uint8)*255
        points=cv2.findNonZero(purple)
        if points is None or len(points)<r*r*.15:
            return False
        hull=cv2.convexHull(points)
        polygon=cv2.approxPolyDP(hull,.05*cv2.arcLength(hull,True),True)
        if len(polygon)!=4 or cv2.contourArea(hull)<r*r*.5:
            return False
        vertices=np.abs(polygon[:,0]-np.array([r,r]))
        # A perk is a diamond with four axis-aligned tips. A square item and
        # a six-sided offering must not be reclassified simply for being purple.
        return float(np.mean(np.min(vertices,axis=1)))<r*.18

    @staticmethod
    def _edges(image:np.ndarray,nodes:list[Node],structure_only=False,focus_node=None,allowed_pairs=None) -> list[tuple[int,int]]:
        hsv=cv2.cvtColor(image,cv2.COLOR_BGR2HSV)
        gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
        edges=[]
        points=np.array([(n.x,n.y) for n in nodes],float)
        identities=np.array([n.id for n in nodes]);obstacle_radii=np.array([n.radius*.9 for n in nodes])
        for i,a in enumerate(nodes):
            for b in nodes[i+1:]:
                if allowed_pairs is not None and (a.id,b.id) not in allowed_pairs:continue
                if focus_node is not None and focus_node not in (a.id,b.id):continue
                distance=np.hypot(a.x-b.x,a.y-b.y)
                radius=(a.radius+b.radius)/2
                if distance<radius*2.1 or distance>radius*6.0:
                    continue
                segment=np.array([b.x-a.x,b.y-a.y],float)
                deltas=points-[a.x,a.y];positions=deltas@segment/(distance*distance)
                residual=deltas-positions[:,None]*segment
                obstruction=((identities!=a.id)&(identities!=b.id)&(positions>0)&(positions<1)&
                    (np.sum(residual*residual,axis=1)<obstacle_radii*obstacle_radii))
                if np.any(obstruction):continue
                start=(a.radius+5)/distance; end=1-(b.radius+5)/distance
                if start>=end:
                    continue
                t=np.linspace(start,end,30)
                xs=np.clip(np.round(a.x+(b.x-a.x)*t).astype(int),0,image.shape[1]-1)
                ys=np.clip(np.round(a.y+(b.y-a.y)*t).astype(int),0,image.shape[0]-1)
                p=hsv[ys,xs]
                colored=(p[:,1]>30)&(p[:,2]>38)&((p[:,0]<42)|(p[:,0]>170))
                # Unpurchased connections have two dark borders around a gray fill.
                # Test local contrast across the segment, rather than treating every
                # dark background pixel as a connection.
                def sample(offset):
                    xx=np.clip(np.round(a.x+(b.x-a.x)*t-(b.y-a.y)/distance*offset).astype(int),0,image.shape[1]-1)
                    yy=np.clip(np.round(a.y+(b.y-a.y)*t+(b.x-a.x)/distance*offset).astype(int),0,image.shape[0]-1)
                    return gray[yy,xx].astype(np.float32)
                offsets=np.linspace(-radius*.15,radius*.15,13)
                stripe=np.min(np.stack([sample(off) for off in offsets]),axis=0)
                contrast=(sample(radius*.32)+sample(-radius*.32))/2-stripe
                outlined=np.mean(contrast>15)>.75 and np.mean(contrast)>20
                # On light scenery a purchased red line can be brighter than
                # its surroundings. Gray dark-stripe contrast then fails even
                # though the narrow saturated red connection is continuous.
                def red_at(offset):
                    xx=np.clip(np.round(a.x+(b.x-a.x)*t-(b.y-a.y)/distance*offset).astype(int),0,image.shape[1]-1)
                    yy=np.clip(np.round(a.y+(b.y-a.y)*t+(b.x-a.x)/distance*offset).astype(int),0,image.shape[0]-1)
                    q=hsv[yy,xx]
                    return float(np.mean(((q[:,0]<10)|(q[:,0]>172))&(q[:,1]>150)&(q[:,2]>75)))
                owned=(NodeState.CENTER,NodeState.PURCHASED)
                red_connection=(a.state in owned and b.state in owned and red_at(0)>.9 and
                    max(red_at(radius*.5),red_at(-radius*.5))<.25)
                # Orange fire/scenery can be coloured along a nonexistent edge.
                # A high mean from a few bright flames is not a continuous
                # connection. Require the same longitudinal support as a border.
                colored_connection=(not structure_only and np.mean(colored)>.75
                    and np.mean(contrast>15)>.75 and np.mean(contrast)>15)
                if colored_connection or outlined or red_connection:
                    edges.append((a.id,b.id))
        return edges

    @staticmethod
    def render_preview(image:np.ndarray,analysis:Analysis,plan=None) -> np.ndarray:
        """Draw the current result without modifying evidence or writing files."""
        canvas=image.copy()
        colors={NodeState.AVAILABLE:(110,220,235),NodeState.PURCHASED:(50,50,240),
                NodeState.ABSORBED:(155,120,160),NodeState.LOCKED:(190,160,95),
                NodeState.CENTER:(20,70,210),NodeState.UNCERTAIN:(0,150,255)}
        by_id={n.id:n for n in analysis.nodes}
        for ai,bi in analysis.edges:
            a,b=by_id[ai],by_id[bi]
            cv2.line(canvas,(a.x,a.y),(b.x,b.y),(145,105,65),1,cv2.LINE_AA)
        for node in analysis.nodes:
            color=colors[node.state]
            cv2.circle(canvas,(node.x,node.y),node.radius,color,2,cv2.LINE_AA)
            label=f'{node.id} {node.state.value}'
            name=node.icon_id or node.type.value
            text_score=f'{node.confidence:.2f}' if node.icon_id else node.match_reason
            for k,text in enumerate((label,f'{name} {text_score}')):
                pos=(max(0,node.x-node.radius),min(analysis.height-8,node.y-node.radius-7+k*13))
                cv2.putText(canvas,text,pos,cv2.FONT_HERSHEY_SIMPLEX,.34,(0,0,0),3,cv2.LINE_AA)
                cv2.putText(canvas,text,pos,cv2.FONT_HERSHEY_SIMPLEX,.34,color,1,cv2.LINE_AA)
        if plan and plan.route:
            route=[by_id[n] for n in plan.route if n in by_id]
            for a,b in zip(route,route[1:]):
                cv2.line(canvas,(a.x,a.y),(b.x,b.y),(50,255,220),4,cv2.LINE_AA)
            if route:
                cv2.circle(canvas,(route[-1].x,route[-1].y),route[-1].radius+7,(50,255,220),3)
                cv2.drawMarker(canvas,(route[0].x,route[0].y),(255,255,255),cv2.MARKER_CROSS,24,3)
        if analysis.ok_box:
            x,y,w,h=analysis.ok_box
            cv2.rectangle(canvas,(x,y),(x+w,y+h),(110,220,235),2)
        cv2.putText(canvas,f'{analysis.screen_state.value} | {len(analysis.nodes)} nodes',
                    (12,analysis.height-18),cv2.FONT_HERSHEY_SIMPLEX,.6,(240,240,240),2,cv2.LINE_AA)
        return canvas

    def save_debug(self,image:np.ndarray,analysis:Analysis,output:Path,plan=None,writer=None) -> Path:
        writer=writer or write_image;output=Path(output)
        crops=output.parent/(output.stem+'_nodes')
        for node in analysis.nodes:
            r=node.content_radius or node.radius
            patch=image[max(0,node.y-r):min(image.shape[0],node.y+r+1),max(0,node.x-r):min(image.shape[1],node.x+r+1)]
            crop_path=crops/f'{node.id:03d}_original.png'
            writer(crop_path,patch);node.crop_path=str(crop_path.resolve())
            normalized=glyph(patch,node_mask(r,patch.shape[:2]),remove_badge=True)
            normalized_path=crops/f'{node.id:03d}_normalized.png'
            writer(normalized_path,normalized if normalized is not None else np.zeros((64,64),np.uint8))
            node.normalized_path=str(normalized_path.resolve())
        canvas=self.render_preview(image,analysis,plan)
        writer(output,canvas)
        analysis.debug_image=str(output.resolve())
        output.with_suffix('.json').write_text(json.dumps(analysis.to_dict(),ensure_ascii=False,indent=2),encoding='utf-8')
        return output

    def analyze_file(self,path:Path,output:Path | None=None) -> Analysis:
        image=read_image(path)
        result=self.analyze(image,str(path))
        if output:
            self.save_debug(image,result,output)
        return result
