"""Explicit experiment: up to three guarded inputs, then per-node evidence."""
import copy,time
from src.core.models import NodeState
from src.core.geometry import purchasable
from src.core.path_planner import plan_route
from src.core.scene_memory import record_scene
from src.core.action_outcomes import operation_id
from src.core.fast_opening import OpeningFallback
from src.core.screen_manager import ScreenError
from src.core.purchase_outcome import physical_node


def candidates(engine,frame,result,validate):
    if not engine.opening.active or not getattr(engine.opening,'persistent',False):return []
    if sum(n.state in (NodeState.AVAILABLE,NodeState.LOCKED) for n in result.nodes)<=3:return []
    simulation=result.simulation_copy();chosen=[]
    for _ in range(3):
        plan=plan_route(simulation,engine.priorities,engine.settings.priority_mode,engine.attempted_nodes(frame,simulation))
        original=next((n for n in result.nodes if n.id==plan.next_node),None)
        if original is None or original.id in [n.id for n in chosen]:break
        proposed=next(n for n in simulation.nodes if n.id==original.id)
        if not purchasable(proposed,simulation.nodes,simulation.edges):break
        # This copy proves only the catalogue price bound. A locked future
        # node still needs fresh observed availability before its actual down.
        price_candidate=copy.deepcopy(original);price_candidate.state=NodeState.AVAILABLE
        try:validate(engine,price_candidate)
        except ScreenError:break
        chosen.append(original)
        next(n for n in simulation.nodes if n.id==original.id).state=NodeState.PURCHASED
        neighbors={b if a==original.id else a for a,b in simulation.edges if original.id in (a,b)}
        for n in simulation.nodes:
            if n.id in neighbors and n.state==NodeState.LOCKED:n.state=NodeState.AVAILABLE;n.state_confidence=.9
    return chosen if len(chosen)==3 else []


class Group:
    def __init__(self,engine,frame,result,targets):
        self.engine=engine;self.frame=frame;self.targets=targets;self.sent=[];self.current=None
        self.base=record_scene(frame,result,engine.attempted,dict(batch={'physical_points':[frame.to_screen(n.x,n.y) for n in targets]}),previous=engine.scene_record)
        self.ident=operation_id(self.base)

    def scene(self,frame,result,node):
        self.current=frame.to_screen(node.x,node.y)
        scene=copy.deepcopy(self.base);scene['attempted']=[list(p) for p in self.engine.attempted]
        scene['pending']={'operation_id':self.ident,'point':list(self.current),'radius':node.radius,
            'batch':{'physical_points':[list(p) for p in self.sent+[self.current]]}}
        return scene

    def unsubmitted(self):
        self.current=None;scene=self.engine.scene_record
        scene['pending']=({'operation_id':self.ident,'point':list(self.sent[-1]),'radius':self.targets[0].radius,
            'batch':{'physical_points':[list(p) for p in self.sent]}} if self.sent else None)
        self.engine.pending_action=bool(self.sent)


def next_guard(engine,original,frame):
    """Wait briefly for fresh direct access, without any full analysis."""
    deadline=min(time.monotonic()+2.5,getattr(engine,'runtime_deadline',float('inf')))
    reason='group_target_not_yet_available';fresh=None
    while time.monotonic()<deadline:
        engine.gate();engine.poll()
        try:
            fresh=engine.capture_frame()
            web=engine.opening.analyze(fresh,engine.detector,engine.priorities,engine.settings.priority_mode,engine.actions)
        except OpeningFallback as exc:
            reason=str(exc)
            if reason not in ('center_or_overlay_changed','fresh_header_required'):break
        except ScreenError as exc:
            if exc.code not in ('stale_frame','item_tooltip','frame_unstable','bloodweb_not_found'):raise
            fresh=None;reason=exc.code
        else:
            node=next((n for n in web.nodes if n.id==original.id),None)
            if node is None:reason='group_target_lost';break
            current=plan_route(web,engine.priorities,engine.settings.priority_mode,engine.attempted_nodes(fresh,web))
            if purchasable(node,web.nodes,web.edges):
                if current.next_node==node.id:return fresh,web,current,node
                reason='group_current_priority_changed';break
            reason='group_target_not_yet_available'
        engine.trace('group_guard_retry',reason=reason,paid_inputs=0)
        engine.wait(.05)
    if fresh is not None:engine.confirmation_capture=fresh
    return reason


