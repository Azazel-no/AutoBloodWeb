"""Ordered-priority routes shared by previews and the verified input controller."""
from dataclasses import dataclass, field,replace
import heapq

from src.core.models import Analysis, NodeState, ScreenState,NodeType
from src.core.geometry import purchasable


@dataclass
class RoutePlan:
    next_node: int | None = None
    route: list[int] = field(default_factory=list)
    target_icon: str | None = None
    reason: str = 'no_action'
    benefit: float = 0.0
    cost_units: int = 0
    risk: float = 0.0
    assumptions: list[str] = field(default_factory=list)


def plan_route(analysis: Analysis, priorities,mode='strict',excluded=()) -> RoutePlan:
    priorities=list(priorities) if not isinstance(priorities,set) else sorted(priorities)
    ranks={ident:rank for rank,ident in reversed(list(enumerate(priorities)))}
    from src.core.priority_targets import EVENT_TARGET,event_eligible,PERK_TARGET,perk_eligible
    if PERK_TARGET in priorities:
        analysis=replace(analysis,nodes=[replace(n,icon_id=PERK_TARGET) if perk_eligible(n) else
            replace(n,state=NodeState.UNCERTAIN) if 'perk' in n.category_candidates and not n.icon_id else n for n in analysis.nodes])
    if EVENT_TARGET in priorities:
        # Keep the concrete detector identity intact in the original analysis.
        nodes=[]
        for n in analysis.nodes:
            if event_eligible(n) and ranks[EVENT_TARGET]<ranks.get(n.icon_id,len(priorities)):
                n=replace(n,icon_id=EVENT_TARGET)
            elif n.orange_background and not n.icon_id and not event_eligible(n) and n.state in (NodeState.AVAILABLE,NodeState.LOCKED):
                n=replace(n,state=NodeState.UNCERTAIN)
            nodes.append(n)
        analysis=replace(analysis,nodes=nodes)
    if analysis.screen_state != ScreenState.BLOODWEB:
        return RoutePlan(reason=analysis.screen_state.value)
    nodes={n.id:n for n in analysis.nodes}
    graph={n.id:[] for n in analysis.nodes}
    for a,b in analysis.edges:
        if a in graph and b in graph:
            graph[a].append(b); graph[b].append(a)
    roots=[n.id for n in analysis.nodes if n.state in (NodeState.CENTER,NodeState.PURCHASED) or n.type==NodeType.BLOODWEB_CENTER]
    blocked={n.id for n in analysis.nodes if n.state in (NodeState.ABSORBED,NodeState.UNCERTAIN)
             or n.type==NodeType.PRESTIGE or n.id in excluded}
    distances={n:0 for n in roots}; paths={n:[n] for n in roots}
    queue=[(0,n) for n in roots]; heapq.heapify(queue)
    while queue:
        distance,a=heapq.heappop(queue)
        if distance!=distances[a]:
            continue
        for b in graph[a]:
            if b in blocked:
                continue
            hazard=sum(nodes[nb].state==NodeState.ABSORBED for nb in graph[b])
            cost=0 if nodes[b].state==NodeState.PURCHASED else 1+hazard*.25
            total=distance+cost
            if total<distances.get(b,float('inf')):
                distances[b]=total; paths[b]=paths[a]+[b]
                heapq.heappush(queue,(total,b))
    targets=[n for n in analysis.nodes if n.icon_id in priorities and n.id in distances
             and n.id not in blocked and n.type not in (NodeType.BLOODWEB_CENTER,NodeType.PRESTIGE)
             and (n.type!=NodeType.PERK or n.icon_id==PERK_TARGET)
             and n.state in (NodeState.AVAILABLE,NodeState.LOCKED)]
    if mode=='top_first' and targets:
        # The best priority on this web comes first; a branch search must not trade it away.
        wanted=[n for n in analysis.nodes if n.icon_id in priorities and n.id not in excluded
                and n.type not in (NodeType.BLOODWEB_CENTER,NodeType.PRESTIGE)
                and (n.type!=NodeType.PERK or n.icon_id==PERK_TARGET)
                and n.state in (NodeState.AVAILABLE,NodeState.LOCKED)]
        top=min(ranks[n.icon_id] for n in wanted)
        for target in sorted((n for n in targets if ranks[n.icon_id]==top),key=lambda n:distances[n.id]):
            route=[ident for ident in paths[target.id] if nodes[ident].state not in (NodeState.CENTER,NodeState.PURCHASED)]
            if route and purchasable(nodes[route[0]],analysis.nodes,analysis.edges):
                return RoutePlan(route[0],route,target.icon_id,'priority')
    if mode in ('smart','top_first') and targets:
        smart=branch_search(analysis,nodes,graph,set(roots),blocked,ranks)
        if smart: return smart
    for target in sorted(targets,key=lambda n:(ranks[n.icon_id],distances[n.id])):
        route=[ident for ident in paths[target.id] if nodes[ident].state not in (NodeState.CENTER,NodeState.PURCHASED)]
        if route and purchasable(nodes[route[0]],analysis.nodes,analysis.edges):
            return RoutePlan(route[0],route,target.icon_id,'priority')
    available=[n for n in analysis.nodes if purchasable(n,analysis.nodes,analysis.edges)]
    available=[n for n in available if n.id in distances and n.id not in blocked]
    if available:
        node=min(available,key=lambda n:(distances.get(n.id,float('inf')),n.id))
        return RoutePlan(node.id,[node.id],node.icon_id,'filler')
    return RoutePlan(reason='no_confident_action')


