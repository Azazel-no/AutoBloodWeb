"""Native preview contracts and per-node outcomes; no mouse/capture imports."""
import copy
import math
import time
from dataclasses import dataclass, field, asdict
import cv2
import numpy as np
from src.core.models import NodeState, NodeType, ScreenState
from src.core.geometry import purchasable
from src.core.persistent_route import map_key
from src.core.priority_targets import PERK_TARGET, perk_eligible


@dataclass(frozen=True)
class PriceRegion:
    box: tuple[int,int,int,int]
    calibration_sha256: str
    # This means a reviewed *native total-price label*, not an arbitrary number
    # or the HUD balance. There is no automatic tooltip calibration yet.
    reviewed_total_label: bool = False
    coordinate_space: str = 'roi'


@dataclass
class PreviewObservation:
    map_id: str
    stamp: float
    ordered_nodes: list[int]
    total_bp: int | None
    price_provenance: str | None
    edges: list[tuple[int,int]]
    reason: str
    hover_point: tuple[int,int] | None = None


def priority_rank(node, priorities):
    ident=PERK_TARGET if perk_eligible(node) and PERK_TARGET in priorities else node.icon_id
    return priorities.index(ident) if ident in priorities else len(priorities)


def red_preview_edges(frame, result, clean_frame=None):
    """Narrow continuous red highlights, excluding previously purchased links.

    Preview frames are probe-only. Highlight colour is never purchased-state
    evidence. Straight line sampling uses the current verified node geometry.
    """
    hsv=cv2.cvtColor(frame.image,cv2.COLOR_BGR2HSV);found=[]
    before=cv2.cvtColor(clean_frame.image,cv2.COLOR_BGR2HSV) if clean_frame is not None else None
    verified={tuple(sorted(e)) for e in result.edges}
    owned={n.id for n in result.nodes if n.state in (NodeState.CENTER,NodeState.PURCHASED)}
    for i,a in enumerate(result.nodes):
        for b in result.nodes[i+1:]:
            if a.id in owned and b.id in owned:continue
            if a.state in (NodeState.ABSORBED,NodeState.UNCERTAIN) or b.state in (NodeState.ABSORBED,NodeState.UNCERTAIN):continue
            dist=math.hypot(a.x-b.x,a.y-b.y);radius=(a.radius+b.radius)/2
            if not radius*2.1<dist<radius*6:continue
            delta=np.array([b.x-a.x,b.y-a.y],float);normal=np.array([-delta[1],delta[0]])/dist
            if any(0<(position:=np.dot(np.array([n.x-a.x,n.y-a.y]),delta)/(dist*dist))<1 and
                   np.linalg.norm(np.array([n.x-a.x,n.y-a.y])-position*delta)<n.radius*.9
                   for n in result.nodes if n.id not in (a.id,b.id)):continue
            t=np.linspace((a.radius+5)/dist,1-(b.radius+5)/dist,30)
            if t[0]>=t[-1]:continue
            points=np.array([a.x,a.y])+t[:,None]*delta
            def fraction(offset,source=hsv,saturation=150,value=75):
                p=np.rint(points+offset*normal).astype(int)
                if np.any(p<0) or np.any(p[:,0]>=hsv.shape[1]) or np.any(p[:,1]>=hsv.shape[0]):return 0.
                q=source[p[:,1],p[:,0]]
                return float(np.mean(((q[:,0]<10)|(q[:,0]>172))&(q[:,1]>saturation)&(q[:,2]>value)))
            def newly_red(offset):
                p=np.rint(points+offset*normal).astype(int)
                if np.any(p<0) or np.any(p[:,0]>=hsv.shape[1]) or np.any(p[:,1]>=hsv.shape[0]):return 1.
                def mask(source):
                    q=source[p[:,1],p[:,0]]
                    return ((q[:,0]<10)|(q[:,0]>172))&(q[:,1]>80)&(q[:,2]>35)
                return float(np.mean(mask(hsv)&~mask(before)))
            strong=fraction(0)>.90 and max(fraction(radius*.5),fraction(-radius*.5))<.25
            # Actual native preview dims its red lines below the purchased-red
            # thresholds. Accept that only on a previously verified edge with
            # a fresh clean comparator: red must newly appear continuously,
            # and both sides must remain clear. This never classifies ownership.
            changed=(before is not None and tuple(sorted((a.id,b.id))) in verified
                and fraction(0,saturation=80,value=35)>.90
                and fraction(0,source=before,saturation=80,value=35)<.25
                and max(newly_red(radius*.5),newly_red(-radius*.5))<.25)
            if strong or changed:
                found.append((a.id,b.id))
    return found


