"""Central automatic unlock (the game buys the whole level).

Levels 1-9 as before. Above level 9 it is used when the level has nothing the
user prioritises (decided once per level), the Entity may take nodes meanwhile,
and if the game does not start buying within a few seconds the app records that
the centre is unavailable and buys node by node.
"""
import math,time
from pathlib import Path
from src.core.models import ScreenState,NodeState,NodeType
from src.core.priority_targets import PERK_TARGET
from src.core.screen_manager import ScreenError
from src.core.scene_memory import record_scene
from src.core.level_transition import header_transition
from src.core.click_lifecycle import transaction
from src.core.runtime_paths import project_root


UNAVAILABLE_SECONDS=4.  # Above level 9: no node bought this long after the click means no automatic unlock.
# Every node must be placed on the map before the centre is pressed (the click itself goes to
# the centre, geometry ~0.9). Live 7.11 20:29 (Cheryl Mason, level 48): one unidentified node's
# geometry wobbled 0.63-0.69 over the animated background and held the centre back 18 s at 0.65.
CENTER_NODE_GEOMETRY=.6


def has_priority_target(engine,result):
    from src.core.priority_targets import perk_eligible
    return any(n.state in (NodeState.AVAILABLE,NodeState.LOCKED) and
               (n.icon_id in engine.priorities or (PERK_TARGET in engine.priorities and perk_eligible(n)))
               for n in result.nodes if n.state!=NodeState.CENTER)


def center_level(engine,result):
    """Levels 1-9 always; above 9 once per level when nothing on it is a priority."""
    if type(result.level) is not int or result.level<1:return False
    if result.level<=9:return True
    if not engine.settings.center_auto_above_9 or getattr(engine,'center_auto_unavailable',None) in (True,result.level):return False
    # Re-evaluated on every read: once the last priority node is bought (or taken by the
    # Entity) the rest of the level goes to the game's own auto-purchase. Logged on change only.
    use=not has_priority_target(engine,result)
    decision=getattr(engine,'center_decision',None)
    if decision!=(result.level,use):
        reason='priority target present' if not use else ('priorities finished' if decision==(result.level,False) else 'no priority targets')
        engine.center_decision=(result.level,use)
        engine.trace('center_auto',level=result.level,use=use,reason=reason)
    return use


def center_mode(engine,result,calibrate=False):
    return (not calibrate and engine.settings.fast_early_levels and
        hasattr(engine.screen,'capture_overlay') and result.screen_state==ScreenState.BLOODWEB and
        center_level(engine,result))

def eligible(engine,result,frame=None):
    items=[n for n in result.nodes if n.state!=NodeState.CENTER]
    remaining=[n for n in items if n.state not in (NodeState.PURCHASED,NodeState.ABSORBED)]
    # A resolved earlier single purchase is compatible with buying the rest.
    # Every attempted point must still be explicitly resolved in today's frame;
    # above level 9 the Entity may have taken an attempted node.
    attempted_safe=not engine.attempted
    if frame is not None and engine.attempted:
        from src.core.purchase_outcome import physical_node
        resolved=(NodeState.PURCHASED,NodeState.ABSORBED) if type(result.level) is int and result.level>9 else (NodeState.PURCHASED,)
        attempted_safe=all((node:=physical_node(frame,result,p,20)) is not None and
            node.state in resolved and node.state_confidence>=(.75 if node.state==NodeState.ABSORBED else .8) for p in engine.attempted)
    return (not engine.settings.dry_run and engine.approved and engine.settings.fast_early_levels and result.screen_state==ScreenState.BLOODWEB and
            type(result.level) is int and center_level(engine,result) and result.level_confidence>=.95 and
            bool(remaining) and all(n.state in (NodeState.AVAILABLE,NodeState.LOCKED,NodeState.PURCHASED)+((NodeState.ABSORBED,) if result.level>9 else ())
                                    and n.geometry_confidence>=CENTER_NODE_GEOMETRY for n in items) and
            attempted_safe and not engine.pending_action and not engine.persisted_scene and
            (engine.settings.max_actions is None or engine.settings.max_actions-engine.actions-engine.unconfirmed_level_actions>=len(remaining)))

