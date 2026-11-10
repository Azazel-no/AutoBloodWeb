"""Read a native tooltip price relative to a visually reviewed currency glyph.

This is a stand-only price upper bound. It does not assign item identities,
prove a chain preview, or authorize input. The native source SHA is mandatory.
"""
import hashlib
from pathlib import Path
import cv2
import numpy as np
from src.utils.images import read_image


def prepared_single_key(frame,result,source):
    """Carry the reviewed filler contract to the final current-map guard."""
    from scripts.live_test_v54 import scene_key
    key=scene_key(frame,result)
    if (source.get('single_filler_preparation') is True and
            source['signature']==key['signature'] and source['level']==key['level']):
        key['single_filler_preparation']=True
    return key


def single_scene_valid(key,frame,result,plan,point):
    """A price belongs to one directly available target in the current graph."""
    from dataclasses import asdict
    from src.core.models import NodeState,ScreenState
    from src.core.geometry import purchasable
    import math
    current=dict(hwnd=frame.window.hwnd,pid=frame.window.pid,client=asdict(frame.window.client),monitor=asdict(frame.window.monitor),dpi=frame.window.dpi)
    if (not frame.live or frame.probe_only or not frame.window.foreground or result.screen_state!=ScreenState.BLOODWEB or
            key['signature']!=current or key['level']!=result.level):return False
    old=[n for n in key['nodes'] if math.dist(n['point'],point)<=5]
    found=[n for n in result.nodes if math.dist(frame.to_screen(n.x,n.y),point)<=5]
    if len(old)!=1 or len(found)!=1:return False
    node=found[0]
    equivalent_filler=(key.get('single_filler_preparation') is True and plan is not None and plan.reason in ('filler','PERSISTENT:filler')
        and old[0]['icon'] is not None and node.icon_id==old[0]['icon'] and node.confidence>=.86
        and node.contour_score>=.68 and node.icon_margin>=.035 and node.geometry_confidence>=.75)
    same_decision=plan is not None and (plan.next_node==node.id or equivalent_filler)
    return (purchasable(node,result.nodes,result.edges) and same_decision and
            not(old[0]['icon'] and node.icon_id and old[0]['icon']!=node.icon_id))


def center_scene_valid(key,frame,result):
    """The centre buys all unowned points; a newly read glyph is not a new point."""
    from dataclasses import asdict
    from src.core.models import NodeState,ScreenState
    import math
    current=dict(hwnd=frame.window.hwnd,pid=frame.window.pid,client=asdict(frame.window.client),monitor=asdict(frame.window.monitor),dpi=frame.window.dpi)
    if (not frame.live or frame.probe_only or not frame.window.foreground or key['signature']!=current or
            type(result.level) is not int or key['level']!=result.level or not 1<=result.level<=9 or result.level_confidence<.95 or
            result.screen_state!=ScreenState.BLOODWEB or len(key['nodes'])!=len(result.nodes)):return False
    matched=set()
    for old in key['nodes']:
        tolerance=max(8,old.get('radius',25)*.2)
        found=[n for n in result.nodes if math.dist(frame.to_screen(n.x,n.y),old['point'])<=tolerance]
        if len(found)!=1:return False
        node=found[0]
        if node.id in matched or node.geometry_confidence<.65:return False
        matched.add(node.id)
        if old['state'] in ('available','locked'):
            if node.state not in (NodeState.AVAILABLE,NodeState.LOCKED):return False
        elif node.state.value!=old['state']:return False
        if old['icon'] and node.icon_id and old['icon']!=node.icon_id:return False
    return True


