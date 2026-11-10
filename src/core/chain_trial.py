"""Bounded native chain executor, shared by ordinary Start and authorized trials.

No native device is constructed here. The engine supplies current Stop health,
clean maps and an explicit native preview/price permit for this operation.
"""
import json
import math
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from src.core.chain_plan import ChainEvidence
from src.core.models import NodeState, ScreenState
from src.core.persistent_route import map_key
from src.core.purchase_outcome import unchanged_scene
from src.core.screen_manager import ScreenError


@dataclass
class ChainPermit:
    scope_id: str
    ledger: Path
    max_commands: int
    max_nodes: int
    max_bp: int
    expires_at: float
    window_signature: tuple
    priorities: tuple
    approved: bool = False
    acknowledge_queue_cancellation_unverified: bool = False
    max_chain_items: int = 3

    def check(self):
        if not self.approved or not self.acknowledge_queue_cancellation_unverified or not self.scope_id:
            raise ScreenError('chain_explicit_permission_required')
        if any(type(v) is not int or v<=0 for v in (self.max_commands,self.max_nodes,self.max_bp)):
            raise ScreenError('invalid_chain_caps')
        if type(self.max_chain_items) is not int or not 2<=self.max_chain_items<=5:
            raise ScreenError('invalid_chain_caps')
        if time.time()>=self.expires_at:raise ScreenError('runtime_limit')

    @contextmanager
    def locked(self):
        self.check()
        # One owner across processes. Corrupt/mismatched budgets never reset.
        import msvcrt
        self.ledger=Path(self.ledger);self.ledger.parent.mkdir(parents=True,exist_ok=True)
        handle=self.ledger.with_suffix('.lock').open('a+b')
        try:
            handle.seek(0,2)
            if not handle.tell():handle.write(b'0');handle.flush()
            handle.seek(0)
            try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            except OSError as exc:raise ScreenError('chain_scope_already_running') from exc
            yield
        finally:handle.close()

    def reserve(self,plan):
        self.check()
        if not 2<=plan.expected_items<=self.max_chain_items:
            raise ScreenError('chain_item_limit')
        caps=dict(commands=self.max_commands,nodes=self.max_nodes,bp=self.max_bp)
        if self.ledger.exists():
            try:data=json.loads(self.ledger.read_text(encoding='utf-8'))
            except (ValueError,OSError) as exc:raise ScreenError('invalid_chain_ledger') from exc
            if data.get('scope_id')!=self.scope_id or data.get('caps')!=caps or data.get('expires_at')!=self.expires_at:
                raise ScreenError('chain_ledger_scope_mismatch')
        else:data=dict(scope_id=self.scope_id,caps=caps,expires_at=self.expires_at,used=dict(commands=0,nodes=0,bp=0),reservations=[])
        used=data.get('used',{})
        if any(type(used.get(k)) is not int or used[k]<0 for k in caps):raise ScreenError('invalid_chain_ledger')
        delta=dict(commands=1,nodes=plan.expected_items,bp=plan.total_bp)
        if any(used[k]+delta[k]>caps[k] for k in caps):raise ScreenError('chain_budget_limit')
        data['used']={k:used[k]+delta[k] for k in caps}
        from src.core.action_outcomes import operation_id
        data['reservations'].append(dict(map_id=plan.map_id,nodes=list(plan.ordered_nodes),bp=plan.total_bp,at=time.time(),
            operation_id=operation_id(dict(pending=dict(chain=plan.summary())))))
        # Charge every possible internal purchase before Mouse; never refund an
        # ambiguous/partial result. Persist through relaunches with the same scope.
        temp=self.ledger.with_suffix('.tmp')
        with temp.open('w',encoding='utf-8') as stream:
            json.dump(data,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,self.ledger)
        return data


def execute_short_chain(engine,plan,permit,previous,latest):
    journal=getattr(engine,'click_lifecycle',None)
    if journal is None:return _execute_short_chain(engine,plan,permit,previous,latest)
    with journal.operation('ACTUAL_PURCHASE',dict(node=plan.terminal,points=plan.physical_points,
            point=plan.physical_points[-1] if plan.physical_points else None,map_id=plan.map_id),kind='native_chain') as op:
        value=_execute_short_chain(engine,plan,permit,previous,latest)
        op['result']='InputSentUnconfirmed' if value.get('unconfirmed') else 'Purchased' if value.get('confirmed') else 'EntityConsumed'
        return value


