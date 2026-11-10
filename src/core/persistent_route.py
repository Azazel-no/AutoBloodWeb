"""Experimental route persistence. Plans are hypotheses, never input permits."""
import copy
import hashlib
from dataclasses import asdict
from src.core.models import NodeState, ScreenState
from src.core.geometry import purchasable
from src.core.path_planner import RoutePlan, plan_route
from src.core.performance import measured


def map_key(frame, result, generation):
    geometry=tuple((n.id,frame.to_screen(n.x,n.y)) for n in result.nodes)
    context=(frame.window.signature,frame.roi,frame.image.shape,result.level,generation,geometry)
    return hashlib.sha256(repr(context).encode()).hexdigest()[:24]


def state_key(result, excluded):
    from src.core.priority_targets import perk_eligible
    # A newly independently accepted same ID does not change route intent just
    # because independent shape evidence became Unknown. Perk eligibility and
    # suspected anonymous perks do affect routing and must invalidate it.
    return (tuple((n.id,n.state,n.icon_id,n.type,perk_eligible(n),
                   'perk' in n.category_candidates and not n.icon_id) for n in result.nodes),
            tuple(sorted(tuple(sorted(e)) for e in result.edges)),tuple(sorted(excluded)))


class PersistentRoute:
    def __init__(self, strict_priorities=True):
        self.strict_priorities=strict_priorities
        self.map_id=None;self.revision=0;self.replans=0;self.reuses=0
        self.queue=[];self.expected={};self.reason='initial';self.previous=None

    @measured('replan','compute')
    def rebuild(self, result, priorities, mode, excluded):
        simulation=result.simulation_copy();self.queue=[]
        # Priority Mode explicitly respects the user's ordering, including PERKS.
        # Old smart mode remains available through the default planner/fallback.
        policy='strict' if priorities and self.strict_priorities else mode
        for _ in range(len(simulation.nodes)):
            plan=plan_route(simulation,priorities,policy,excluded)
            if plan.next_node is None:break
            self.queue.append(copy.deepcopy(plan))
            owned=next(n for n in simulation.nodes if n.id==plan.next_node)
            owned.state=NodeState.PURCHASED
            neighbors={b if a==owned.id else a for a,b in simulation.edges if owned.id in (a,b)}
            for node in simulation.nodes:
                if node.id in neighbors and node.state==NodeState.LOCKED:
                    node.state,node.state_confidence=NodeState.AVAILABLE,.90
        self.expected={n.id:n.state for n in result.nodes}
        self.revision+=1;self.replans+=1

    def choose(self, frame, result, priorities, mode='strict', excluded=(), memory=None):
        if result.screen_state!=ScreenState.BLOODWEB:
            self.map_id=None;self.queue=[]
            return RoutePlan(reason=result.screen_state.value)
        current=map_key(frame,result,getattr(memory,'generation',0))
        signature=state_key(result,excluded);choices=(tuple(priorities),mode)
        nodes={n.id:n for n in result.nodes}
        reason=None
        if current!=self.map_id:reason='new_or_untrusted_map'
        elif choices!=self.choices:reason='priorities_changed'
        elif self.previous is not None:
            old_states=dict((ident,state) for ident,state,*_ in self.previous[0])
            # An intended purchase and newly available neighbors are expected.
            # Absorption, a lost line, or a changed identity require a memory replan.
            if self.previous[1]!=signature[1]:reason='fresh_graph_changed'
            elif any(n.state==NodeState.ABSORBED and old_states.get(n.id)!=n.state for n in result.nodes):reason='entity_observed'
            elif any(old[2:]!=new[2:] and new[1] not in (NodeState.PURCHASED,NodeState.ABSORBED) for old,new in zip(self.previous[0],signature[0])):reason='identity_or_category_changed'
            elif any(old_states.get(n.id)==NodeState.PURCHASED and n.state!=NodeState.PURCHASED for n in result.nodes):reason='state_contradiction'
            elif any(ident in nodes and nodes[ident].state in (NodeState.AVAILABLE,NodeState.LOCKED)
                     for ident in set(self.previous[2])-set(signature[2])):
                # Reconciliation can release attempted physical slots after
                # the route was seeded. Restore their unowned paths once;
                # ordinary purchased targets leaving exclusions need no replan.
                reason='unowned_exclusions_cleared'
        if reason is None:
            self.queue=[p for p in self.queue if nodes[p.next_node].state!=NodeState.PURCHASED]
            if (self.queue and self.queue[0].next_node in excluded
                    and nodes[self.queue[0].next_node].state==NodeState.AVAILABLE):
                # Await the pending purchase; exclusion is not a topology
                # change and cannot authorize another attempt at this target.
                self.previous=signature;self.reuses+=1;self.reason='pending_confirmation'
                if memory is not None:memory.route_plan=self.summary(frame,result)
                return RoutePlan(reason='persistent_confirmation')
            if self.queue and (self.queue[0].next_node in excluded or not purchasable(nodes[self.queue[0].next_node],result.nodes,result.edges)):
                reason='remaining_route_unavailable'
        self.map_id=current;self.choices=choices
        if reason:
            self.reason=reason;self.rebuild(result,priorities,mode,excluded)
        elif getattr(self,'seeded',False):self.seeded=False;self.reason='new_or_untrusted_map'
        else:self.reuses+=1;self.reason='remaining_route_valid'
        self.previous=signature
        self.expected={n.id:n.state for n in result.nodes}
        if memory is not None:memory.route_plan=self.summary(frame,result)
        if not self.queue:return RoutePlan(reason='persistent:no_confident_action')
        plan=copy.deepcopy(self.queue[0]);plan.reason='PERSISTENT:'+plan.reason
        if not purchasable(nodes[plan.next_node],result.nodes,result.edges):
            return RoutePlan(reason='persistent:fresh_direct_access_required')
        return plan

    def summary(self, frame=None, result=None):
        return dict(map_id=self.map_id,revision=self.revision,replans=self.replans,reuses=self.reuses,
            reason=self.reason,intent='priority' if getattr(self,'choices',((),))[0] else 'fast_level',
            priorities=list(getattr(self,'choices',((),))[0]),
            remaining=[asdict(p) for p in self.queue],expected_states=dict(self.expected),
            evidence_stamp=frame.captured_at if frame else None,
            physical_points={n.id:frame.to_screen(n.x,n.y) for n in result.nodes} if frame and result else {})


