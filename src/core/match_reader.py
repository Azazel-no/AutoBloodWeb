"""Read the Tab scoreboard and the HUD portrait column from captured pixels only.

No input, no game memory, no network. Positions come from
assets/ui/match_layout.json, measured on real 1920x1080 frames and scaled by
client height. Unknown perks stay None and are drawn as "?"; nothing is guessed.
"""
from dataclasses import dataclass, field
import json
from pathlib import Path
import copy
import cv2
import numpy as np
from src.core.perk_matcher import SlotResult

LAYOUT='assets/ui/match_layout.json'
EMPTY='empty'


def load_layout(root):
    path=Path(root)/LAYOUT
    if not path.is_file():return None
    layout=json.loads(path.read_text(encoding='utf-8'))
    if layout.get('schema')!=2:raise ValueError('invalid_match_layout')
    return layout


@dataclass
class TabRow:
    index: int
    own: bool
    slots: tuple  # SlotResult per Tab perk slot 1-4; icon_id 'empty' for an empty slot
    tiles: tuple  # Captured BGR diamond tiles, drawn as the real in-game art
    portrait: tuple = field(repr=False)  # (lab, mask) of the inner portrait circle


class Geometry:
    def __init__(self,layout,shape):
        height,width=shape[:2];self.scale=height/layout['reference_height']
        self.tab_origin=(width-height*layout['tab']['aspect'])/2
        self.layout=layout

    def tab(self,x,y):return round(self.tab_origin+x*self.scale),round(y*self.scale)
    def hud(self,x,y):return round(x*self.scale),round(y*self.scale)
    def size(self,value):return max(1,round(value*self.scale))


def right_centre_x(layout,half):
    """Centre, in reference pixels, of something `half` wide drawn right of the nicknames."""
    return layout['hud']['right_x']+half+2


def crop(image,cx,cy,half):
    y0,y1,x0,x1=cy-half,cy+half,cx-half,cx+half
    if y0<0 or x0<0 or y1>image.shape[0] or x1>image.shape[1]:return None
    return image[y0:y1,x0:x1]


def portrait_feature(image,center,radius,size=48,inner=.72):
    """Inner circle only: status rings, slashes and backgrounds differ between Tab and HUD."""
    patch=crop(image,center[0],center[1],radius)
    if patch is None:return None
    patch=cv2.resize(patch,(size*2,size*2),interpolation=cv2.INTER_AREA)
    yy,xx=np.ogrid[:size*2,:size*2]
    mask=((xx-size)**2+(yy-size)**2)<=(size*inner)**2
    return cv2.cvtColor(patch,cv2.COLOR_BGR2LAB).astype(np.float32),mask


def portrait_similarity(a,b):
    """Best weighted correlation of L/a/b inside the circle over small shifts and scales."""
    if a is None or b is None:return -1.
    lab_a,mask=a;size=lab_a.shape[0];best=-1.
    p=[lab_a[:,:,c][mask] for c in range(3)];p=[v-v.mean() for v in p]
    norms=[np.sqrt((v*v).sum()) for v in p]
    for scale in (.9,1.,1.1):
        resized=cv2.resize(b[0],None,fx=scale,fy=scale);off=(resized.shape[0]-size)//2
        for dy in (-4,-2,0,2,4):
            for dx in (-4,-2,0,2,4):
                y0,x0=off+dy,off+dx
                if y0<0 or x0<0 or y0+size>resized.shape[0] or x0+size>resized.shape[1]:continue
                window=resized[y0:y0+size,x0:x0+size];total=0.
                for c,weight in ((0,.6),(1,.2),(2,.2)):
                    q=window[:,:,c][mask];q=q-q.mean()
                    total+=weight*float((p[c]*q).sum()/(norms[c]*np.sqrt((q*q).sum())+1e-6))
                best=max(best,total)
    return best


def band_colours(image,geometry,row_y):
    """(grey-blue, gold) fractions of a row band. Teammate rows measured ~0.6 grey-blue;
    the own row is gold. Plain HUD frames never exceeded 0.21 grey-blue."""
    t=geometry.layout['tab'];x0,_=geometry.tab(t['row_band_x'][0],0);x1,_=geometry.tab(t['row_band_x'][1],0)
    _,y0=geometry.tab(0,row_y+t['row_band_dy'][0]);_,y1=geometry.tab(0,row_y+t['row_band_dy'][1])
    hsv=cv2.cvtColor(image[max(0,y0):y1,max(0,x0):x1],cv2.COLOR_BGR2HSV)
    if not hsv.size:return 0.,0.
    h,s,v=hsv[:,:,0],hsv[:,:,1],hsv[:,:,2]
    blue=(h>=85)&(h<=112)&(s>=25)&(s<=75)&(v>=40)&(v<=95)
    gold=(h>=10)&(h<=35)&(s>=45)&(v>=60)
    return float(blue.mean()),float(gold.mean())