def _execute_short_chain(engine,plan,permit,previous,latest):
    """One bounded native command; longer paths require an explicit permit."""
    permit.check();before_frame,before=previous;frame,result=latest
    now=time.monotonic();engine.gate()
    engine.check_input_budget()
    if engine.settings.dry_run or not engine.approved or engine.persisted_scene or engine.pending_action:
        raise ScreenError('chain_session_not_ready')
    production=(engine.settings.native_chain and getattr(engine,'native_chain_permit',None) is permit)
    if engine.settings.run_mode!='test' and not production:raise ScreenError('chain_trial_only')
    if (frame.window.signature!=permit.window_signature or tuple(engine.priorities)!=permit.priorities
            or list(engine.priorities)!=plan.priorities):raise ScreenError('chain_scope_changed')
    if (not frame.live or not before_frame.live or frame.probe_only or before_frame.probe_only
            or not frame.window.foreground or not before_frame.window.foreground
            or not before_frame.captured_at<frame.captured_at or now-frame.captured_at>2
            or now-before_frame.captured_at>2 or not unchanged_scene(previous,latest)
            or result.screen_state!=ScreenState.BLOODWEB):raise ScreenError('chain_two_clean_frames_required')
    generation=getattr(getattr(engine.detector,'memory',None),'generation',0)
    if plan.map_id!=map_key(frame,result,generation):raise ScreenError('chain_map_changed')
    if (plan.blockers or not 2<=plan.expected_items<=permit.max_chain_items or plan.game_preview!=plan.ordered_nodes
            or len(plan.preview_stamps)!=2 or not plan.preview_stamps[0]<plan.preview_stamps[1]<before_frame.captured_at
            or now-plan.preview_stamps[0]>5 or type(plan.total_bp) is not int or plan.total_bp<=0
            or not plan.price_provenance):raise ScreenError('chain_preview_or_price_unproven')
    # The BP counter is out of decisions unless its developer option is on.
    if getattr(engine.settings,'bloodpoint_checks',True) and (type(frame.bloodpoints) is not int or frame.bloodpoints<plan.total_bp
            or before_frame.bloodpoints!=frame.bloodpoints):raise ScreenError('chain_bp_unproven')
    from src.core.geometry import purchasable
    nodes={n.id:n for n in result.nodes};edges={tuple(sorted(e)) for e in result.edges}
    if (plan.available_start not in nodes or not purchasable(nodes[plan.available_start],result.nodes,result.edges)
            or plan.available_start!=plan.ordered_nodes[0] or plan.terminal!=plan.ordered_nodes[-1]
            or any(i not in nodes or nodes[i].state not in (NodeState.AVAILABLE,NodeState.LOCKED) for i in plan.ordered_nodes)
            or any(tuple(sorted((a,b))) not in edges for a,b in zip(plan.ordered_nodes,plan.ordered_nodes[1:]))):
        raise ScreenError('chain_fresh_path_unproven')
    if [frame.to_screen(nodes[i].x,nodes[i].y) for i in plan.ordered_nodes]!=plan.physical_points:
        raise ScreenError('chain_geometry_changed')
    target=nodes[plan.terminal];position=engine.check(frame,target.x,target.y)
    inner=target.radius*min(frame.roi.width/frame.image.shape[1],frame.roi.height/frame.image.shape[0])*.35
    # Two native previews of this exact route, each captured while the physical
    # pointer stayed on the endpoint, independently prove the game's hit-test.
    # Dim locked rings retain their original geometry score; ordinary direct
    # purchases and any chain without this positive native proof keep .70.
    native_hit=(len(plan.hover_points)==2 and all(tuple(p)==position for p in plan.hover_points))
    if inner<5 or (target.geometry_confidence<.7 and not native_hit):raise ScreenError('unsafe_node')
    if any(any(math.dist(p,q)<8 for q in engine.attempted) for p in plan.physical_points):
        raise ScreenError('target_already_attempted')
    evidence=ChainEvidence(frame,result,plan);last=latest;sent=None;transition_retired=False
    with permit.locked():
        permit.reserve(plan);engine.check(frame,target.x,target.y)
        engine.attempted.extend(plan.physical_points);engine.pending_action=True
        from src.core.scene_memory import record_scene
        engine.scene_record=record_scene(frame,result,engine.attempted,dict(point=list(position),radius=target.radius,icon=target.icon_id,chain=plan.summary()),previous=engine.scene_record)
        if hasattr(engine,'bind_click_operation'):engine.bind_click_operation()
        engine.trace('chain_before',plan=plan.summary(),cancellation_verified=False)
        opening=getattr(engine,'opening',None)
        if getattr(opening,'active',False) and getattr(opening,'persistent',False):
            # All preview-certified points may turn red after this one input.
            # This declares expectations only: compact vision must still read
            # each fresh rim/glyph, and other red changes remain unexpected.
            opening.expected_purchases.update(plan.ordered_nodes)
            engine.trace('chain_expected_purchases',nodes=list(plan.ordered_nodes))
        engine.mouse.target_region=(position,inner)
        try:
            sent=time.perf_counter();engine.attempts+=1
            engine.performance.levels.input()
            duration=engine.settings.hold_seconds if engine.settings.purchase_mode=='hold' else .05
            if hasattr(getattr(engine.screen,'backend',None),'close_probe'):engine.screen.backend.close_probe()
            engine.mouse.purchase(position,duration,engine.stop,lambda:engine.check_held(frame,target.x,target.y))
            engine.mouse.release();engine.gate()
            if hasattr(engine.mouse,'park') and hasattr(engine.screen,'parking_point'):
                point=engine.screen.parking_point(frame)
                if not point:raise ScreenError('chain_parking_required')
                engine.mouse.park(point,engine.stop,lambda:engine.check_held(frame,target.x,target.y))
            else:raise ScreenError('chain_parking_required')
            if hasattr(engine.screen,'fast_center_retry'):engine.screen.fast_center_retry=True
            deadline=min(time.monotonic()+engine.settings.purchase_timeout,time.monotonic()+max(0,permit.expires_at-time.time()))
            stale_retries=0;unresolved_reads=0;recropped=False
            while time.monotonic()<deadline:
                engine.poll();engine.gate();permit.check()
                try:fresh,analysis,_=engine.recover_read(deadline)
                except ScreenError as exc:
                    # Discard only stale observations, within the existing
                    # deadline. The native command is never submitted again.
                    if exc.code!='stale_frame' or stale_retries>=2:raise
                    stale_retries+=1
                    engine.trace('stale_frame_discarded',reason=exc.code,retry=stale_retries,source='chain_after_input')
                    continue
                last=(fresh,analysis)
                if type(fresh.bloodpoints) is int and type(frame.bloodpoints) is int and frame.bloodpoints-fresh.bloodpoints>plan.total_bp:
                    evidence.reason='chain_bp_exceeded';raise ScreenError(evidence.reason)
                from src.core.level_transition import header_transition
                if header_transition(engine.scene_record,fresh,analysis):
                    # Reuse exactly the saved-chain transition contract. Two
                    # fresh next-web maps may retire an old operation without
                    # crediting its unobserved nodes or retrying its old path.
                    if not engine.persisted_scene:
                        import copy
                        engine.persisted_scene=copy.deepcopy(engine.scene_record)
                    if engine.recheck_saved_scene(fresh,analysis):
                        transition_retired=True;engine.pending_action=False
                        engine.unconfirmed_level_actions+=1
                        engine.trace('chain_transition_retired',unconfirmed=evidence.summary()['unconfirmed'],
                            purchase_credit_for_missing_nodes=0,paid_retry=False)
                        break
                try:complete=evidence.observe(fresh,analysis)
                except ValueError as exc:raise ScreenError(str(exc)) from exc
                engine.scene_record['pending']['chain_outcome']=evidence.summary()
                engine.trace('chain_observation',analysis_id=analysis.analysis_id,outcome=evidence.summary())
                if complete or len(evidence.confirmed|evidence.entity)==plan.expected_items:break
                unresolved_reads+=1
                if (unresolved_reads>=3 and not recropped and hasattr(engine.screen,'reset_region')
                        and any(evidence.red[i]==0 and evidence.absorbed[i]==0 for i in evidence.summary()['unconfirmed'])):
                    # Reobserve geometry once when Hough persistently omits a
                    # shrinking ring. No node is filled from memory, no input
                    # is repeated, and the original confirmation deadline holds.
                    engine.screen.reset_region();recropped=True
                    if opening and hasattr(opening,'cancel'):opening.cancel('chain_confirmation_recrop')
                    engine.trace('chain_confirmation_recrop',paid_inputs=0,unconfirmed=evidence.summary()['unconfirmed'])
            outcome=evidence.summary();elapsed=time.perf_counter()-sent
            outcome.update(input_seconds=elapsed,seconds_per_confirmed_node=elapsed/len(evidence.confirmed) if evidence.confirmed else None,
                observed_bp_delta=frame.bloodpoints-last[0].bloodpoints if type(last[0].bloodpoints) is int and type(frame.bloodpoints) is int else None,
                partial=bool(evidence.confirmed) and len(evidence.confirmed)<plan.expected_items)
            outcome['level_transition_confirmed']=transition_retired
            engine.actions+=len(evidence.confirmed)
            from src.core.priority_targets import PERK_TARGET,perk_eligible
            for ident in evidence.confirmed:
                original=nodes[ident]
                engine.performance.levels.purchase(original.icon_id in engine.priorities or (PERK_TARGET in engine.priorities and perk_eligible(original)))
            if engine.performance.levels.current:engine.performance.levels.current['chains']+=1
            # An unresolved node keeps the saved scene blocked across Start.
            resolved=len(evidence.confirmed|evidence.entity)==plan.expected_items
            engine.pending_action=not (resolved or transition_retired)
            if resolved:
                engine.reconcile_operation([(f,r) for f,r,p in list(engine.recent_frames)[-2:]],'chain_worker')
                engine.scene_record['pending']=None
            from src.core.persistent_route import PersistentRoute
            route=PersistentRoute();route.choose(last[0],last[1],engine.priorities,engine.settings.priority_mode,
                engine.attempted_nodes(*last),getattr(engine.detector,'memory',None))
            engine.trace('chain_outcome',**outcome)
            return outcome
        except (ScreenError,InterruptedError):
            engine.stop.set()
            if engine.scene_record and engine.scene_record.get('pending'):
                engine.scene_record['pending']['chain_outcome']=evidence.summary()
                if evidence.reason in ('chain_unexpected_purchase','chain_bp_exceeded','chain_confirmed_state_contradiction'):
                    engine.scene_record['pending']['chain_blocked_reason']=evidence.reason
            raise
        finally:
            engine.mouse.release()
            if engine.pending_action:engine.trace('chain_unresolved',outcome=evidence.summary(),automatic_retry=False,cancellation_verified=False)
