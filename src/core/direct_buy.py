"""Price provenance and direct single preparation; never purchase evidence."""
from dataclasses import dataclass
import json,time
from src.core.models import NodeState,NodeType
from src.core.screen_manager import ScreenError


@dataclass(frozen=True)
class PriceBound:
    value: int
    source: str
    provenance: dict


def standard_target(engine,node):
    """Existing published 5000 BP ceiling, not an exact rarity-derived price."""
    if (node is None or node.state!=NodeState.AVAILABLE or node.event_offering or node.orange_background
            or node.rarity not in ('brown','yellow','green','blue','purple','pink')):
        raise ScreenError('single_standard_price_bound_unproven')
    if node.type==NodeType.PERK and node.category_confidence>=.95:return
    library=engine.detector.library
    entry=engine.detector.matcher.by_id.get(node.icon_id)
    if (entry is None or node.icon_id in getattr(library,'event_metadata',{})
            or node.confidence<.86 or node.contour_score<.68):
        raise ScreenError('single_standard_price_bound_unproven')


def accepted_id(engine,node):
    return bool(node and node.icon_id and node.confidence>=.86 and node.contour_score>=.68
        and node.icon_margin>=.035 and node.geometry_confidence>=.7
        and node.icon_id in getattr(getattr(engine.detector,'matcher',None),'by_id',{}))


def known_price(engine,frame,node):
    cache=getattr(engine,'direct_price_observations',{})
    saved=cache.get(node.icon_id) if accepted_id(engine,node) else None
    if (saved and saved['window']==frame.window.signature and saved['rarity']==node.rarity
            and saved['type']==node.type and 0<=time.monotonic()-saved['observed_at']<=600):
        return PriceBound(saved['value'],'native_observed_upper_bound',saved)
    try:standard_target(engine,node)
    except (ScreenError,AttributeError):return None
    return PriceBound(5000,'existing_standard_catalog_ceiling',dict(icon_id=node.icon_id,
        price_is_upper_bound=True,source='https://forums.bhvr.com/dead-by-daylight/kb/articles/167-bloodwebs'))


def remember_price(engine,frame,node,permit):
    """Keep two independent native readings for a currently accepted ID only."""
    if not accepted_id(engine,node) or len(permit.get('proofs',[]))!=1:return
    proof=permit['proofs'][0];stamps=proof.get('stamps',[]);value=proof.get('price')
    if (proof.get('node')!=node.id or type(value) is not int or value<=0 or value!=permit.get('price')
            or len(stamps)!=2 or not stamps[0]<stamps[1] or len(proof.get('source_sha256',''))!=64):return
    saved=dict(value=value,observed_at=time.monotonic(),window=frame.window.signature,rarity=node.rarity,
        type=node.type,stamps=stamps,source_sha256=proof['source_sha256'],box=proof.get('box'),icon_id=node.icon_id)
    cache=getattr(engine,'direct_price_observations',None)
    if cache is None:cache={};engine.direct_price_observations=cache
    cache[node.icon_id]=saved


def preparation_key(frame,node):return (frame.window.signature,frame.captured_at,frame.to_screen(node.x,node.y),node.icon_id)


def prefer_direct_single(engine,frame,result,node):
    if node is None:return False
    from src.core.path_planner import plan_route
    plan=plan_route(result,engine.priorities,engine.settings.priority_mode,engine.attempted_nodes(frame,result))
    return plan.next_node==node.id and len(plan.route)<=1


def install(engine,root):
    """Node costs are neither read nor counted: the target is bought directly."""
    def prepare(frame,result,node):
        engine.trace('direct_buy_prepared',node=node.id,point=frame.to_screen(node.x,node.y),price_probes=0,source='ordinary_start')
        return frame,result,node
    engine.prepare_direct_single=prepare


def chain_key(engine,frame,result):
    """Substantive map state, excluding stamps, score jitter and renumbering."""
    nodes=tuple(sorted((frame.to_screen(n.x,n.y),n.state,n.icon_id,n.type,n.rarity,
        n.confidence>=.86,n.contour_score>=.68,n.geometry_confidence>=.7) for n in result.nodes))
    points={n.id:frame.to_screen(n.x,n.y) for n in result.nodes}
    edges=tuple(sorted(tuple(sorted((points[a],points[b]))) for a,b in result.edges))
    return frame.window.signature,result.level,tuple(engine.priorities),engine.settings.priority_mode,nodes,edges


def chain_probe_allowed(engine,frame,result):
    key=chain_key(engine,frame,result)
    if getattr(engine,'chain_rejected_scene',None)==key:
        engine.trace('chain_skipped',reason='same_map_preparation_already_rejected',paid_inputs=0)
        return False
    return True


def reject_chain(engine,frame,result):engine.chain_rejected_scene=chain_key(engine,frame,result)