class NativePriceLocator:
    def __init__(self,root,calibration,reader):
        path=Path(root)/calibration['source']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=calibration['sha256']:raise ValueError('native_price_source_changed')
        image=read_image(path);x,y,w,h=calibration['currency_box']
        self.template=cv2.cvtColor(image[y:y+h,x:x+w],cv2.COLOR_BGR2GRAY)
        self.client_size=calibration['client_size'];self.reader=reader

    def locate(self,image,point):
        h,w=image.shape[:2]
        if [w,h]!=self.client_size:return None
        # DBD flips cards to the left near the web's right boundary.
        # Both searches exclude HUD and still require the reviewed BP glyph
        # and the complete strictly decoded numeral run.
        x0=max(0,round(point[0]+32));x1=min(w,round(point[0]+800));y0=round(h*.17);y1=round(h*.94)
        gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY);region=gray[y0:y1,x0:x1];th,tw=self.template.shape
        from src.core.bloodweb_detector import BloodwebDetector
        tooltips=BloodwebDetector._tooltip_regions(image,round(h*.037),point)
        right=self.locate_region(region,(x0,y0),tooltips)
        left0=max(round(w*.14),round(point[0]-800));left1=max(0,round(point[0]-32))
        left=self.locate_region(gray[y0:y1,left0:left1],(left0,y0),tooltips)
        if right and left:return None
        return right or left

    def reobserve_box(self,image,box):
        """Recheck a previously complete numeral box and its adjacent BP glyph."""
        h,w=image.shape[:2];x,y,bw,bh=box;th,tw=self.template.shape
        gx=x-tw;gy=y-3
        if [w,h]!=self.client_size or min(gx,gy)<0 or x+bw>w or y+bh>h:return None
        gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
        glyph=gray[gy:gy+th,gx:gx+tw]
        if glyph.shape!=self.template.shape or cv2.matchTemplate(glyph,self.template,cv2.TM_CCOEFF_NORMED)[0,0]<.96:return None
        return self.reader.decode(image[y:y+bh,x:x+bw])

    def locate_region(self,region,origin=(0,0),tooltips=None):
        """Decode an original native region, preserving its full-client offset."""
        if region.ndim==3:region=cv2.cvtColor(region,cv2.COLOR_BGR2GRAY)
        th,tw=self.template.shape;x0,y0=origin
        if region.shape[0]<th or region.shape[1]<tw:return None
        scores=cv2.matchTemplate(region,self.template,cv2.TM_CCOEFF_NORMED);found=[]
        for _ in range(4):
            _,score,_,(x,y)=cv2.minMaxLoc(scores)
            if score<.96:break
            gx,gy=x+x0,y+y0;box=[gx+tw,gy+3,105,th+3];boxes=[box.copy()]
            if tooltips:
                enclosing=[r for r in tooltips if r[0]<=gx and gx+tw<r[0]+r[2] and r[1]<gy<r[1]+r[3]]
                if len(enclosing)>1:
                    # A single animated event card can expose both its title
                    # and rarity strip as header candidates. Deduplicate only
                    # nested bounds sharing the same right and bottom edges;
                    # independent cards remain ambiguous.
                    rights=[r[0]+r[2] for r in enclosing];bottoms=[r[1]+r[3] for r in enclosing]
                    if max(rights)-min(rights)>8 or max(bottoms)-min(bottoms)>8:return None
                    left=max(r[0] for r in enclosing);top=max(r[1] for r in enclosing)
                    right=min(rights);bottom=min(bottoms)
                    if (right-left)*(bottom-top)<.9*min(r[2]*r[3] for r in enclosing):return None
                    enclosing=[(left,top,right-left,bottom-top)]
                # Do not feed scenery beyond the native card to digit OCR.
                # The card's independently detected header bounds its body.
                if enclosing:
                    box[2]=min(box[2],enclosing[0][0]+enclosing[0][2]-20-box[0])
                    if box[2]>=20:boxes.append(box.copy())
            bx,by,bw,bh=boxes[0]
            strip=region[by-y0:by-y0+bh,bx-x0:bx-x0+bw]
            if strip.shape==(bh,bw):
                _,_,stats,_=cv2.connectedComponentsWithStats((strip>200).astype(np.uint8))
                ink=sorted((list(map(int,s[:4])) for s in stats[1:] if s[3]>=9 and s[4]>=6),key=lambda s:s[0])
                if ink and ink[0][0]<=16:
                    # The numeral baseline is anchored next to the reviewed
                    # BP glyph. Fire outside the card can enter the wide strip
                    # at a different vertical baseline; it is not another digit.
                    # Keep every component on the numeral row, including an
                    # unwanted suffix (which must still fail the strict decoder).
                    first=ink[0]
                    ink=[s for s in ink if abs(s[1]-first[1])<=3 and abs(s[3]-first[3])<=3]
                # Bound the entire white numeral run, not an arbitrary prefix.
                # Every sizeable component must have consistent digit geometry;
                # scenery or text touching the strip edge disables this crop.
                if (3<=len(ink)<=9 and ink[0][0]<=16 and
                        all(3<=w<=22 and 10<=h<=25 and x+w<bw for x,y,w,h in ink) and
                        max(s[3] for s in ink)-min(s[3] for s in ink)<=3 and
                        all(0<=b[0]-a[0]-a[2]<=16 for a,b in zip(ink,ink[1:]))):
                    boxes.append([bx,by,min(bw,ink[-1][0]+ink[-1][2]+8),bh])
            decoded=[]
            for candidate in boxes:
                bx,by,bw,bh=candidate
                if bx-x0+bw>region.shape[1] or by-y0+bh>region.shape[0]:continue
                value=self.reader.decode(region[by-y0:by-y0+bh,bx-x0:bx-x0+bw])
                if value is not None:decoded.append((value,candidate))
            # Card-edge clipping can change Otsu's threshold by a few pixels.
            # Accept the same strict decoder on either geometrically bounded
            # region; contradictory readable numbers are always ambiguous.
            if not decoded or len({v for v,_ in decoded})!=1:return None
            value,box=decoded[-1]
            if not 1000<=value<=2000000:return None
            found.append(dict(value=value,box=box,score=float(score),currency=[gx,gy,tw,th]))
            scores[max(0,y-th):min(scores.shape[0],y+th),max(0,x-tw):min(scores.shape[1],x+tw)]=-1
        if not found:return None
        # Locked tooltips can show standalone and full-path prices. The larger
        # displayed price bounds this single action; it is not a chain proof.
        return dict(max(found,key=lambda p:p['value']),labels=found)