# Standard Bloodweb prices by rim colour. A mismatch only means "no chain".
STANDARD_PRICES={'brown':2000,'green':2500,'blue':3250,'purple':4000,'pink':5000}


def certified_graph_path(clean, target_id, total):
    """The game's item card often hides the red path, so prove it another way.

    Accepts only one shortest path from the owned nodes to the target whose
    standard prices add up exactly to the total the game displays.
    """
    nodes={n.id:n for n in clean.nodes}
    if target_id not in nodes:return []
    graph={i:[] for i in nodes}
    for a,b in clean.edges:
        if a in nodes and b in nodes:graph[a].append(b);graph[b].append(a)
    owned=[n.id for n in clean.nodes if n.state in (NodeState.CENTER,NodeState.PURCHASED)]
    level=[[i] for i in owned];seen=set(owned);found=[]
    while level and not found:
        following=[];reached=set()
        for path in level:
            for other in graph[path[-1]]:
                if other in seen or nodes[other].state not in (NodeState.AVAILABLE,NodeState.LOCKED):continue
                reached.add(other);following.append(path+[other])
        found=[p[1:] for p in following if p[-1]==target_id]
        seen|=reached;level=following
    if len(found)!=1 or len(found[0])<2:return []
    path=found[0]
    if any(nodes[i].type==NodeType.PERK or nodes[i].rarity not in STANDARD_PRICES for i in path):return []
    return path if sum(STANDARD_PRICES[nodes[i].rarity] for i in path)==total else []


def read_preview(frame, clean, target_id, map_id, reader=None, price_region=None, clean_frame=None):
    if not frame.probe_only or clean.screen_state!=ScreenState.BLOODWEB:
        return PreviewObservation(map_id,frame.captured_at,[],None,None,[],'probe_only_required')
    if (clean_frame is None or clean_frame.window.signature!=frame.window.signature or clean_frame.roi!=frame.roi
            or clean_frame.image.shape!=frame.image.shape or clean.level!=frame.level
            or clean_frame.captured_at>=frame.captured_at or frame.captured_at-clean_frame.captured_at>5):
        return PreviewObservation(map_id,frame.captured_at,[],None,None,[],'preview_context_unproven')
    edges=red_preview_edges(frame,clean,clean_frame);owned={n.id for n in clean.nodes if n.state in (NodeState.CENTER,NodeState.PURCHASED)}
    graph={n.id:[] for n in clean.nodes}
    for a,b in edges:graph[a].append(b);graph[b].append(a)
    paths=[]
    def walk(node,path):
        if node==target_id:paths.append(path);return
        if len(path)>len(graph):return
        for other in graph[node]:
            if other not in path and other not in owned:walk(other,path+[other])
    for root in owned:walk(root,[root])
    path=[];reason='preview_path_unknown_or_ambiguous'
    if len(paths)==1:
        path=paths[0][1:]
        used={tuple(sorted(e)) for e in zip(paths[0],paths[0][1:])}
        if used!={tuple(sorted(e)) for e in edges}:path=[]
        else:reason='preview_price_unknown'
    total=None;provenance=None
    if reader and price_region and price_region.reviewed_total_label is True and len(price_region.calibration_sha256)==64:
        x,y,w,h=price_region.box
        pixels=frame.raw_image if price_region.coordinate_space=='client' else frame.image if price_region.coordinate_space=='roi' else None
        if pixels is not None and x>=0 and y>=0 and w>0 and h>0 and x+w<=pixels.shape[1] and y+h<=pixels.shape[0]:
            total=reader.decode(pixels[y:y+h,x:x+w])
            if type(total) is int and total>0:
                provenance=price_region.calibration_sha256
                if not path and reason=='preview_path_unknown_or_ambiguous':path=certified_graph_path(clean,target_id,total)
                if path:reason='observed_preview'
            else:total=None
    hover=None
    pointer=getattr(frame,'visibility',None) or {}
    target=next((n for n in clean.nodes if n.id==target_id),None)
    if target is not None and path:
        expected=clean_frame.to_screen(target.x,target.y)
        if (pointer.get('cursor_before')==list(expected) and pointer.get('cursor_after')==list(expected)):
            hover=expected
    return PreviewObservation(map_id,frame.captured_at,path,total,provenance,edges,reason,hover)