def branch_search(analysis,nodes,graph,roots,blocked,ranks,depth=5,width=8):
    """Bounded branch value search. Entity outcomes and bloodpoint prices are unknown.

    Only the first action uses an actually available node. Adjacency unlocking
    in subsequent steps is a planning hypothesis, rechecked after each purchase.
    """
    candidates=[n for n in nodes.values() if n.id not in blocked and purchasable(n,analysis.nodes,analysis.edges) and any(i in roots for i in graph[n.id])]
    best=None
    for first in candidates:
        component=set(); pending=[first.id]
        while pending:
            ident=pending.pop()
            if ident in component or ident in roots or ident in blocked: continue
            component.add(ident); pending.extend(graph[ident])
        def value(ident):
            if nodes[ident].type in (NodeType.BLOODWEB_CENTER,NodeType.PRESTIGE): return 0
            rank=ranks.get(nodes[ident].icon_id)
            return 0 if rank is None else 8*(.5**rank)
        def risk(ident): return sum(nodes[j].state==NodeState.ABSORBED for j in graph[ident])*.35
        beam=[([first.id],value(first.id),risk(first.id))]
        for _ in range(depth-1):
            expanded=list(beam)
            for route,benefit,hazard in beam:
                bought=roots|set(route)
                for ident in component-set(route):
                    node=nodes[ident]
                    if node.state not in (NodeState.AVAILABLE,NodeState.LOCKED) or not any(j in bought for j in graph[ident]): continue
                    expanded.append((route+[ident],benefit+value(ident),hazard+risk(ident)))
            unique={frozenset(route):(route,benefit,hazard) for route,benefit,hazard in expanded}
            beam=sorted(unique.values(),key=lambda v:(v[1]-.75*len(v[0])-v[2],-len(v[0])),reverse=True)[:width]
        route,benefit,hazard=max(beam,key=lambda v:v[1]-.75*len(v[0])-v[2])
        if benefit<=0: continue
        score=benefit-.75*len(route)-hazard
        choice=(score,-len(route),-first.id)
        if best is None or choice>best[0]: best=(choice,route,benefit,hazard)
    if best is None: return None
    _,route,benefit,hazard=best
    target=next((nodes[i].icon_id for i in reversed(route) if nodes[i].icon_id in ranks),None)
    return RoutePlan(route[0],route,target,'smart_priority',round(benefit,3),len(route),round(hazard,3),
        ['cost_in_purchases_not_bloodpoints','hypothetical_adjacency_unlock','entity_future_unknown'])
