"""Explicit opening experiment: five prepared inputs, then real evidence.

Only the first five non-perk actions on a pristine level are eligible. The
simulation predicts unlocks; it never supplies purchase evidence. Physical
input still checks the original frame's age, window, focus, cursor and Stop.
"""
import copy
from src.core.models import NodeState,NodeType,ScreenState
from src.core.geometry import purchasable
from src.core.path_planner import plan_route


def centre_follows(engine):
    # Without priorities the whole level already goes to the centre and never reaches a burst.
    return bool(engine.priorities) and engine.settings.center_auto_above_9 and engine.settings.fast_early_levels and not getattr(engine,'center_auto_unavailable',False)


def mid_level(engine,result):
    """K: after the first purchase a level can still be bought several nodes per read."""
    return engine.settings.burst_mid_level and any(n.state in (NodeState.PURCHASED,NodeState.ABSORBED) for n in result.nodes)


def prepare(engine,frame,result):
    def reject(reason,node=None):
        engine._turbo_last_reason=reason
        engine.trace('burst_eligibility',burst_requested=True,burst_eligible=False,burst_reject_reason=reason,
                     planned_actions=[],executed_actions=[],full_scans_during_burst=0,
                     confirmed_purchases_after_burst=0,burst_elapsed_ms=0,
                     rejected_target=(dict(node=node.id,icon=node.icon_id,type=node.type,rarity=node.rarity,
                         confidence=node.confidence,contour=node.contour_score,margin=node.icon_margin,
                         geometry=node.geometry_confidence) if node else None))
        engine.event(engine.state,'direct_burst_fallback',dict(level=result.level,reason=reason,node=node.id if node else None))
        return None
    if engine.settings.dry_run or not engine.approved:return reject('live_session_required')
    if engine.pending_action or engine.persisted_scene:return reject('unresolved_previous_input')
    if result.screen_state!=ScreenState.BLOODWEB or result.level is None or result.level_confidence<.95:
        return reject('fresh_level_header_required')
    if not 10<=result.level<=49:return reject('early_center_or_prestige')
    if any(n.state==NodeState.UNCERTAIN for n in result.nodes):return reject('opening_already_changed')
    mid=mid_level(engine,result)
    if not mid and any(n.state in (NodeState.PURCHASED,NodeState.ABSORBED) for n in result.nodes):
        return reject('opening_already_changed')
    attempted=engine.attempted_nodes(frame,result)
    if attempted and not mid:return reject('opening_already_attempted')
    remaining=sum(n.state in (NodeState.AVAILABLE,NodeState.LOCKED) for n in result.nodes)
    # No screenshot is taken between prepared inputs, so the level must not be able to end during
    # them. In the worst case the Entity takes one more node per purchase, hence two per input.
    limit=min(5,(remaining-1)//2) if mid else 5
    if remaining<=5 if not mid else limit<2:
        return reject('would_finish_level_before_evidence')
    if engine.settings.max_input_commands is not None and engine.attempts+limit>engine.settings.max_input_commands:
        return reject('command_budget')
    if engine.settings.max_actions is not None and engine.actions+limit>engine.settings.max_actions:
        return reject('node_budget')
    simulation=result.simulation_copy();steps=[]
    def known_standard(node):
        basic=(node.type not in (NodeType.UNKNOWN,NodeType.PERK) and node.category_hint!='perk' and node.icon_id
            and node.confidence>=.86 and node.contour_score>=.68 and node.geometry_confidence>=.65
            and node.rarity in ('brown','yellow','green','blue','purple','pink'))
        if not basic:return False
        matcher=getattr(engine.detector,'matcher',None);library=getattr(engine.detector,'library',None)
        if not matcher or node.icon_id not in matcher.by_id:return False
        from src.core.direct_buy import known_price
        candidate=copy.copy(node);candidate.state=NodeState.AVAILABLE
        return known_price(engine,frame,candidate) is not None
    # Empty Fast Level has no item ordering to preserve. Prefer a provable
    # non-perk branch over an arbitrary Unknown filler, without assigning an ID
    # to that Unknown or weakening any recognition threshold.
    excluded={n.id for n in simulation.nodes if n.state in (NodeState.AVAILABLE,NodeState.LOCKED)
        and not known_standard(n)} if not engine.priorities else set()
    excluded|=attempted
    priority_seen=False
    def closes_early():return mid and len(steps)>=2  # Later targets are simply left to the next read.
    for _ in range(limit):
        plan=plan_route(simulation,engine.priorities,engine.settings.priority_mode,excluded)
        node=next((n for n in simulation.nodes if n.id==plan.next_node),None)
        if engine.priorities and plan.reason=='filler' and node is not None and not known_standard(node):
            # Preserve every actual priority decision. As with Native Chain,
            # absent selected IDs do not require buying an unrecognised filler
            # before another provable filler branch. Unknown is not promoted.
            filler_excluded={n.id for n in simulation.nodes if n.state in (NodeState.AVAILABLE,NodeState.LOCKED)
                and not known_standard(n)}
            alternative=plan_route(simulation,engine.priorities,engine.settings.priority_mode,filler_excluded)
            if alternative.reason=='filler':
                plan=alternative;node=next((n for n in simulation.nodes if n.id==plan.next_node),None)
        if node is None or not purchasable(node,simulation.nodes,simulation.edges):
            if closes_early():break
            return reject('five_known_nonperk_targets_unreachable' if excluded else 'path_not_proven')
        perk=node.type==NodeType.PERK or node.category_hint=='perk'
        # PERKS-first (user, NEXT_TASKS 2.4): a perk may close the burst, nothing is sent after
        # it. As the very first target it stays an ordinary single purchase.
        if perk and not steps:return reject('early_perk_entity_risk',node)
        # Priorities done: the rest of the level is cheaper through the centre auto-purchase.
        if len(steps)>=2 and plan.reason=='filler' and priority_seen and centre_follows(engine):break
        priority_seen=priority_seen or plan.reason!='filler'
        if perk:
            candidate=copy.copy(node);candidate.state=NodeState.AVAILABLE
            from src.core.direct_buy import known_price
            if known_price(engine,frame,candidate) is None:
                if len(steps)>=2:break  # The burst ends before a perk it cannot bound.
                return reject('identity_or_standard_price_unproven',node)
        # Same bound as purchasable(): an unbought node's ring is capped near 0.70 by the formula
        # (live 7.11: 0.673 on clean webs) and this gate rejected almost every opening.
        if node.geometry_confidence<.65:
            if closes_early():break
            return reject('target_geometry_unproven',node)
        if not perk and not known_standard(node):
            if closes_early():break
            return reject('identity_or_standard_price_unproven',node)
        steps.append((simulation.simulation_copy(),copy.deepcopy(node)))
        if perk:break
        node.state=NodeState.PURCHASED
        neighbors={b if a==node.id else a for a,b in simulation.edges if node.id in (a,b)}
        for adjacent in simulation.nodes:
            if adjacent.id in neighbors and adjacent.state==NodeState.LOCKED:
                adjacent.state=NodeState.AVAILABLE;adjacent.state_confidence=.9
    engine.trace('burst_eligibility',burst_requested=True,burst_eligible=True,burst_reject_reason=None,
                 planned_actions=[dict(node=n.id,point=frame.to_screen(n.x,n.y)) for _,n in steps])
    engine.event(engine.state,'direct_burst_ready',dict(level=result.level))
    return steps


def dispatch(engine,frame,result):
    if hasattr(getattr(engine.screen,'backend',None),'close_probe'):
        engine.screen.backend.close_probe()
        engine.trace('probe_stream_stopped_before_burst',paid_inputs=0)
    steps=prepare(engine,frame,result)
    if not steps:return None
    from src.core.grouped_purchase import execute_group
    reserve=getattr(engine,'reserve_burst',lambda group:None)
    return execute_group(engine,frame,result,[n for _,n in steps],reserve,prepared=steps)