def tile_kind(tile):
    """'perk' for a saturated diamond tile, 'empty' for the dark empty slot, None otherwise."""
    if tile is None:return None
    hsv=cv2.cvtColor(tile,cv2.COLOR_BGR2HSV);h,w=tile.shape[:2]
    yy,xx=np.ogrid[:h,:w];inside=(np.abs(xx-w/2)+np.abs(yy-h/2))<=min(h,w)*.38
    sat=hsv[:,:,1][inside];val=hsv[:,:,2][inside]
    if np.mean((sat>70)&(val>60))>.35:return 'perk'
    if np.mean(val<70)>.7 and np.mean(sat<60)>.7:return EMPTY
    return None


def is_scoreboard(image,layout):
    """Visual check that Tab is really open: perk diamonds where the layout says."""
    g=Geometry(layout,image.shape);t=layout['tab'];half=g.size(t['perk_half'])
    kinds=[tile_kind(crop(image,*g.tab(x,y),half)) for y in t['rows_y'] for x in t['perk_x']]
    bands=[band_colours(image,g,y)[0] for y in t['rows_y']]
    return (sum(k=='perk' for k in kinds)>=8 and sum(k is None for k in kinds)<=4
        and sum(b>=.45 for b in bands)>=2)


def scoreboard_evidence(image,layout):
    """What the scoreboard check saw, for the log: why a frame was rejected."""
    g=Geometry(layout,image.shape);t=layout['tab'];half=g.size(t['perk_half'])
    kinds=[tile_kind(crop(image,*g.tab(x,y),half)) for y in t['rows_y'] for x in t['perk_x']]
    return dict(perks=sum(k=='perk' for k in kinds),empty=sum(k==EMPTY for k in kinds),unknown=sum(k is None for k in kinds),
                bands=[round(band_colours(image,g,y)[0],2) for y in t['rows_y']],end_scoreboard=is_end_scoreboard(image))


def with_tab_fit(layout,s,ty):
    """A copy of the layout with the Tab table scaled about the screen centre (x) and moved by ty (y)."""
    t=copy.deepcopy(layout['tab']);x=lambda v:960+s*(v-960)
    t.update(rows_y=[s*y+ty for y in t['rows_y']],perk_x=[x(v) for v in t['perk_x']],perk_half=t['perk_half']*s,portrait_x=x(t['portrait_x']),
             portrait_dy=t['portrait_dy']*s,portrait_radius=t['portrait_radius']*s,row_band_x=[x(v) for v in t['row_band_x']],row_band_dy=[d*s for d in t['row_band_dy']])
    t['name']=dict(t['name'],x=[x(v) for v in t['name']['x']],dy=t['name']['dy']*s,half=t['name']['half']*s)
    return {**layout,'tab':t}


