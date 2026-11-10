"""Attempted points survive a new Start; only fresh evidence resolves a scene."""
import math
from dataclasses import asdict
from src.core.models import NodeState


def record_scene(frame,result,attempted,pending=None,previous=None):
    scene=dict(window=asdict(frame.window),attempted=[list(p) for p in attempted],level=result.level,level_confidence=result.level_confidence,
        captured_at=frame.captured_at,bp_before=frame.bloodpoints,
        nodes=[dict(point=list(frame.to_screen(n.x,n.y)),radius=n.radius,state=n.state.value,icon=n.icon_id) for n in result.nodes],pending=pending)
    scene['transition_glyphs']=transition_glyphs(previous,frame,result)
    if pending:
        from src.core.action_outcomes import operation_id
        pending['operation_id']=operation_id(scene)
    return scene


def transition_glyphs(previous,frame,result):
    """Historical accepted glyphs for exact-next-header comparison, never routing."""
    import copy
    if (not getattr(frame,'live',False) or getattr(frame,'probe_only',True) or not frame.window.foreground
            or type(result.level) is not int or result.level_confidence<.95
            or result.details.get('confirmation_only')):return []
    prior=[]
    if (previous and same_window(previous,frame) and previous.get('level')==result.level
            and previous.get('level_confidence',0)>=.95):
        prior=previous.get('transition_glyphs',[])
    rows=[]
    for node in result.nodes:
        if node.state==NodeState.CENTER or node.geometry_confidence<.65:continue
        point=list(frame.to_screen(node.x,node.y))
        matches=[g for g in prior if math.dist(g['point'],point)<max(8,g['radius']*.2)]
        if len(matches)>1:continue
        old=copy.deepcopy(matches[0]) if matches else None
        accepted=(node.state!=NodeState.ABSORBED and node.icon_id and node.confidence>=.86
            and node.contour_score>=.68 and node.icon_margin>=.035)
        if old and accepted and old.get('icon')!=node.icon_id:old['conflicted']=True
        if old:
            if sum(math.dist(g['point'],frame.to_screen(n.x,n.y))<max(8,g['radius']*.2)
                   for n in result.nodes for g in [old])==1:rows.append(old)
        elif accepted:
            rows.append(dict(point=point,radius=node.radius,icon=node.icon_id,score=node.confidence,
                contour=node.contour_score,margin=node.icon_margin,geometry=node.geometry_confidence,
                stamp=frame.captured_at,conflicted=False))
    return rows


def same_window(record,frame):
    current=asdict(frame.window);old=record.get('window',{})
    return all(old.get(k)==current[k] for k in ('hwnd','pid','client','monitor','dpi'))


def terminal_observation(record,frame,result):
    """Retain observed old-map states for transition proof, never purchase credit."""
    import copy
    from src.core.models import ScreenState
    pending=(record or {}).get('pending') or {}
    if (not pending or any(pending.get(k) for k in ('chain','batch','automatic','chain_blocked_reason'))
            or not frame.live or frame.probe_only or not frame.window.foreground
            or not same_window(record,frame) or result.screen_state!=ScreenState.BLOODWEB
            or result.details.get('confirmation_only') or type(record.get('level')) is not int
            or result.level!=record['level'] or result.level_confidence<.95
            or record.get('level_confidence',0)<.95 or frame.captured_at<=record.get('captured_at',0)):
        return None
    old=record.get('nodes',[]);updates={}
    for node in result.nodes:
        point=frame.to_screen(node.x,node.y)
        matches=[i for i,n in enumerate(old) if math.dist(n['point'],point)<max(8,n['radius']*.2)]
        if not matches:
            if node.state in (NodeState.PURCHASED,NodeState.ABSORBED):return None
            continue  # An unmatched unowned proposal cannot increase ownership.
        if len(matches)!=1 or matches[0] in updates:return None
        previous=old[matches[0]]
        if previous.get('icon') and node.icon_id and previous['icon']!=node.icon_id:return None
        if previous['state'] in (NodeState.PURCHASED,NodeState.ABSORBED) and node.state!=previous['state']:return None
        if node.geometry_confidence<.65 or node.state_confidence<(.75 if node.state==NodeState.ABSORBED else .8):return None
        updates[matches[0]]=node.state.value
    targets=[i for i,n in enumerate(old) if math.dist(n['point'],pending['point'])<5]
    if (len(targets)!=1 or updates.get(targets[0])!=NodeState.PURCHASED
            or len(updates)<max(len(old),len(result.nodes))*.7):return None
    observed=copy.deepcopy(record);observed.pop('transition_observation',None)
    for index,state in updates.items():observed['nodes'][index]['state']=state
    observed['transition_witness']=dict(stamp=frame.captured_at,analysis_id=result.analysis_id,purchase_confirmed=False)
    return observed


def transition_view(record):
    observed=record.get('transition_observation')
    if (isinstance(observed,dict) and observed.get('window')==record.get('window')
            and observed.get('level')==record.get('level') and observed.get('pending')==record.get('pending')
            and observed.get('transition_witness',{}).get('purchase_confirmed') is False
            and observed['transition_witness'].get('stamp',0)>record.get('captured_at',0)):
        return observed
    return record