def execute_group(engine,frame,result,targets,reserve,prepared=None):
    if not (2<=len(targets)<=5 if prepared else len(targets)==3) or engine.pending_action or engine.persisted_scene or not engine.approved or engine.settings.dry_run:
        raise ScreenError('group_session_not_ready')
    if prepared and hasattr(engine.screen,'backend') and time.monotonic()-frame.captured_at>.4:
        # Initial FULL_SCAN can consume the lease before the first down.
        # Refresh once before reserving/sending, never between prepared inputs.
        from src.core.turbo_burst import prepare
        fresh,web,_=engine.recover_read(retry_stale=True)
        renewed=prepare(engine,fresh,web)
        age=time.monotonic()-fresh.captured_at
        engine.trace('burst_preflight_refresh',old_stamp=frame.captured_at,new_stamp=fresh.captured_at,
            scan_kind=web.details.get('scan_kind'),age=age,eligible=bool(renewed),paid_inputs=0)
        if not renewed or age>.4:
            if age>.4:engine.turbo_unfresh_scene=(fresh.window.signature,web.level)
            engine.preparation_changed_without_input=True
            raise ScreenError('purchase_preparation_changed')
        frame,result,prepared=fresh,web,renewed;targets=[n for _,n in renewed]
    group=Group(engine,frame,result,targets)
    if prepared:
        from src.core.direct_buy import known_price
        group.price_upper_bound=sum(known_price(engine,frame,n).value for _,n in prepared)
    reserve(group);started=time.perf_counter()
    full_before=getattr(engine.opening,'full_scans',None)
    current=getattr(engine.performance.levels,'current',None) or {}
    level_full_before=current.get('full')
    price_probes_before=getattr(engine,'price_probes_by_scene',{}).get((frame.window.signature,result.level),0)
    engine.trace('direct_burst_prepared' if prepared else 'group_prepared',full_scans_before=level_full_before,
        full_scans_total_before=full_before,price_probes=price_probes_before if prepared else None,commands_planned=len(targets),
        price_upper_bound=getattr(group,'price_upper_bound',None))
    engine.active_group=group;reason='three_inputs'
    engine.burst_inputs_active=bool(prepared)
    if prepared:reason='five_prepared_inputs'
    engine.trace('group_before',operation_id=group.ident,points=[frame.to_screen(n.x,n.y) for n in targets])
    try:
        for index,original in enumerate(targets):
            if prepared and index:
                # A 50ms down is not a 50ms game transaction. The native
                # 150ms cadence bought only2/5; leave the measured animation
                # interval, without taking another screenshot or replanning.
                engine.wait(max(0,engine.settings.burst_gap-(time.monotonic()-group.last_down_finished)))
            if prepared:
                result,node=prepared[index]
            elif index:
                guarded=next_guard(engine,original,frame)
                if isinstance(guarded,str):reason=guarded;break
                fresh,web,current,node=guarded
                frame,result=fresh,web;engine.recent_frames.append((frame,result,current))
                engine.trace('group_input_guard',frame=frame.metadata(),node=node.id,kind='compact_no_full')
            else:node=original
            engine.opening.expected_purchases.add(node.id)
            down_before=getattr(engine.mouse,'down_attempt_sequence',None)
            try:engine._buy_once(frame,result,node=node)
            except ScreenError as exc:
                # Expiration before a new MouseDown ends the prepared prefix.
                # Previously this skipped confirmation and paused the worker.
                # Never relax the lease or continue sending the remaining plan.
                if (prepared and exc.code=='stale_frame' and group.sent and down_before is not None
                        and getattr(engine.mouse,'down_attempt_sequence',None)==down_before):
                    engine.opening.expected_purchases.discard(node.id)
                    reason='burst_lease_expired_before_down'
                    engine.trace('burst_prefix_stopped',reason=reason,executed_actions=group.sent,
                        unsent_point=frame.to_screen(node.x,node.y),down_api_attempted=False)
                    break
                raise
            group.last_down_finished=time.monotonic()
        engine.burst_inputs_active=False
        engine.scene_record['pending']['batch'].update(deferred_verification=True,inputs_completed_at=time.monotonic())
        if prepared and hasattr(engine.mouse,'park') and hasattr(engine.screen,'parking_point'):
            parked=engine.screen.parking_point(frame)
            if parked:engine.mouse.park(parked,engine.stop,lambda:engine.check_held(frame,node.x,node.y))
        engine.trace('group_inputs_complete',commands=len(group.sent),reason=reason,verification_starts_now=True)
        if prepared:engine.trace('burst_inputs_complete',planned_actions=[group.frame.to_screen(n.x,n.y) for n in targets],
            executed_actions=group.sent,full_scans_during_burst=0,captures_during_burst=0,
            input_elapsed_ms=round((time.perf_counter()-started)*1000,3))
        engine.last_group_commands=len(group.sent)
        rows=[r for r in engine.click_lifecycle.rows if r['qpc']>=started]
        downs=[r.get('api_started_at',r['qpc']) for r in rows if r['stage']=='MOUSE_DOWN']
        ups=[r['qpc'] for r in rows if r['stage']=='MOUSE_UP']
        native_input_ms=(max(ups)-min(downs))*1000 if downs and ups else None
        confirmation_started=time.perf_counter()
        if not group.sent:raise ScreenError('group_no_input')
        engine.active_group=None;previous=None;signature=None
        deadline=min(time.monotonic()+engine.settings.purchase_timeout,engine.runtime_deadline)
        while time.monotonic()<deadline:
            engine.poll();f,r,p=engine.recover_read(deadline,retry_stale=True);states=[];observed=[]
            for point in group.sent:
                original=next(n for n in group.targets if group.frame.to_screen(n.x,n.y)==point)
                node=physical_node(f,r,point,original.radius)
                observed.append(dict(point=point,state=node.state.value if node else 'missing',
                    confidence=node.state_confidence if node else None,icon=node.icon_id if node else None))
                missed=(node is not None and node.state in (NodeState.AVAILABLE,NodeState.LOCKED) and node.state_confidence>=.8
                    and node.geometry_confidence>=.7 and (node.state==NodeState.LOCKED or purchasable(node,r.nodes,r.edges))
                    and f.captured_at-engine.scene_record['pending']['batch']['inputs_completed_at']>=1.5)
                if (node is None or (original.icon_id and node.icon_id and original.icon_id!=node.icon_id)
                        or not (node.state==NodeState.PURCHASED and node.state_confidence>=.8
                                or node.state==NodeState.ABSORBED and node.state_confidence>=.75 or missed)):
                    states.append('unresolved');continue
                states.append(node.state.value)
            if 'unresolved' in states:states=[]
            engine.trace('group_observation',states=states,observed=observed,frame=f.metadata())
            if states and signature==states and previous and f.captured_at>previous[0].captured_at:
                engine.reconcile_operation([previous,(f,r)],'group_worker')
                count=states.count('purchased');engine.actions+=count
                for original,state in zip(group.targets,states):
                    if state=='purchased':engine.performance.levels.purchase(original.icon_id in engine.priorities)
                engine.pending_action=False;engine.scene_record['pending']=None
                engine.trace('group_outcome',confirmed=count,absorbed=states.count('absorbed'),missed=states.count('available')+states.count('locked'),commands=len(group.sent),reason=reason)
                if prepared:engine.trace('burst_outcome',burst_requested=True,burst_eligible=True,burst_reject_reason=None,
                    planned_actions=[group.frame.to_screen(n.x,n.y) for n in targets],executed_actions=group.sent,
                    full_scans_during_burst=0,confirmed_purchases_after_burst=count,
                    all_planned_inputs_sent=len(group.sent)==len(targets),input_stop_reason=reason,
                    burst_elapsed_ms=round((time.perf_counter()-started)*1000,3),
                    native_first_down_to_last_up_ms=native_input_ms,confirmation_ms=(time.perf_counter()-confirmation_started)*1000,
                    captures_between_inputs=0,price_probes_before=price_probes_before,full_scans_before=level_full_before,
                    full_scans_total_before=full_before,
                    absorbed_after_burst=states.count('absorbed'),missed_after_burst=states.count('available')+states.count('locked'))
                return f,r,p
            signature=states or None;previous=(f,r) if states else None
        raise ScreenError('purchase_result_unknown','group_unresolved')
    finally:
        if prepared and engine.pending_action:
            engine.trace('burst_interrupted',burst_requested=True,burst_eligible=True,burst_reject_reason='input_or_outcome_incomplete',
                planned_actions=[group.frame.to_screen(n.x,n.y) for n in targets],executed_actions=group.sent,
                full_scans_during_burst=0,confirmed_purchases_after_burst=0,
                burst_elapsed_ms=round((time.perf_counter()-started)*1000,3))
        engine.burst_inputs_active=False;engine.active_group=None;engine.mouse.release()