@transaction('early_center')
def purchase_level(engine,frame,result):
    """One bounded centre input; count only explicit twice-red item results."""
    engine.check_input_budget()
    center=next((n for n in result.nodes if n.state==NodeState.CENTER),None)
    if not eligible(engine,result,frame) or center is None:raise ScreenError('automatic_unlock_unproven')
    engine.check(frame,center.x,center.y)
    point=frame.to_screen(center.x,center.y)
    inner_radius=center.radius*min(frame.roi.width/frame.image.shape[1],frame.roi.height/frame.image.shape[0])*.35
    if inner_radius<5 or center.geometry_confidence<.7:raise ScreenError('unsafe_node')
    engine.mouse.target_region=(point,inner_radius)
    engine.trace('safe_input_region',center=point,inner_radius=inner_radius,analysis_id=result.analysis_id,automatic=True)
    engine.attempted.append(point);engine.pending_action=True
    reference=record_scene(frame,result,engine.attempted,dict(point=list(point),radius=center.radius,icon=None,automatic=True))
    engine.scene_record=reference
    if hasattr(engine,'bind_click_operation'):engine.bind_click_operation()
    targets={n.id:n for n in result.nodes if n.state in (NodeState.AVAILABLE,NodeState.LOCKED)};red={i:0 for i in targets};confirmed=set();previous=None;stable=0;last_stamp=frame.captured_at
    if engine.opening.active and getattr(engine.opening,'persistent',False):
        engine.opening.expected_purchases.update(targets)
    engine.emit(__import__('src.core.automation',fromlist=['State']).State.PURCHASING,'automatic_unlock_running')
    down_before=getattr(engine.mouse,'down_attempt_sequence',None)
    try:
        now=time.perf_counter()
        if hasattr(engine,'last_input_time'):engine.performance.add('input_interval',now-engine.last_input_time,'interval',action=engine.actions)
        engine.last_input_time=now
        with engine.performance.span('automatic_unlock_input','aggregate'):
            engine.purchase_input(point,.05 if engine.settings.purchase_mode!='hold' else engine.settings.hold_seconds,
                lambda:engine.check_held(frame,center.x,center.y),frame,center.x,center.y,allow_repeats=False)
        engine.mouse.release();parked=engine.screen.parking_point(frame)
        if parked:engine.mouse.park(parked,engine.stop,lambda:engine.check_held(frame,center.x,center.y))
        deadline=time.monotonic()+min(15,engine.settings.refresh_timeout)
        clicked_at=time.monotonic();high=result.level>9;unchanged=0
        while time.monotonic()<deadline:
            engine.gate();engine.poll()
            try:fresh,web,plan=engine.recover_read(deadline,retry_stale=True)
            except ScreenError as exc:
                # Live 13.11 (The Mastermind, level 50): a slow read came back older than 2 s
                # three times and aborted this wait before the "bought nothing" check below
                # could run. Keep reading until the deadline instead; no input is sent here.
                if exc.code!='stale_frame':raise
                engine.trace('automatic_unlock_stale_read',paid_inputs=0);continue
            if (not fresh.live or fresh.probe_only or not fresh.window.foreground or
                    fresh.window.signature!=frame.window.signature):raise ScreenError('live_frame_required')
            if fresh.captured_at<=last_stamp:continue
            last_stamp=fresh.captured_at
            if engine.settings.bloodpoint_checks and fresh.bloodpoints==0:raise ScreenError('insufficient_bp')
            if web.screen_state==ScreenState.TUTORIAL:
                from src.core.modal_recovery import dismiss_tutorial
                fresh,web,plan=dismiss_tutorial(engine,fresh,web,deadline=deadline)
            if web.screen_state in (ScreenState.PRESTIGE,ScreenState.REWARD):raise ScreenError('player_action_required')
            if web.screen_state==ScreenState.PRESTIGE_READY:
                # Level 50 bought out by the centre: the prestige emblem replaces the web.
                # No level header follows, so without this the wait ran into next_bloodweb_timeout.
                current=(fresh,web,None);stable=stable+1 if engine.stable_scene(previous,current) else 1;previous=current
                if stable>=2:
                    engine.pending_action=False;engine.persisted_scene=None;engine.attempted.clear()
                    reference['pending']=None;engine.scene_record=reference
                    if not confirmed:engine.unconfirmed_level_actions+=1
                    engine.trace('automatic_level_outcome',confirmed_nodes=len(confirmed),unconfirmed_nodes=len(targets)-len(confirmed),purchase_confirmed=bool(confirmed),next_screen='prestige_ready')
                    return fresh,web,plan
                continue
            if header_transition(reference,fresh,web):
                current=(fresh,web,None);stable=stable+1 if engine.stable_scene(previous,current) else 1;previous=current
                if stable>=2:
                    engine.pending_action=False;engine.persisted_scene=None;engine.attempted.clear()
                    engine.scene_record=record_scene(fresh,web,[])
                    if not confirmed:engine.unconfirmed_level_actions+=1
                    engine.trace('new_level_confirmed',analysis_id=web.analysis_id,source='early_automatic_unlock',observations=stable,level=web.level)
                    engine.trace('automatic_level_outcome',confirmed_nodes=len(confirmed),unconfirmed_nodes=len(targets)-len(confirmed),purchase_confirmed=bool(confirmed))
                    from src.core.path_planner import plan_route
                    plan=plan_route(web,engine.priorities,engine.settings.priority_mode)
                    return fresh,web,plan
                continue
            previous=None;stable=0
            if web.screen_state!=ScreenState.BLOODWEB:continue
            if not high and any(n.state==NodeState.ABSORBED for n in web.nodes):raise ScreenError('automatic_unlock_entity')
            from src.core.purchase_outcome import physical_node
            if not (type(web.level) is int and web.level==result.level and web.level_confidence>=.95):
                # Live 14.11 (The Mastermind, level 35 -> 36): the next web was already drawn
                # while its header was still unreadable; its fresh ring of buyable nodes was
                # taken for "bought nodes buyable again" and the run paused. Only a confidently
                # read same level can contradict; anything else is read again.
                engine.trace('automatic_unlock_level_unread',level=web.level,confidence=round(float(web.level_confidence or 0),3),paid_inputs=0)
                continue
            for original in result.nodes:
                if original.state!=NodeState.PURCHASED and original.id not in confirmed:continue
                found=physical_node(fresh,web,frame.to_screen(original.x,original.y),original.radius)
                if found and found.state!=NodeState.PURCHASED and found.state_confidence>=.8:
                    raise ScreenError('automatic_unlock_state_contradiction')
            for ident,node in targets.items():
                found=physical_node(fresh,web,frame.to_screen(node.x,node.y),node.radius)
                ok=found and found.state==NodeState.PURCHASED and found.state_confidence>=.8 and not(node.icon_id and found.icon_id and node.icon_id!=found.icon_id)
                red[ident]=red[ident]+1 if ok else 0
                if red[ident]>=2 and ident not in confirmed:
                    confirmed.add(ident);engine.actions+=1
                    from src.core.priority_targets import perk_eligible
                    engine.performance.levels.purchase(PERK_TARGET in engine.priorities and perk_eligible(node))
                    engine.trace('automatic_node_confirmed',point=frame.to_screen(node.x,node.y),observations=red[ident],analysis_id=web.analysis_id)
            if high and not confirmed:
                # Nothing bought or taken yet: every original target still available.
                still=all((found:=physical_node(fresh,web,frame.to_screen(n.x,n.y),n.radius)) is not None
                          and found.state in (NodeState.AVAILABLE,NodeState.LOCKED) for n in targets.values())
                unchanged=unchanged+1 if still else 0
                if unchanged>=2 and time.monotonic()-clicked_at>=UNAVAILABLE_SECONDS:
                    # Verified on fresh frames that the centre bought nothing: retire the click, buy node by node.
                    # One silent press (live 13.11, level 50) is not proof that the game never
                    # auto-buys: this level goes node by node, the next level tries the centre
                    # again. Two levels in one run without effect switch it off for good.
                    failed=getattr(engine,'center_failed_levels',set())|{result.level};engine.center_failed_levels=failed
                    engine.center_auto_unavailable=True if len(failed)>=2 else result.level
                    engine.pending_action=False;reference['pending']=None;engine.scene_record=reference
                    if point in engine.attempted:engine.attempted.remove(point)
                    reference['attempted']=[list(p) for p in engine.attempted]
                    engine.trace('center_auto_unavailable',level=result.level,observations=unchanged,paid_inputs=1,purchase_credit=0)
                    from src.core.path_planner import plan_route
                    return fresh,web,plan_route(web,engine.priorities,engine.settings.priority_mode)
        raise ScreenError('next_bloodweb_timeout')
    except Exception:
        if type(down_before) is int and engine.mouse.down_attempt_sequence==down_before:
            engine.pending_action=False;reference['pending']=None
            if point in engine.attempted:engine.attempted.remove(point)
            reference['attempted']=[list(p) for p in engine.attempted]
            engine.trace('input_not_submitted',point=point,down_api_attempted=False,automatic=True)
        raise
    finally:engine.mouse.release()