def chain_ring_certificate(record,frame,node,point):
    """A shrinking red rim contained in its original validated click region.

    This is outcome-only geometry, not a larger general matching tolerance or
    permission to click. Every other original footprint must remain disjoint.
    """
    old=[n for n in record.get('nodes',[]) if math.dist(n['point'],point)<5]
    if len(old)!=1:return None
    old=old[0];radius=old['radius'];current=frame.to_screen(node.x,node.y)
    scale=min(frame.roi.width/frame.image.shape[1],frame.roi.height/frame.image.shape[0])
    fresh_radius=node.radius*scale;distance=math.dist(current,old['point'])
    if (old['state'] not in (NodeState.AVAILABLE,NodeState.LOCKED) or
            node.state!=NodeState.PURCHASED or node.state_confidence<.9 or node.geometry_confidence<.8 or
            old.get('icon') and node.icon_id and old['icon']!=node.icon_id or
            not .5*radius<=fresh_radius<=.8*radius or
            not distance<radius*.35 or distance+fresh_radius>radius):return None
    if any(other is not old and math.dist(current,other['point'])<=fresh_radius+other['radius']
           for other in record.get('nodes',[])):return None
    return dict(kind='contained_shrinking_red_ring',before_point=list(old['point']),
        before_radius=radius,point=list(current),radius=fresh_radius,stamp=frame.captured_at)


def chain_matching_node(record,frame,result,point):
    old=[n for n in record.get('nodes',[]) if math.dist(n['point'],point)<5]
    if len(old)!=1:return None
    found=[n for n in result.nodes if math.dist(frame.to_screen(n.x,n.y),point)<max(5,old[0]['radius']*.2)]
    if found:return found[0] if len(found)==1 else None
    found=[n for n in result.nodes if chain_ring_certificate(record,frame,n,point)]
    return found[0] if len(found)==1 else None


def chain_unexpected_points(record,frame,result):
    """Current red nodes must belong to prior owned nodes or the actual path."""
    pending=record.get('pending') or {};points=(pending.get('chain') or {}).get('physical_points',[])
    expected={id(node) for point in points if (node:=chain_matching_node(record,frame,result,point)) is not None}
    def owned(node):
        matches=[n for n in record.get('nodes',[]) if n['state'] in (NodeState.CENTER,NodeState.PURCHASED)
            and math.dist(frame.to_screen(node.x,node.y),n['point'])<max(8,n['radius']*.2)]
        return len(matches)==1 and not(matches[0].get('icon') and node.icon_id and matches[0]['icon']!=node.icon_id)
    return [list(frame.to_screen(n.x,n.y)) for n in result.nodes if n.state==NodeState.PURCHASED
            and id(n) not in expected and not owned(n)]


def chain_state_signature(record,frame,result):
    """Every pending chain point must be freshly resolved; the tip alone fails."""
    pending=record.get('pending') or {}
    if pending.get('chain_blocked_reason'):return None
    chain=pending.get('chain') or pending.get('batch',{})
    points=chain.get('physical_points',[])
    if not (1 if pending.get('batch') else 2)<=len(points)<=5:return None
    observations=[]
    for point in points:
        old=[n for n in record.get('nodes',[]) if math.dist(n['point'],point)<5]
        tolerance=(max(8,old[0]['radius']*.4) if pending.get('batch') else max(5,old[0]['radius']*.2)) if len(old)==1 else 5
        found=([node] if (node:=chain_matching_node(record,frame,result,point)) is not None else []) if pending.get('chain') else [n for n in result.nodes if math.dist(frame.to_screen(n.x,n.y),point)<tolerance]
        if len(old)!=1 or len(found)!=1:return None
        node=found[0];ident=old[0].get('icon')
        if ident and node.icon_id and ident!=node.icon_id:return None
        if node.state==NodeState.PURCHASED and node.state_confidence>=.8:observations.append('purchased')
        elif node.state==NodeState.ABSORBED and node.state_confidence>=.75:observations.append('absorbed')
        elif (pending.get('batch',{}).get('deferred_verification') is True and
                type(pending['batch'].get('inputs_completed_at')) in (int,float) and
                frame.captured_at-pending['batch']['inputs_completed_at']>=1.5 and
                node.state in (NodeState.AVAILABLE,NodeState.LOCKED) and node.state_confidence>=.8 and node.geometry_confidence>=.7):
            from src.core.geometry import purchasable
            if node.state==NodeState.AVAILABLE and not purchasable(node,result.nodes,result.edges):return None
            observations.append(node.state.value)
        else:return None
    return tuple(observations)


def layout_changed(record,frame,result):
    if not same_window(record,frame):return False
    old=[n for n in record.get('nodes',[]) if n['state']!=NodeState.CENTER]
    new=[n for n in result.nodes if n.state!=NodeState.CENTER]
    if not old or not new:return False
    matches=sum(any(math.dist(a['point'],frame.to_screen(b.x,b.y))<max(6,a['radius']*.25) for b in new) for a in old)
    new_matches=sum(any(math.dist(a['point'],frame.to_screen(b.x,b.y))<max(6,a['radius']*.25) for a in old) for b in new)
    # Entity removal yields a subset of the old web, not a new level.
    return matches<len(old)*.7 and new_matches<len(new)*.7