def tab_candidates(image,layout):
    """Best (s, ty) pairs: 16 perk tiles on a grid and row bands that are grey-blue or gold, never red."""
    t=layout['tab'];hsv=cv2.cvtColor(image[...,:3],cv2.COLOR_BGR2HSV);h,s,v=hsv[...,0],hsv[...,1],hsv[...,2]
    area=lambda mask:cv2.integral(mask.astype(np.uint8))
    perk=area((s>70)&(v>60));good=area(((h>=85)&(h<=112)&(s>=25)&(s<=75)&(v>=40)&(v<=95))|((h>=10)&(h<=35)&(s>=45)&(v>=60)));red=area(((h<8)|(h>172))&(s>140)&(v>60))
    k=image.shape[0]/layout['reference_height'];origin=(image.shape[1]-image.shape[0]*t['aspect'])/2
    xs=np.array(t['perk_x'],float);ys=np.array(t['rows_y'],float);band=np.array(t['row_band_x'],float);dy=t['row_band_dy']
    def box(integral,x0,x1,y0,y1):
        return (integral[y1,x1]-integral[y0,x1]-integral[y1,x0]+integral[y0,x0])/np.maximum(1,(x1-x0)*(y1-y0))
    def score(scale,shift):
        half=max(3,t['perk_half']*scale*k*.38);rows=(scale*ys+shift)*k;cols=origin+(960+scale*(xs-960))*k
        top=(rows+dy[0]*scale*k).astype(int);bottom=(rows+dy[1]*scale*k).astype(int)
        if rows.min()-half<0 or bottom.max()>=image.shape[0]:return -1.
        X,Y=np.meshgrid(cols,rows)
        tiles=box(perk,(X-half).astype(int),(X+half).astype(int),(Y-half).astype(int),(Y+half).astype(int))
        x0=(origin+(960+scale*(band[0]-960))*k);x1=(origin+(960+scale*(band[1]-960))*k)
        x0=np.full(4,int(x0));x1=np.full(4,int(x1))
        if box(red,x0,x1,top,bottom).max()>.25:return -1.  # A grid shifted one row down lands on the red killer row.
        return float((tiles>.3).sum()+tiles.mean()+2*(box(good,x0,x1,top,bottom)>=.3).sum())
    coarse=sorted(((score(a,b),a,b) for a in np.arange(.6,1.16,.03) for b in range(-180,181,6)),reverse=True)[:6]
    fine={(round(float(a+i*.01),2),b+j):0 for _,a,b in coarse for i in (-2,-1,0,1,2) for j in (-6,-4,-2,0,2,4,6)}
    return [p for _,p in sorted(((score(*p),p) for p in fine),reverse=True)[:12]]


def find_scoreboard(image,layout):
    """The layout fitted to this Tab scoreboard, whatever DBD's UI scale, or None."""
    if is_end_scoreboard(image):return None
    if is_scoreboard(image,layout):return layout
    for scale,shift in tab_candidates(image,layout):
        fitted=with_tab_fit(layout,scale,shift);g=Geometry(fitted,image.shape)
        if is_scoreboard(image,fitted) and sum(max(band_colours(image,g,y))>=.3 for y in fitted['tab']['rows_y'])>=3:
            return with_tab_fit(layout,scale,shift+refine_rows(image,fitted))
    return None


def refine_rows(image,fitted):
    """Vertical correction in reference pixels: where the saturated pixels fill the diamonds best."""
    g=Geometry(fitted,image.shape);t=fitted['tab'];half=g.size(t['perk_half']);hsv=cv2.cvtColor(image[...,:3],cv2.COLOR_BGR2HSV)
    mask=(hsv[...,1]>70)&(hsv[...,2]>60);yy,xx=np.ogrid[-half:half,-half:half];inside=np.abs(xx)+np.abs(yy)<=half*.9;best,shift=-1.,0
    for dy in range(-10,11):
        total=0.
        for y in t['rows_y']:
            for x in t['perk_x']:
                cx,cy=g.tab(x,y+dy);patch=mask[cy-half:cy+half,cx-half:cx+half]
                if patch.shape==inside.shape:total+=patch[inside].mean()
        if total>best+1e-6 or (abs(total-best)<=1e-6 and abs(dy)<abs(shift)):best,shift=total,dy
    return shift


def supported_ui_scale(scale):
    """The UI and HUD scales this app has native references for: 100 % and 90 %."""
    return abs(scale-1)<=.04 or abs(scale-.9)<=.04


def with_hud_fit(layout,s,t):
    """A copy of the layout with the HUD column scaled about its bottom-left anchor."""
    h=copy.deepcopy(layout['hud']);anchor=layout['hud'].get('anchor_y',1075)
    h.update(fit_scale=s,portrait_x=h['portrait_x']*s,portrait_y=[anchor-s*(anchor-y)+t for y in h['portrait_y']],portrait_radius=h['portrait_radius']*s,
             cluster_x=h['cluster_x']*s,right_x=h['right_x']*s,diamond=h['diamond']*s,diamond_gap=h['diamond_gap']*s)
    h['name']=dict(h['name'],x=[v*s for v in h['name']['x']],dy=h['name']['dy']*s,half=h['name']['half']*s)
    h['sizes']={k:(dict(v,diamond=v['diamond']*s,cluster_x=v['cluster_x']*s) if isinstance(v,dict) else v) for k,v in h['sizes'].items()}
    return {**layout,'hud':h}