class NativeTotalPriceLocator:
    """Reviewed native web badge distinguishes total path price from item cost."""
    def __init__(self,root,calibration,currency):
        if calibration.get('reviewed_total_label') is not True:raise ValueError('native_total_label_not_reviewed')
        self.calibration=calibration;self.currency=currency
        path=Path(root)/calibration['source']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=calibration['sha256']:raise ValueError('native_total_source_changed')
        x,y,w,h=calibration['web_badge_box'];image=read_image(path)
        self.template=cv2.cvtColor(image[y:y+h,x:x+w],cv2.COLOR_BGR2GRAY)
        self.provenance=hashlib.sha256(__import__('json').dumps(calibration,sort_keys=True).encode()).hexdigest()

    def locate(self,image,point):
        h,w=image.shape[:2]
        if [w,h]!=self.calibration['client_size']:return None
        th,tw=self.template.shape;x0=max(round(w*.14),point[0]-800);x1=min(w,point[0]+800);y0=round(h*.17);y1=round(h*.94)
        gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY);region=gray[y0:y1,x0:x1]
        if region.shape[0]<th or region.shape[1]<tw:return None
        scores=cv2.matchTemplate(region,self.template,cv2.TM_CCOEFF_NORMED)
        _,score,_,(x,y)=cv2.minMaxLoc(scores)
        if score<.96:return None
        scores[max(0,y-th):y+th+1,max(0,x-tw):x+tw+1]=-1
        if scores.max(initial=-1)>=.96:return None
        gx=x+x0;gy=y+y0;left=gx+tw;top=max(0,gy-10)
        value=self.currency.locate_region(image[top:min(h,gy+80),left:min(w,left+200)],(left,top))
        if value is None:return None
        cx,cy,_,_=value['currency'];dx,dy=self.calibration['currency_offset']
        if abs(cx-gx-dx)>3 or abs(cy-gy-dy)>3:return None
        return dict(value,web_badge=[gx,gy,tw,th],badge_score=float(score),provenance=self.provenance)
