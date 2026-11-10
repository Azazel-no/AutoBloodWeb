"""Node framing evidence independent of item identity and absolute screen position."""
import cv2
import numpy as np

_ANGLES=np.arange(96)*2*np.pi/96
_COS=np.cos(_ANGLES);_SIN=np.sin(_ANGLES);_RADII=np.arange(.72,1.36,.04)


def tile_polygon(hsv,x,y,r):
    patch=hsv[y-r:y+r+1,x-r:x+r+1]
    if patch.shape[:2]!=(2*r+1,2*r+1):return False
    yy,xx=np.ogrid[-r:r+1,-r:r+1]
    mask=((patch[:,:,1]>65)&(patch[:,:,2]>25)&(xx*xx+yy*yy<(r*.83)**2)).astype(np.uint8)
    points=cv2.findNonZero(mask)
    if points is None:return False
    hull=cv2.convexHull(points);poly=cv2.approxPolyDP(hull,.045*cv2.arcLength(hull,True),True)[:,0]
    if len(poly) not in (4,6) or cv2.contourArea(hull)<r*r*.55:return False
    dist=np.linalg.norm(poly-[r,r],axis=1)
    return bool(dist.min()>r*.38 and dist.max()/max(1,dist.min())<1.5 and np.linalg.norm(poly.mean(axis=0)-[r,r])<r*.2)


def framing(gray,x,y,r):
    radii=_RADII*r
    offsets=np.array([-4.,0.,4.])[:,None,None]
    distances=radii[None,:,None]+offsets
    xs=np.clip(np.rint(x+distances*_COS).astype(int),0,gray.shape[1]-1)
    ys=np.clip(np.rint(y+distances*_SIN).astype(int),0,gray.shape[0]-1)
    values=gray[ys,xs].astype(np.float32)
    valley=(values[0]-values[1]>9)&(values[2]-values[1]>9)
    edge=np.abs(values[2]-values[0])>15
    dark=np.mean(values[1]<35,axis=1)
    outlined=values[1]<35
    opposite=np.mean(outlined[:,:48]&outlined[:,48:],axis=1)
    return dict(valley=float(np.max(np.mean(valley,axis=1))),regular=float(np.max(np.mean(edge,axis=1))),dark_ring=float(dark.max()),opposite_dark=float(opposite.max()))


def validate_candidates(gray,circles,center,edges,previous=(),hsv=None,confirmed_graph=False,only_last=False,framing_cache=None):
    """Edges support framing, but a missing edge cannot reject a strong frame.

    Independent of glyph whiteness, library matches, hue, and screen height.
    Disconnected clusters do not establish a web just by linking to each other.
    Motion only rejects weak candidates: real animated rims remain admissible.
    """
    graph={i:set() for i in range(len(circles)+1)}
    for a,b in edges:graph[a].add(b);graph[b].add(a)
    connected={0};pending=[0]
    while pending:
        for ident in graph[pending.pop()]-connected:
            connected.add(ident);pending.append(ident)
    accepted=[];audit=[]
    cache=framing_cache if framing_cache is not None else {}
    def current_frame(x,y,r):
        key=(x,y,r)
        if key not in cache:cache[key]=framing(gray,x,y,r)
        return cache[key]
    def strong(e):
        return ((e['valley']>=.42 and e['regular']>=.65) or
                (e['valley']>=.32 and e['regular']>=.7 and e['dark_ring']>=.22) or
                (e['valley']>=.25 and e['regular']>=.8) or
                (e['dark_ring']>=.4 and e['regular']>=.85) or
                (e['dark_ring']>=.6 and e['regular']>=.65))
    def structured(e,linked,polygon):
        return linked and e['regular']>=.35 and (e['dark_ring']>=.25 and e['opposite_dark']>=.04 or
                                                e['valley']>=.28 and (polygon or e['dark_ring']>=.1) or
                                                (polygon and e['valley']>=.25 and e['regular']>=.8))
    for ident,(x,y,r) in enumerate(circles,1):
        if only_last and ident!=len(circles):continue
        evidence=current_frame(x,y,r)
        polygon=tile_polygon(hsv,x,y,r) if hsv is not None else False
        linked=ident in connected
        # Texture on a bright sleeve can have radial valleys and apparent
        # links to real nodes. Without a tile polygon, the secondary graph
        # must still observe part of the game's dark circular outline.
        valid=strong(evidence) or structured(evidence,linked,polygon) or (confirmed_graph and linked and evidence['regular']>=.35 and (polygon or evidence['valley']>=.25 and evidence['regular']>=.6 and evidence['dark_ring']>=.08))
        origin=(x,y)
        if not valid and linked:
            # A partial/offset Hough circle can miss the real lower frame.
            # Refine locally using framing only, never using item templates.
            proposals=[]
            for dx in (-.36,-.18,0,.18,.36):
                for dy in (-.36,-.18,0,.18,.36):
                    a,b=round(x+dx*r),round(y+dy*r)
                    e=current_frame(a,b,r)
                    if strong(e):proposals.append((.7*e['valley']+.15*e['regular']+.15*e['dark_ring'],a,b,r,e))
            if proposals:
                _,x,y,r,evidence=max(proposals,key=lambda p:p[0]);valid=True
        stable=any(np.hypot(x-a,y-b)<max(6,r*.2) for a,b,*_ in previous)
        # A previously analysed web is supporting evidence only. It never
        # resurrects a candidate lacking current framing and a current edge.
        if previous and not stable and not strong(evidence) and not linked:valid=False
        audit.append(dict(x=int(x),y=int(y),radius=int(r),accepted=valid,connected=linked,tile_polygon=polygon,
                          origin=list(map(int,origin)),stable=stable,**evidence,reason='game_frame_and_structure' if valid else 'unproven_node_frame'))
        if valid and not any(np.hypot(x-a,y-b)<min(r,c)*1.1 for a,b,c in accepted):accepted.append((x,y,r))
    # Genuine tiles cannot overlap the neighboring tile's circular boundary.
    # A nearby pumpkin can inherit an apparent edge through the real node.
    scores={(a['x'],a['y'],a['radius']):(a['dark_ring'],a['valley']) for a in audit}
    ranked=sorted(accepted,key=lambda c:scores[c],reverse=True)
    distinct=[]
    for x,y,r in ranked:
        if any(np.hypot(x-a,y-b)<(r+c)*.9 for a,b,c in distinct):
            for item in audit:
                if (item['x'],item['y'],item['radius'])==(x,y,r):item.update(accepted=False,reason='overlapping_node_frame')
        else:distinct.append((x,y,r))
    return sorted(distinct,key=lambda c:(c[1],c[0])),audit