@dataclass
class ChainPlan:
    map_id: str
    map_revision: int
    available_start: int
    terminal: int
    ordered_nodes: list[int]
    game_preview: list[int] = field(default_factory=list)
    total_bp: int | None = None
    priorities: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    preview_stamps: list[float] = field(default_factory=list)
    physical_points: list[tuple[int,int]] = field(default_factory=list)
    price_provenance: str | None = None
    hover_points: list[tuple[int,int]] = field(default_factory=list)

    @property
    def expected_items(self):return len(self.ordered_nodes)

    @property
    def intermediates(self):return self.ordered_nodes[:-1]

    def summary(self):
        return dict(asdict(self),expected_items=self.expected_items,intermediates=self.intermediates,
                    theoretical_saved_commands=max(0,self.expected_items-1))

    def bind_preview(self, observations):
        self.blockers=[b for b in self.blockers if b not in ('native_preview_required','preview_mismatch','price_unknown','preview_not_independent')]
        self.preview_stamps=[];self.game_preview=[];self.total_bp=None;self.price_provenance=None;self.hover_points=[]
        if len(observations)!=2 or observations[0].stamp>=observations[1].stamp:
            self.blockers.append('preview_not_independent');return
        if any(o.map_id!=self.map_id or o.ordered_nodes!=self.ordered_nodes or o.reason!='observed_preview' for o in observations):
            self.blockers.append('preview_mismatch');return
        a,b=observations
        if (type(a.total_bp) is not int or a.total_bp<=0 or a.total_bp!=b.total_bp
                or not a.price_provenance or a.price_provenance!=b.price_provenance):
            self.blockers.append('price_unknown');return
        self.game_preview=list(a.ordered_nodes);self.preview_stamps=[a.stamp,b.stamp]
        self.total_bp=a.total_bp;self.price_provenance=a.price_provenance
        if self.physical_points and a.hover_point==b.hover_point==self.physical_points[-1]:
            self.hover_points=[a.hover_point,b.hover_point]


def native_candidate_route(result,priorities,mode,max_nodes=3):
    """Keep priority decisions; an actual filler decision may probe a longer path.

    This returns a hypothesis only. The native preview and native total price
    still have to certify the entire path before the existing chain executor.
    """
    from src.core.path_planner import plan_route,RoutePlan
    route=plan_route(result,priorities,mode)
    if route.reason!='filler' or len(route.route)>1:return route
    nodes={n.id:n for n in result.nodes};graph={i:[] for i in nodes}
    for a,b in result.edges:
        if a in nodes and b in nodes:graph[a].append(b);graph[b].append(a)
    owned={n.id for n in result.nodes if n.state in (NodeState.CENTER,NodeState.PURCHASED)}
    queue=[(root,[]) for root in sorted(owned)];seen=set(owned);paths=[]
    for root,path in queue:
        if len(path)>=max_nodes or path and nodes[root].type==NodeType.PERK:continue
        for other in sorted(graph[root]):
            if other in seen or nodes[other].state not in (NodeState.AVAILABLE,NodeState.LOCKED):continue
            seen.add(other);following=path+[other]
            queue.append((other,following))
            if len(following)>=2 and purchasable(nodes[following[0]],result.nodes,result.edges):paths.append(following)
    if not paths:return route
    path=min(paths,key=lambda p:(-len(p),p))
    return RoutePlan(path[0],path,nodes[path[-1]].icon_id,'fast_level_native_preview_candidate')


def propose_chain(frame,result,route,priorities,map_id,revision=1,max_nodes=3):
    """Only a contiguous graph hypothesis. Never fabricated native preview."""
    ids=list(route.route[:max_nodes]);nodes={n.id:n for n in result.nodes}
    if not ids:return None
    edges={tuple(sorted(e)) for e in result.edges};blockers=['native_preview_required','price_unknown']
    if not purchasable(nodes[ids[0]],result.nodes,result.edges):blockers.append('start_not_directly_available')
    if any(tuple(sorted((a,b))) not in edges for a,b in zip(ids,ids[1:])):blockers.append('graph_route_not_a_chain')
    if len(ids)<2:blockers.append('no_input_saving')
    if any(nodes[i].state not in (NodeState.AVAILABLE,NodeState.LOCKED) for i in ids):blockers.append('unsafe_prestate')
    rank=priority_rank(nodes[ids[-1]],priorities)
    higher=[n.id for n in result.nodes if n.id not in ids and n.state in (NodeState.AVAILABLE,NodeState.LOCKED) and priority_rank(n,priorities)<rank]
    if higher:blockers.append('higher_priority_outside_chain')
    risks=[]
    if result.level is None or result.level>=10:
        risks.append('entity_interruption_unverified')
        if len(ids)<len(route.route) and priorities:blockers.append('priority_path_truncated_under_entity_risk')
    if any(nodes[i].type==NodeType.PERK for i in ids[:-1]):blockers.append('intermediate_perk_early_entity')
    return ChainPlan(map_id,revision,ids[0],ids[-1],ids,priorities=list(priorities),risks=risks,blockers=blockers,
                     physical_points=[frame.to_screen(nodes[i].x,nodes[i].y) for i in ids])