from src.core.fast_opening import FastOpening, OpeningFallback


class PersistentVision(FastOpening):
    """Same compact certificates, with observed Entity changes and a level route."""
    persistent=True

    def __init__(self, strict_priorities=True, local_recognition=False):
        super().__init__();self.route=PersistentRoute(strict_priorities);self.expected_purchases=set();self.observed_red=set();self.absorbed_nodes=set()
        self.local_recognition=local_recognition;self.local_library_matches=0

    def verify_local(self,frame,node,detector):
        try:return super().verify_local(frame,node,detector)
        except OpeningFallback as exc:
            if not self.local_recognition or str(exc)!='glyph_category_or_rarity_changed':raise
        # A failed temporal/global bound does not justify rerunning geometry
        # and every other glyph. Independently rank only this fresh crop with
        # the normal matcher and thresholds; never take an ID from memory.
        from dataclasses import fields
        from src.core.models import Match,NodeType
        feature,key,category,patch=self.parts(frame,node,detector)
        old_key=getattr(self,'features',{}).get(node.id,(None,key))[1]
        if node.icon_id and node.state!=NodeState.PURCHASED and key!=old_key:
            entry=detector.matcher.by_id.get(node.icon_id)
            uncertain=(entry and key[:2]==old_key[:2] and not key[-1] and not old_key[-1]
                and category.category=='unknown' and entry.category[:-1] in category.candidates)
            if not uncertain:raise OpeningFallback('glyph_category_or_rarity_changed')
        if key[-1]:
            match=Match(reason='suspected_perk',rarity=key[0]);kind=NodeType.PERK
        else:
            match=detector.matcher.match(patch,node.content_radius or node.radius,
                prepared_feature=feature,use_cache=False)
            self.local_library_matches+=1
            self.frame_library_matches=getattr(self,'frame_library_matches',0)+1
            kind=NodeType(detector.matcher.by_id[match.icon_id].category[:-1]) if match.icon_id else NodeType.UNKNOWN
        if node.icon_id and match.icon_id and node.icon_id!=match.icon_id:
            raise OpeningFallback('conflicting_fresh_identity')
        for field in fields(Match):
            if hasattr(node,field.name):setattr(node,field.name,copy.deepcopy(getattr(match,field.name)))
        node.type=kind;node.icon_margin=match.margin;node.match_reason=match.reason
        node.category_hint=category.category;node.category_confidence=category.confidence
        node.category_candidates=list(category.candidates)
        node.event_offering=category.event_offering and kind not in (NodeType.PERK,NodeType.ITEM,NodeType.ADDON)
        node.event_confidence=category.confidence if node.event_offering else 0.
        node.orange_background=category.orange_fraction>=.45
        node._opening_overlaps=getattr(match,'_opening_overlaps',None)
        _,fingerprint,_,previous=detector.memory.identity(node.x,node.y,patch,node.content_radius or node.radius,category)
        self.local_updates[node.id]=(fingerprint,previous)
        self.refreshed_matches[node.id]=match
        self.certificates.append(dict(node=node.id,fresh_local_ranking=True,icon_id=node.icon_id,
            score=node.confidence,margin=node.icon_margin,contour=node.contour_score))

    def begin(self,*args):
        # Re-entry is permitted only after the caller did a fresh full analysis.
        # An invalid local map itself cannot reinitialize certificates.
        if not self.active:self.seen.discard(self.context(args[0],args[2]))
        ready=super().begin(*args)
        if ready:
            frame,result,detector,priorities,mode,_=args
            self.route.map_id=map_key(frame,result,detector.memory.generation)
            self.route.choices=(tuple(priorities),mode)
            self.route.previous=state_key(result,())
            self.route.seeded=True
            self.expected_purchases={n.id for n in self.nodes if n.state==NodeState.PURCHASED}
            self.observed_red=set(self.expected_purchases)
            self.absorbed_nodes={n.id for n in self.nodes if n.state==NodeState.ABSORBED}
        return ready

    def plan(self,result,actions,excluded):
        plan=self.route.choose(self.last_frame,result,self.priorities,self.mode,excluded,self.memory)
        if plan.next_node is not None:self.expected_purchases.add(plan.next_node)
        return plan

    def summary(self):
        return dict(super().summary(),persistent_route=self.route.summary(),local_library_matches=self.local_library_matches)