def fit_hud(image,layout,band=False):
    """(scale, shift, confident): nicknames, the gaps between them and the dark ring round each portrait.

    `band`: the image is already only the left part of the client."""
    k=layout['reference_height']/image.shape[0];gray=cv2.cvtColor((image if band else image[:,:round(.3*image.shape[1])])[...,:3],cv2.COLOR_BGR2GRAY)
    if k!=1:gray=cv2.resize(gray,None,fx=k,fy=k,interpolation=cv2.INTER_AREA)
    h=layout['hud'];ys=np.array(h['portrait_y'],float);anchor=h.get('anchor_y',1075);n=h['name'];row=np.linspace(0,2*np.pi,48,endpoint=False)
    bright=cv2.integral((cv2.morphologyEx(gray,cv2.MORPH_TOPHAT,cv2.getStructuringElement(cv2.MORPH_RECT,(9,9)))>70).astype(np.uint8));g=gray.astype(np.float32)
    def box(y0,y1,x0,x1):return (bright[y1,x1]-bright[y0,x1]-bright[y1,x0]+bright[y0,x0])/np.maximum(1,(x1-x0)*(y1-y0))
    found=[]
    for s in np.round(np.arange(.6,1.06,.02),2):
        for t in range(-8,26,2):
            cy=anchor-s*(anchor-ys)+t;text=(cy+n['dy']*s).astype(int);half=max(3,int(10*s));x0,x1=int(n['x'][0]*s),int((n['x'][0]+150)*s)
            if text.min()-half<0 or text.max()+half>=g.shape[0]:continue
            rows=box(text-half,text+half,np.full(4,x0),np.full(4,x1));mid=((text[:-1]+text[1:])//2)
            gaps=box(mid-half,mid+half,np.full(3,x0),np.full(3,x1));px=int(h['portrait_x']*s);r=h['portrait_radius']*s;edge=[]
            for y in cy:
                xs=(px+r*1.25*np.cos(row)).astype(int);ring=(y+r*1.25*np.sin(row)).astype(int)
                if xs.min()<0 or ring.min()<0 or ring.max()>=g.shape[0]:break
                edge.append(g[int(y-r*.5):int(y+r*.5),int(px-r*.5):int(px+r*.5)].mean()-g[ring,xs].mean())
            if len(edge)==4:found.append((float(np.sort(rows)[1:].mean()-gaps.max()+np.sort(edge)[1:].mean()/400),float(s),t,float(np.sort(rows)[1:].mean())))
    if not found:return 1.,0,False
    best=max(found);at_one=max((f for f in found if f[1]==1.),default=None)
    if at_one and at_one[0]>=.9*best[0]:best=at_one  # A scale of 1 and a small shift is by far the usual case.
    return best[1],best[2],best[0]>=.13 and best[3]>=.09


def lobby_evidence(image,layout):
    """Fractions inside the lobby's READY/CANCEL button: red brush, claw logo, white text, dark banner."""
    spec=layout['lobby'];h,w=image.shape[:2];s=h/layout['reference_height']
    x0,y0,x1,y1=spec['box']
    box=image[round(y0*s):round(y1*s),max(0,round(w-(1920-x0)*s)):max(0,round(w-(1920-x1)*s))]
    if box.size==0:return None
    def cols(a,b):return slice(round(a*s),round(b*s))
    pixels=box.astype(np.int32);b,g,r=pixels[...,0],pixels[...,1],pixels[...,2]
    red=(r>100)&(r>g*1.8)&(r>b*1.8)
    hsv=cv2.cvtColor(box,cv2.COLOR_BGR2HSV)
    white=(hsv[...,2]>200)&(hsv[...,1]<60);dark=hsv[...,2]<70
    tx0,tx1,ty0,ty1,_=spec['text']
    return dict(red_brush=float(red[:,cols(*spec['red_brush'][:2])].mean()),logo=float(red[:,cols(*spec['logo'][:2])].mean()),
                text=float(white[cols(ty0,ty1),cols(tx0,tx1)].mean()),dark=float(dark[:,cols(*spec['dark'][:2])].mean()))


def is_lobby(image,layout):
    """The lobby or loadout screen: its READY/CANCEL button is on screen (any client language)."""
    evidence=lobby_evidence(image,layout)
    if evidence is None:return False
    spec=layout['lobby']
    return all(evidence[k]>=spec[k][-1] for k in ('red_brush','logo','text','dark'))


def is_end_scoreboard(image):
    """The end-game MATCH SCOREBOARD: white MATCH over the red SCOREBOARD title, top left."""
    h=image.shape[0];s=h/1080
    area=lambda x0,x1,y0,y1:image[round(y0*s):round(y1*s),round(x0*s):round(x1*s),:3]
    title=area(92,342,132,166).astype(np.int16);over=area(92,150,108,130)
    if not title.size or not over.size:return False
    red=((title[:,:,2]>170)&(title[:,:,1]<70)&(title[:,:,0]<70)).mean()
    return bool(red>=.2 and (over.min(axis=2)>190).mean()>=.1)


def hud_visible(image,layout):
    """Nicknames beside the HUD portraits: status icons cover a portrait, never all the names."""
    g=Geometry(layout,image.shape);h=layout['hud'];n=h['name'];found=0
    kernel=cv2.getStructuringElement(cv2.MORPH_RECT,(9,9))
    for y in h['portrait_y']:
        x0,cy=g.hud(n['x'][0],y+n['dy']);x1,_=g.hud(n['x'][1],y);half=g.size(n['half'])
        strip=image[max(0,cy-half):cy+half,x0:x1,:3]
        if not strip.size:continue
        bright=cv2.morphologyEx(cv2.cvtColor(strip,cv2.COLOR_BGR2GRAY),cv2.MORPH_TOPHAT,kernel)>70
        found+=bright.any(axis=0).mean()>=.05
    return found>=2


def scoreboard_signature(image,layout):
    """What each Tab perk tile looks like ('perk' / empty / unknown): equal on two frames once fully shown."""
    g=Geometry(layout,image.shape);t=layout['tab'];half=g.size(t['perk_half'])
    return tuple(tile_kind(crop(image,*g.tab(x,y),half)) for y in t['rows_y'] for x in t['perk_x'])


LOW_MARGIN=.05   # H3 (14.11): an accepted perk this close to the runner-up is saved for review


def save_unknown_tile(matcher,tile,result,accepted=False):
    """The unrecognised (or barely recognised) tile only, never a nickname, and its scores, once per image."""
    try:
        _save_tile(matcher,tile,result,accepted)
    except Exception:   # A diagnostic file must never disturb the overlay (live 14.11: a Path error broke every Tab read).
        import logging;logging.getLogger(__name__).exception('Saving a diagnostic perk tile failed; the read goes on')


def _save_tile(matcher,tile,result,accepted):
    folder=getattr(matcher,'unknown_dir',None)
    if folder is None:return
    if accepted:folder=Path(folder)/'close_calls'
    import hashlib
    from src.utils.images import write_image
    try:
        key=hashlib.sha256(tile.tobytes()).hexdigest()[:16];folder=Path(folder);path=folder/(key+'.png')
        if path.exists():return
        folder.mkdir(parents=True,exist_ok=True);write_image(path,tile)
        path.with_suffix('.json').write_text(json.dumps(dict(reason=result.reason,score=result.score,margin=result.margin,
            candidates=result.candidates,accepted_as=list(result.names) if accepted else None),ensure_ascii=False),encoding='utf-8')
    except (OSError,ValueError,TypeError):pass  # A diagnostic file must never disturb the overlay.


def read_tab(image,matcher,layout):
    g=Geometry(layout,image.shape);t=layout['tab'];half=g.size(t['perk_half']);rows=[]
    bands=[band_colours(image,g,y) for y in t['rows_y']]
    gold=[gold if blue<.2 else 0. for blue,gold in bands]
    own_index=int(np.argmax(gold)) if max(gold)>=.25 else None
    for index,y in enumerate(t['rows_y']):
        slots=[];tiles=[]
        for x in t['perk_x']:
            tile=crop(image,*g.tab(x,y),half);tiles.append(None if tile is None else tile.copy())
            kind=tile_kind(tile)
            if kind==EMPTY:slots.append(SlotResult(EMPTY,None,(),0.,0.,'empty_slot'));continue
            result=None
            for extra in (0,2,4):  # Small crop variations; thresholds are the matcher's own.
                candidate=crop(image,*g.tab(x,y),half-2+extra)
                result=matcher.identify(candidate) if candidate is not None else None
                if result is not None and result.icon_id:break
            if result is not None and not result.icon_id and tile is not None:save_unknown_tile(matcher,tile,result)
            elif result is not None and result.icon_id and result.margin<LOW_MARGIN and tile is not None:
                save_unknown_tile(matcher,tile,result,accepted=True)   # A close call: kept with its scores to find look-alikes.
            slots.append(result or SlotResult(None,None,(),0.,0.,'outside_frame'))
        px,py=g.tab(t['portrait_x'],y+t['portrait_dy'])
        rows.append(TabRow(index,index==own_index,tuple(slots),tuple(tiles),portrait_feature(image,(px,py),g.size(t['portrait_radius']))))
    return rows


def hud_portraits(image,layout):
    g=Geometry(layout,image.shape);h=layout['hud']
    return [portrait_feature(image,g.hud(h['portrait_x'],y),g.size(h['portrait_radius'])) for y in h['portrait_y']]


SLOTS=(1,2,3)  # HUD slots 2-4 on screen; slot 0 is the player.


def score_rows(rows,candidates):
    """Score every teammate row against every HUD slot.

    `candidates[slot]` is a list of HUD portrait features for that slot (recent
    frames); a pair's score is its best candidate. Returns (scores, best) keyed by
    (row index, slot); `best` is the candidate that produced the score.
    """
    scores={};best={}
    for r in rows:
        if r.own:continue
        for s in SLOTS:
            top,feature=-1.,None
            for candidate in candidates[s]:
                value=portrait_similarity(r.portrait,candidate)
                if value>top:top,feature=value,candidate
            scores[(r.index,s)]=top;best[(r.index,s)]=feature
    return scores,best


def direct_pairs(table,rows,slots,minimum,margin):
    """Pairs that reach `minimum` and win their row and column by `margin` among the
    rows and slots still free; repeated as pairs are taken."""
    rows=list(rows);slots=list(slots);result={}
    progress=True
    while progress and rows and slots:
        progress=False
        for r in rows:
            ranked=sorted(((table.get((r,s),-1.),s) for s in slots),reverse=True)
            top,slot=ranked[0];second=ranked[1][0] if len(ranked)>1 else -1
            column=sorted((table.get((o,slot),-1.) for o in rows),reverse=True)
            column_second=column[1] if len(column)>1 else -1
            if top>=minimum and top-second>=margin and top-column_second>=margin:
                result[slot]=r;rows.remove(r);slots.remove(slot);progress=True;break
    return result


def assign_scores(scores,row_ids,minimum=.65,margin=.08,floor=.35,names=None,name_minimum=.75,name_margin=.2):
    """Map row indexes to HUD slots from portrait scores and, when given, nickname scores.

    Portraits and nicknames are equal methods: a pair is taken when either is
    confident. Where they contradict each other, neither is used. Free rows and
    slots are then retried by portrait, and when exactly one row and one slot are
    left they are paired by elimination if their portrait score reaches `floor`.
    Returns ({slot: row}, {slot: 'portrait' | 'nickname' | 'portrait+nickname' | 'by_elimination'}).
    """
    row_ids=list(row_ids)
    portrait=direct_pairs(scores,row_ids,SLOTS,minimum,margin)
    named=direct_pairs(names,row_ids,SLOTS,name_minimum,name_margin) if names else {}
    blocked_rows=set();blocked_slots=set()
    for slot,row in named.items():
        clash=[(s,r) for s,r in portrait.items() if (s==slot)!=(r==row)]
        if clash:
            blocked_slots.add(slot);blocked_rows.add(row)
            for s,r in clash:blocked_slots.add(s);blocked_rows.add(r)
    result={};methods={}
    for source,label in ((portrait,'portrait'),(named,'nickname')):
        for slot,row in source.items():
            if slot in blocked_slots or row in blocked_rows:continue
            methods[slot]='portrait+nickname' if slot in result else label;result[slot]=row
    rows=[r for r in row_ids if r not in result.values() and r not in blocked_rows]
    slots=[s for s in SLOTS if s not in result and s not in blocked_slots]
    for slot,row in direct_pairs(scores,rows,slots,minimum,margin).items():
        result[slot]=row;methods[slot]='portrait';rows.remove(row);slots.remove(slot)
    if result and len(rows)==1 and len(slots)==1 and not blocked_rows and scores.get((rows[0],slots[0]),-1.)>=floor:
        result[slots[0]]=rows[0];methods[slots[0]]='by_elimination'
    return result,methods


def assign(rows,portraits,minimum=.65,margin=.08,methods=None,names=None):
    """Map teammate Tab rows to HUD slots 2-4 (indexes 1-3) by portrait only.

    `portraits[slot]` is one HUD feature or a list of recent ones. Returns
    ({slot: TabRow}, scores); `methods`, when given, receives how each slot was paired.
    """
    candidates=[p if isinstance(p,list) else [p] for p in portraits]
    scores,_=score_rows(rows,candidates)
    by_index={r.index:r for r in rows if not r.own}
    pairs,how=assign_scores(scores,list(by_index),minimum,margin,names=names)
    if methods is not None:methods.update(how)
    return {slot:by_index[r] for slot,r in pairs.items()},scores