class ChainEvidence:
    """Two distinct clean red frames per node. Missing/absorbed != purchased."""
    def __init__(self,frame,result,plan):
        self.signature=frame.window.signature;self.level=result.level;self.stamp=frame.captured_at
        self.nodes=copy.deepcopy([next(n for n in result.nodes if n.id==i) for i in plan.ordered_nodes])
        self.points=list(plan.physical_points);self.red={n.id:0 for n in self.nodes};self.absorbed={n.id:0 for n in self.nodes}
        self.confirmed=set();self.entity=set();self.reason='pending'
        self.before_owned=[frame.to_screen(n.x,n.y) for n in result.nodes if n.state in (NodeState.CENTER,NodeState.PURCHASED)]
        self.before_owned_regions=[(frame.to_screen(n.x,n.y),n.radius) for n in result.nodes if n.state in (NodeState.CENTER,NodeState.PURCHASED)]
        self.before_owned_identity=[(frame.to_screen(n.x,n.y),n.radius,n.icon_id) for n in result.nodes if n.state in (NodeState.CENTER,NodeState.PURCHASED)]
        self.unexpected=[]
        from src.core.scene_memory import record_scene
        self.geometry=record_scene(frame,result,[],dict(chain=dict(physical_points=self.points)))
        self.ring_observations=[]

    def observe(self,frame,result):
        if frame.captured_at<=self.stamp or frame.probe_only:return False
        self.stamp=frame.captured_at
        if frame.window.signature!=self.signature:raise ValueError('chain_window_changed')
        if result.screen_state!=ScreenState.BLOODWEB or result.level!=self.level:
            self.reason='level_or_screen_changed_unconfirmed';return False
        # Preserve strict point matching; a second outcome-only certificate
        # handles a smaller red rim wholly inside the old click footprint.
        from src.core.scene_memory import chain_matching_node,chain_ring_certificate,chain_unexpected_points
        self.unexpected=chain_unexpected_points(self.geometry,frame,result)
        if self.unexpected:
            self.reason='chain_unexpected_purchase';raise ValueError(self.reason)
        for original,point in zip(self.nodes,self.points):
            node=chain_matching_node(self.geometry,frame,result,point)
            certificate=chain_ring_certificate(self.geometry,frame,node,point) if node else None
            if certificate:self.ring_observations.append(certificate)
            valid=bool(node and not(original.icon_id and node.icon_id and original.icon_id!=node.icon_id))
            red=valid and node.state==NodeState.PURCHASED and node.state_confidence>=.8
            absorbed=valid and node.state==NodeState.ABSORBED and node.state_confidence>=.75
            if original.id in self.confirmed and not red:
                self.reason='chain_confirmed_state_contradiction';raise ValueError(self.reason)
            self.red[original.id]=self.red[original.id]+1 if red else 0
            self.absorbed[original.id]=self.absorbed[original.id]+1 if absorbed else 0
            if self.red[original.id]>=2:self.confirmed.add(original.id)
            if self.absorbed[original.id]>=2:self.entity.add(original.id)
        self.reason='complete' if len(self.confirmed)==len(self.nodes) else 'partial_or_pending'
        return self.reason=='complete'

    def summary(self):
        return dict(confirmed=sorted(self.confirmed),absorbed=sorted(self.entity),
            unconfirmed=[n.id for n in self.nodes if n.id not in self.confirmed and n.id not in self.entity],
            red_observations=dict(self.red),unexpected_purchases=list(self.unexpected),reason=self.reason,automatic_retry=False,cancellation_verified=False,
            contained_ring_observations=list(self.ring_observations))
