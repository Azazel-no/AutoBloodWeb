"""One verified purchase per fresh analysis. Injectable devices allow no-input tests."""
from dataclasses import dataclass, asdict
from enum import StrEnum
import threading
import time
import hashlib
import math
from collections import deque
from pathlib import Path
from src.core.models import NodeState, ScreenState
from src.core.path_planner import plan_route
from src.core.screen_manager import ScreenError
from src.core.geometry import purchasable
from src.core.models import NodeType
from src.core.action_report import ActionReport
from src.core.performance import measured
from src.core.click_lifecycle import ClickLifecycle,transaction


class State(StrEnum):
    SCANNING='scanning'
    WAIT_STABLE='wait_stable'
    RECOVERING='recovering'
    READY='automation_ready'
    DRY_RUN='dry_run'
    PURCHASING='purchasing'
    WAIT_PURCHASE='wait_purchase'
    ENTITY='entity'
    FINISHED='finished'
    WAIT_NEXT_BLOODWEB='wait_next_bloodweb'
    WAIT_REFRESH='wait_next_bloodweb'  # Compatibility name for callers.
    MANUAL_ACTION='manual_action'
    PRESTIGE='prestige'
    PRESTIGE_ACTIVATION='prestige_activation'
    PRESTIGE_ANIMATION='prestige_animation'
    REWARD='reward'
    INSUFFICIENT_BP='insufficient_bp'
    UNKNOWN='unknown'
    PAUSED='paused'
    ERROR='error'
    STOPPED='stopped'


@dataclass
class Settings:
    dry_run: bool = True
    auto_prestige: bool = False
    prestige_max_bp: int = 20000
    prestige_price_calibration: dict | None = None
    purchase_mode: str = 'hold'
    hold_seconds: float = .65
    purchase_timeout: float = 8
    refresh_timeout: float = 15
    startup_delay: float = 3
    poll_interval: float = .3
    max_actions: int | None = 20
    max_input_commands: int | None = None
    max_runtime: float | None = 300
    focus_timeout: float = 20
    priority_mode: str = 'top_first'
    character: str = 'blight'
    detailed_diagnostics: bool = False
    max_consecutive_errors: int = 12
    run_mode: str = 'test'
    fast_early_levels: bool = True
    center_auto_above_9: bool = True  # Centre auto-purchase above level 9 when nothing on the level is a priority.
    fast_opening: bool = True
    persistent_route: bool = True
    experimental_persistent: bool = False
    shadow_planner: bool = False
    native_chain: bool = False
    turbo_burst: bool = False
    burst_mid_level: bool = False  # K: several clicks per read after the opening too.
    burst_gap: float = .55  # Seconds between prepared clicks; the game drops inputs that come too fast.
    recovery_supervisor: bool = False
    # The BP counter is not read by default: only nodes matter. Running out of BP
    # is detected from the web (verified unbought purchases). Developer option.
    bloodpoint_checks: bool = False
    max_levels: int | None = None  # Stop after this many completed levels (a prestige counts as one).

    def validate(self):
        for name,lo,hi in (('hold_seconds',.05,5),('purchase_timeout',1,30),('refresh_timeout',2,120),
                           ('startup_delay',0,30),('focus_timeout',1,120),('poll_interval',.01,2),('max_actions',1,10000),('max_runtime',1,86400)):
            value=getattr(self,name)
            if value is None and name in ('max_actions','max_runtime') and self.run_mode=='continuous':continue
            if not isinstance(value,(int,float)) or not math.isfinite(value) or not lo<=value<=hi:
                raise ValueError('invalid_setting: '+name)
        if self.purchase_mode not in ('click','hold','repeat'): raise ValueError('invalid_purchase_mode')
        if self.max_input_commands is not None and (type(self.max_input_commands) is not int or not 1<=self.max_input_commands<=10000):
            raise ValueError('invalid_setting: max_input_commands')
        if self.priority_mode not in ('top_first','smart','strict'): raise ValueError('invalid_priority_mode')
        if self.run_mode not in ('test','continuous'):raise ValueError('invalid_run_mode')
        if type(self.fast_early_levels) is not bool:raise ValueError('invalid_setting: fast_early_levels')
        if type(self.fast_opening) is not bool:raise ValueError('invalid_setting: fast_opening')
        if type(self.prestige_max_bp) is not int or not 1<=self.prestige_max_bp<=20000:raise ValueError('invalid_setting: prestige_max_bp')
        if self.prestige_price_calibration is not None and not isinstance(self.prestige_price_calibration,dict):
            raise ValueError('invalid_setting: prestige_price_calibration')
        if type(self.burst_gap) not in (int,float) or not .25<=self.burst_gap<=1:raise ValueError('invalid_setting: burst_gap')
        for name in ('persistent_route','experimental_persistent','shadow_planner','native_chain','turbo_burst','burst_mid_level','recovery_supervisor'):
            if type(getattr(self,name)) is not bool:raise ValueError('invalid_setting: '+name)
        if type(self.max_consecutive_errors) is not int or not 1<=self.max_consecutive_errors<=100:raise ValueError('invalid_setting: max_consecutive_errors')


class Automation:
    def __init__(self,screen,detector,mouse,priorities,settings=None,event=None,approved=False,hotkeys_ready=False,
                 report_path=None,calibration_target=None,prestige_approved=False):
        self.screen,self.detector,self.mouse=screen,detector,mouse
        self.priorities=list(priorities); self.settings=settings or Settings(); self.settings.validate()
        self.event=event or (lambda *args:None)
        self.approved,self.hotkeys_ready=approved,hotkeys_ready
        self.stop=threading.Event(); self.pause=threading.Event(); self.state=State.STOPPED
        self.signature=None; self.receipt=None; self.actions=0
        self.attempts=0;self.entity_actions=0;self.transitions=0;self.entity_points=None
        self.prestige_actions=0
        self.report=ActionReport(report_path,flush_interval=.25); self.mouse.audit=self.trace
        self.click_lifecycle=ClickLifecycle(self.report.path.with_suffix('.clicks.jsonl') if self.report.path else None)
        self.mouse.lifecycle=self.click_lifecycle
        self.evidence_writer=None
        self.calibration_target=calibration_target
        self.calibration_window_proof=None
        self.prestige_approved=prestige_approved
        self.attempted=[]; self.transition_verified=False
        self.pending_action=False
        self.unconfirmed_level_actions=0
        self.consecutive_errors=0
        self.completed_layout=None
        self.input_ready_check=None
        self.screen.fast_center_retry=False
        self.last_purchase_outcome=None
        self.retry_permit=None
        self.persisted_scene=None;self.scene_record=None;self.pending_scene_confirmations=0
        self.recent_frames=deque(maxlen=3)
        from src.core.fast_opening import FastOpening
        from src.core.persistent_route import PersistentVision
        self.opening=(PersistentVision(strict_priorities=self.settings.experimental_persistent,local_recognition=True)
            if self.settings.experimental_persistent or (self.settings.persistent_route and self.settings.fast_opening)
            else FastOpening())
        from src.core.shadow_planner import ShadowPlanner
        self.shadow=ShadowPlanner() if self.settings.shadow_planner else None
        from src.core.performance import Performance
        self.performance=Performance(Path(report_path).with_suffix('.performance.json') if report_path else None,report_path)
        if hasattr(self.opening,'route'):self.opening.route.performance=self.performance
        self.diagnostics=None
        self.outcome_journal=None
        self.active_group=None
        self.preparation_recoveries=0;self.consecutive_preparation_changes=0
        from src.core.recovery_supervisor import RecoverySupervisor
        self.recovery=RecoverySupervisor() if self.settings.recovery_supervisor else None
        self.readonly_resolution=None
        if report_path:
            from src.core.diagnostics import DiagnosticStore
            self.diagnostics=DiagnosticStore(self.report.path.parent.parent/'analysis_runs')

    def trace(self,stage,**details):
        if stage in ('input_commit_begin','input_commit_end'):
            self.report.defer_flush=stage=='input_commit_begin'
        journal=getattr(self,'click_lifecycle',None)
        if journal and stage in ('chain_skipped','native_price_target_refreshed','native_price_clean_wait',
                'native_price_preparation_changed','retry_revalidated','purchase_outcome'):
            journal.record('PREPARATION_REJECTED' if stage=='chain_skipped' else stage.upper(),**details)
        import logging
        logging.getLogger(__name__).info('Action %s %s',stage,details)
        if stage=='new_level_confirmed':
            self.transitions+=1;self.entity_points=None
            # The same new level can be confirmed by two paths (saved scene, then the
            # next-level wait): the level limit counts a level once, by its number.
            level=details.get('level')
            if level is None or level!=getattr(self,'last_counted_level',None):
                self.levels_completed=getattr(self,'levels_completed',0)+1;self.last_counted_level=level
            if self.recent_frames:
                frame,result,_=self.recent_frames[-1]
                if result.screen_state==ScreenState.BLOODWEB:
                    self.performance.levels.confirm(frame,result,transition=self.performance.levels.current is not None)
        if stage=='SendInput' and 'seconds' in details:
            self.performance.add('physical_button_input',details['seconds'],action=self.actions,flag=details.get('flag'))
            self.input_api_seconds=getattr(self,'input_api_seconds',0)+details['seconds']
        if stage=='cursor_api' and 'seconds' in details:
            self.performance.add('cursor_movement_api',details['seconds'],action=self.actions,api=details.get('api'))
            self.input_api_seconds=getattr(self,'input_api_seconds',0)+details['seconds']
        with self.performance.span('action_audit','io',action=self.actions,analysis_id=details.get('analysis_id')):
            self.report.add(stage,state=self.state.value,**details)

    def reconcile_operation(self,observations,source):
        if self.outcome_journal and self.scene_record and self.scene_record.get('pending'):
            record=self.outcome_journal.reconcile(self.scene_record,observations,source)
            self.scene_record['last_outcome']=record
            if self.scene_record['pending'].get('batch'):
                missed=[p for p,s in zip(record['points'],record['states']) if s in ('available','locked')]
                if missed:
                    self.attempted=[p for p in self.attempted if list(p) not in missed]
                    self.scene_record['attempted']=[list(p) for p in self.attempted]
                    self.trace('group_missed_revalidated',points=missed,stamps=record['stamps'],purchase_credit=0,automatic_batch_retry=False)
            self.trace('operation_reconciled',**record)
            self.click_lifecycle.record('VERIFY',canonical_operation_id=record['operation_id'],points=record['points'],
                states=record['states'],stamps=record['stamps'],result='Purchased' if record['confirmed_nodes'] else 'EntityConsumed' if record['absorbed_nodes'] else 'FailedWithVerifiedNoPurchase')
            return record

    def bind_click_operation(self):
        if self.scene_record and self.scene_record.get('pending'):
            from src.core.action_outcomes import operation_id
            ident=operation_id(self.scene_record);op=self.click_lifecycle.current.get()
            if op:op['canonical_id']=ident
            self.click_lifecycle.record('CANONICAL_BIND',canonical_operation_id=ident)

    def emit(self,state,message='',packet=None):
        self.state=state; self.event(state,message,packet)
        if state in (State.PAUSED,State.ERROR):self.performance.levels.pause(message)
        if message not in ('','hide_for_dialog','restore_window'):
            self.trace('state',message=message)

    def halt(self):
        self.stop.set()
        try: self.mouse.release()
        except ScreenError as exc: self.trace('release_failed',reason=exc.code,detail=exc.detail)

    def toggle_pause(self):
        if self.pause.is_set(): self.pause.clear()
        else:
            self.pause.set()
            try: self.mouse.release()
            except ScreenError as exc:
                self.stop.set(); self.trace('release_failed',reason=exc.code,detail=exc.detail)

    def wait(self,seconds):
        start=time.perf_counter()
        try:
            if self.stop.wait(seconds): raise InterruptedError
        finally:
            seconds=time.perf_counter()-start;stage=getattr(self,'wait_stage','poll_wait')
            self.performance.add(stage,seconds,'wait',action=self.actions,started_at=start,ended_at=start+seconds)
            self.performance.levels.wait(stage,seconds)

    def gate(self):
        if self.stop.is_set(): raise InterruptedError
        self.live_safety()
        self.check_budget()
        while self.pause.is_set():
            self.check_budget()
            self.emit(State.PAUSED)
            self.wait(.1)

    def check_budget(self):
        if time.monotonic()>getattr(self,'runtime_deadline',float('inf')):raise ScreenError('runtime_limit')

    def check_input_budget(self):
        if self.settings.max_input_commands is not None and self.attempts>=self.settings.max_input_commands:
            raise ScreenError('action_limit')

    def purchase_input(self,point,duration,check,frame,x,y,allow_repeats=False):
        """One pulse. A further command requires verified no-purchase evidence."""
        if hasattr(getattr(self.screen,'backend',None),'close_probe'):self.screen.backend.close_probe()
        self.check_input_budget()
        old_settle=getattr(self.mouse,'settle_seconds',.1)
        def guarded():
            if not getattr(self.mouse,'held',False):self.check(frame,x,y)
            return check()
        try:
            if allow_repeats:self.trace('blind_repeat_suppressed',reason='verification_required')
            self.gate();self.check_input_budget();self.mouse.settle_seconds=.1
            self.attempts+=1;self.performance.levels.input()
            self.mouse.purchase(point,duration,self.stop,guarded)
            if self.stop.is_set() or self.pause.is_set():raise InterruptedError
        finally:
            self.mouse.settle_seconds=old_settle
            self.mouse.release()

    def live_safety(self):
        if (getattr(self,'burst_inputs_active',False)
                and getattr(getattr(self.screen,'backend',None),'_probe_capture',None) is not None):
            raise ScreenError('burst_capture_forbidden','background_probe_stream_active')
        if self.evidence_writer and self.evidence_writer.failures():
            raise ScreenError('evidence_write_failed',str(self.evidence_writer.failures()))
        if not self.settings.dry_run and (not self.hotkeys_ready or (self.input_ready_check and not self.input_ready_check())):
            raise ScreenError('hotkey_registration_failed')

    def poll(self):
        # Processing time already separates real captures. Keep a minimum
        # capture interval instead of adding another fixed sleep afterwards.
        gap=self.settings.poll_interval-(time.monotonic()-getattr(self,'last_capture_at',time.monotonic()))
        self.wait(max(0,gap))

    def recover_window_geometry(self,current):
        """Discard prepared coordinates before observing a moved idle window."""
        if (getattr(self,'calibrating',False) or self.pending_action or getattr(self.mouse,'held',False)
                or getattr(self,'burst_inputs_active',False)
                or (self.persisted_scene or {}).get('pending') or (self.scene_record or {}).get('pending')):
            raise ScreenError('window_changed','paid_operation_geometry_unresolved')
        count=getattr(self,'geometry_recoveries',0)+1
        if self.recovery:
            if self.recovery.charge(self.progress_mark()):raise ScreenError('window_changed','geometry_recovery_limit')
        elif count>3:raise ScreenError('window_changed','geometry_recovery_limit')
        self.geometry_recoveries=count
        self.mouse.release()
        if self.scene_record and not self.persisted_scene:
            import copy
            self.persisted_scene=copy.deepcopy(self.scene_record)
        self.signature=current.signature
        self.reset_prepared_state('window_geometry_changed',window_changed=True)
        self.trace('window_geometry_recovery',current_window=asdict(current),paid_inputs=0,attempt=count)
        self.emit(State.RECOVERING,'window_geometry_recovery')

    def reset_prepared_state(self,reason,window_changed=False):
        """Forget prepared coordinates and proofs. Attempts and pending are kept."""
        self.screen.dialog_check_needed=True
        self.confirmation_capture=None
        self.saved_scene_observation=None;self.saved_scene_stable_count=0;self.saved_geometry_match=False
        self.automatic_proof=None;self.native_chain_permit=None
        if window_changed:
            self.direct_price_observations={}
            self.completed_layout=None;self.transition_verified=False
        if hasattr(self.screen,'reset_region'):self.screen.reset_region()
        if hasattr(self.detector,'invalidate'):self.detector.invalidate()
        if hasattr(self.opening,'cancel'):self.opening.cancel(reason)

    def park_cursor_off_web(self):
        """A resting cursor highlights a path and dims nodes. Moves only, no click."""
        backend=getattr(self.screen,'backend',None)
        if backend is None or not hasattr(self.mouse,'park') or not hasattr(backend,'owns_point'):return
        try:
            window=backend.info();point=(window.client.x+8,window.client.y+window.client.height//2)
            if not window.foreground or not backend.owns_point(window,point):return
            def check():
                self.gate();current=backend.info()
                if current.signature!=window.signature or not current.foreground:raise ScreenError('game_focus_lost')
            self.mouse.park(point,self.stop,check)
            self.trace('start_cursor_parked',point=point,clicks=0)
        except ScreenError as exc:
            self.trace('start_cursor_park_skipped',reason=exc.code,clicks=0)

    def progress_mark(self):
        return (self.actions,self.transitions,self.unconfirmed_level_actions,self.prestige_actions)

    def unresolved_operation(self):
        for scene in (self.persisted_scene,self.scene_record):
            pending=(scene or {}).get('pending')
            if pending:return pending
        return dict(unknown=True) if self.pending_action else None

    def readonly_window_allowed(self):
        """A new hwnd/pid may be re-read; changed geometry needs the user."""
        backend=getattr(self.screen,'backend',None)
        memory=self.persisted_scene if (self.persisted_scene or {}).get('pending') else self.scene_record
        if backend is None or not memory:return backend is None
        current=backend.info();window=asdict(current);old=memory.get('window',{})
        if any(old.get(key)!=window[key] for key in ('client','monitor','dpi')):return False
        self.signature=current.signature
        return True

    def enter_readonly_resolution(self):
        """Same state as a new Start holding a saved pending scene: observe only."""
        import copy
        if (self.persisted_scene or {}).get('pending'):
            self.pending_action=False
            return True
        pending=(self.scene_record or {}).get('pending')
        if not pending:return False
        self.persisted_scene=copy.deepcopy(self.scene_record)
        self.persisted_scene['attempted']=[list(p) for p in self.attempted]
        self.pending_action=False
        self.pending_scene_confirmations=0;self.saved_chain_signature=None
        self.readonly_resolution=dict(operation_id=pending.get('operation_id'),
            prior=len((pending.get('chain_outcome') or {}).get('confirmed',[])))
        self.trace('recovery_readonly_recheck',operation_id=pending.get('operation_id'),paid_inputs=0,automatic_retry=False)
        return True

    def credit_readonly_resolution(self):
        resolution=self.readonly_resolution
        if not resolution:return
        self.readonly_resolution=None
        record=(self.scene_record or {}).get('last_outcome') or {}
        if resolution['operation_id'] and record.get('operation_id')==resolution['operation_id']:
            added=max(0,record.get('confirmed_nodes',0)-resolution['prior'])
            self.actions+=added
            self.trace('recovery_readonly_reconciled',operation_id=resolution['operation_id'],added_confirmed=added,paid_inputs=0,automatic_retry=False)

    def await_game(self,exc):
        """No input while DBD is away. Resume only after a visible countdown."""
        sup=self.recovery;backend=getattr(self.screen,'backend',None)
        if backend is None:return False
        from src.core.recovery_supervisor import WAIT_CODES
        started=time.monotonic();deadline=started+sup.wait_limit_seconds
        self.event(self.state,'restore_window',None)
        self.wait_stage='game_return_wait'
        def foreground():
            try:return backend.info().foreground
            except ScreenError as error:
                if error.code not in WAIT_CODES:raise
                return False
        try:
            while True:
                self.emit(State.RECOVERING,'recovery_waiting_game',dict(reason=exc.code))
                while not foreground():
                    self.gate()
                    if time.monotonic()>=deadline:
                        self.trace('recovery_exhausted',reason=exc.code,limit='recovery_wait_limit',paid_inputs=0)
                        exc.detail=(exc.detail+';' if exc.detail else '')+'recovery_wait_limit'
                        return False
                    self.wait(.2)
                end=time.monotonic()+sup.countdown_seconds;shown=None;interrupted=False
                while (remaining:=end-time.monotonic())>0:
                    self.gate()
                    if not foreground():
                        interrupted=True;break
                    if math.ceil(remaining)!=shown:
                        shown=math.ceil(remaining)
                        self.emit(State.SCANNING,'prepare_countdown',{'seconds':shown})
                    self.wait(min(.1,remaining))
                if not interrupted:break
                self.trace('recovery_countdown_interrupted',reason=exc.code,paid_inputs=0)
        finally:
            self.wait_stage='poll_wait'
            sup.exclude_wait(time.monotonic()-started)
        self.trace('recovery_game_returned',reason=exc.code,waited=time.monotonic()-started,paid_inputs=0)
        return True

    def supervise_recovery(self,exc,calibrate):
        """True: run a fresh read. False: the caller re-raises and pauses."""
        from src.core.recovery_supervisor import classify,PAUSE,PAUSE_DETAILS,WAIT
        sup=self.recovery
        if sup is None or calibrate or self.settings.dry_run:return False
        pending=self.unresolved_operation()
        if pending and pending.get('chain_blocked_reason'):return False
        kind=classify(exc.code,exc.detail)
        probes=getattr(self,'hover_probes',0);hovered=probes!=getattr(self,'recovery_probe_mark',0)
        self.recovery_probe_mark=probes
        if hovered and kind!=WAIT and not pending:
            # Retries may only re-read the screen. Re-entering would hover again.
            self.trace('recovery_exhausted',reason=exc.code,limit='recovery_hover_not_repeated',paid_inputs=0)
            exc.detail=(exc.detail+';' if exc.detail else '')+'recovery_hover_not_repeated'
            return False
        if kind==PAUSE:
            # One read-only look at an unknown payment result, never a new input.
            operation=str((pending or {}).get('operation_id') or (pending or {}).get('point'))
            if not (pending and exc.code=='purchase_result_unknown' and exc.detail not in PAUSE_DETAILS
                    and sup.first_readonly(operation)):return False
        self.mouse.release()
        if kind==WAIT:
            if not self.await_game(exc):return False
            self.park_cursor_off_web()
        else:
            limit=sup.charge(self.progress_mark(),(exc.code,exc.detail))
            if limit:
                self.trace('recovery_exhausted',reason=exc.code,limit=limit,attempts=len(sup.attempts),paid_inputs=0)
                exc.detail=(exc.detail+';' if exc.detail else '')+limit
                return False
        if pending and not (self.readonly_window_allowed() and self.enter_readonly_resolution()):return False
        backend=getattr(self.screen,'backend',None)
        if hasattr(backend,'close_probe'):backend.close_probe()
        self.reset_prepared_state('supervisor_recovery')
        self.direct_prepared_single=None;self.retry_permit=None;self.retained_single_target=None
        self.preparation_replacement=None;self.preparation_changed_without_input=False
        self.native_chain_fallback=None;self.header_wait_since=None
        self.consecutive_errors=0;self.consecutive_preparation_changes=0
        attempt=len(sup.attempts)
        self.trace('supervisor_recovery',reason=exc.code,detail=exc.detail,attempt=attempt,unresolved=bool(pending),paid_inputs=0)
        self.emit(State.RECOVERING,'supervisor_recovery',dict(reason=exc.code,attempt=attempt,limit=sup.max_attempts))
        if kind!=WAIT:
            self.gate()
            self.wait_stage='recovery_backoff'
            try:self.wait(sup.delay())
            finally:self.wait_stage='poll_wait'
        return True

    def capture_frame(self,confirmation_of=None):
        if getattr(self,'burst_inputs_active',False):raise ScreenError('burst_capture_forbidden')
        self.gate()
        if not self.settings.dry_run and hasattr(self.screen,'backend'):
            current=self.screen.backend.info()
            if not current.foreground:raise ScreenError('game_focus_lost')
            if hasattr(self.screen.backend,'access_status'):
                access=self.screen.backend.access_status(current)
                if not access['allowed']:raise ScreenError(access['reason'])
            if self.signature is not None and current.signature!=self.signature:self.recover_window_geometry(current)
        started=time.perf_counter()
        self.last_capture_at=time.monotonic()
        new_capture=False
        try:
            frame=getattr(self,'confirmation_capture',None)
            self.confirmation_capture=None
            if frame is None:
                new_capture=True
                frame=self.screen.capture_confirmation(confirmation_of) if confirmation_of is not None else self.screen.capture()
        except ScreenError as exc:
            if self.diagnostics and getattr(self.screen,'last_raw',None) is not None:
                from src.core.models import Analysis
                image=self.screen.last_raw
                failed=Analysis(image.shape[1],image.shape[0],ScreenState.UNKNOWN,diagnostics=[exc.code])
                self.failed_frame=(image.copy(),failed,exc.code)
                self.diagnostics.record(image,failed,self.detector,phase='capture_failed',reason=exc.code,full=self.settings.detailed_diagnostics)
            raise
        finally:
            ended=time.perf_counter()
            self.performance.add('capture_total',ended-started,'aggregate',action=self.actions,started_at=started,ended_at=ended)
            for name,seconds in (getattr(self.screen,'last_timings',{}) if new_capture else {}).items():
                if name!='capture_total':self.performance.add(name,seconds,'capture' if name=='capture_pixels' else 'compute',action=self.actions,
                    **getattr(self.screen,'last_intervals',{}).get(name,{}))
        if time.monotonic()-frame.captured_at>2: raise ScreenError('stale_frame')
        if not frame.live: raise ScreenError('live_frame_required')
        if not self.settings.dry_run and not frame.window.foreground:raise ScreenError('game_focus_lost')
        self.last_capture_at=frame.captured_at
        if self.signature is None: self.signature=frame.window.signature
        elif self.signature!=frame.window.signature: raise ScreenError('window_changed')
        if self.settings.bloodpoint_checks:self.check_bloodpoint_observation(frame)
        return frame

    def check_bloodpoint_observation(self,frame):
        from src.core.bloodpoint_evidence import BloodpointEvidence
        if not hasattr(self,'bp_evidence'):self.bp_evidence=BloodpointEvidence()
        pending=(self.scene_record or {}).get('pending') or {}
        bound=getattr(self,'series_bp_debit_bound',None)
        if bound is None:
            if pending.get('chain'):bound=pending['chain'].get('total_bp')
            elif pending.get('automatic'):bound=None
            elif pending.get('prestige'):bound=pending.get('price')
            elif pending.get('batch'):bound=getattr(self.active_group,'price_upper_bound',None)
            else:bound=getattr(self,'direct_single_price_bound',None)
        try:self.bp_evidence.observe(frame,bound)
        except ScreenError as exc:
            self.trace('bp_reading_discarded',raw_value=frame.bloodpoints,last_proven=self.bp_evidence.last,
                       price_bound=bound,frame_stamp=frame.captured_at,reason=exc.code,paid_inputs=0)
            if self.report.path and frame.raw_image is not None and getattr(self,'bp_proof_count',0)<3:
                self.bp_proof_count=getattr(self,'bp_proof_count',0)+1
                from src.utils.images import write_image
                image=frame.raw_image;h,w=image.shape[:2]
                path=self.report.path.with_suffix('')/f'bp_unproven_{self.bp_proof_count}.png'
                write_image(path,image[:round(h*.15),round(w*.6):])
                self.trace('bp_reading_source',path=str(path),raw_value=frame.bloodpoints,frame_stamp=frame.captured_at)
            raise

    @measured('map_read')
    def read(self):
        first_sample=self.performance.sequence
        scans_before=self.opening.full_scans
        replans_before=getattr(getattr(self.opening,'route',None),'replans',0)
        matches_before=getattr(self.opening,'local_library_matches',0)
        frame=self.capture_frame()
        started=time.perf_counter()
        from src.core.fast_opening import OpeningFallback
        result=plan=None
        if self.opening.active:
            try:
                result=self.opening.analyze(frame,self.detector,self.priorities,self.settings.priority_mode,self.actions)
                plan=self.opening.plan(result,self.actions,self.attempted_nodes(frame,result))
                from src.core.early_unlock import center_mode
                if (plan.next_node is None and not center_mode(self,result,getattr(self,'calibrating',False))
                        and not self.pending_action and not self.persisted_scene
                        and any(n.state in (NodeState.AVAILABLE,NodeState.LOCKED) for n in result.nodes)):
                    # A compact crop can miss a newly exposed perk marker.
                    # Recheck this same fresh frame fully before declaring that
                    # the level has no action; never promote Unknown in memory.
                    raise OpeningFallback('remaining_action_unproven')
            except OpeningFallback as exc:
                self.opening.cancel(str(exc));self.trace('opening_fallback',reason=str(exc),
                    node=getattr(self.opening,'last_checked_node',None),
                    state_node=getattr(self.opening,'last_state_node',None),
                    certificate=getattr(self.opening,'last_certificate',None))
                self.keep_fallback_frame(frame,str(exc))
                result=plan=None
        if result is None:
            result=self.detector.analyze_live(frame) if hasattr(self.detector,'analyze_live') else self.detector.analyze(frame.image,'live_game')
            self.opening.full_scans+=1
            from src.core.early_unlock import eligible
            staged_scene=False
            if self.persisted_scene and not self.pending_action:
                from src.core.level_transition import header_transition,pending_target_on_proven_next_level,known_glyph_transition,restarted_incomplete_scene,idle_forward_transition
                from src.core.scene_memory import same_window
                memory=self.persisted_scene
                if not same_window(memory,frame):
                    import copy
                    from dataclasses import asdict
                    current_window=asdict(frame.window)
                    compatible=all(memory.get('window',{}).get(k)==current_window[k] for k in ('client','monitor','dpi'))
                    if compatible:
                        comparison=copy.deepcopy(memory);comparison['window']=current_window
                        staged_scene=(known_glyph_transition(comparison,frame,result) or restarted_incomplete_scene(comparison,frame,result) or
                                      (not memory.get('pending') and idle_forward_transition(comparison,frame,result)))
                else:
                    staged_scene=(pending_target_on_proven_next_level(memory,frame,result) if memory.get('pending')
                        else header_transition(memory,frame,result) or not memory.get('attempted'))
                # This seeds vision only. persisted_scene still blocks Mouse
                # until recheck_saved_scene accepts two independent captures.
                persistent_saved=(getattr(self.opening,'persistent',False) and same_window(memory,frame))
                allowed=(NodeState.CENTER,NodeState.AVAILABLE,NodeState.LOCKED,NodeState.PURCHASED,NodeState.ABSORBED) if persistent_saved else (NodeState.CENTER,NodeState.AVAILABLE,NodeState.LOCKED)
                staged_scene=(staged_scene or persistent_saved) and all(n.state in allowed for n in result.nodes)
            persistent=getattr(self.opening,'persistent',False)
            if ((self.settings.experimental_persistent or (self.settings.fast_opening and not self.settings.dry_run)) and not getattr(self,'calibrating',False)
                    and ((not self.persisted_scene and (persistent or not self.attempted)) or staged_scene)
                    and (persistent or not self.pending_action)
                    and hasattr(self.detector,'memory') and (not eligible(self,result) or persistent)):
                if self.opening.begin(frame,result,self.detector,self.priorities,self.settings.priority_mode,self.actions):
                    excluded=() if staged_scene else self.attempted_nodes(frame,result)
                    if persistent and self.pending_action:self.opening.expected_purchases.update(excluded)
                    self.trace('opening_started',queue=[p.next_node for p in self.opening.queue],level=result.level,pending_scene_validation=bool(staged_scene))
                    plan=self.opening.plan(result,self.actions,excluded)
        detector_ended=time.perf_counter();detector_time=detector_ended-started
        absorbed=[frame.to_screen(n.x,n.y) for n in result.nodes if n.state==NodeState.ABSORBED]
        if self.entity_points is not None:
            unseen=[p for p in absorbed if not any(math.dist(p,q)<8 for q in self.entity_points)]
            self.entity_actions+=len(unseen);self.entity_points.extend(unseen)
        else:self.entity_points=list(absorbed)
        excluded=self.attempted_nodes(frame,result)
        if plan is None:
            with self.performance.span('route',action=self.actions):plan=plan_route(result,self.priorities,self.settings.priority_mode,excluded)
        result.details['opening_counters']=self.opening.summary()
        if self.shadow:
            with self.performance.span('shadow_planner',action=self.actions):
                decision=self.shadow.observe(frame,result,self.priorities,self.settings.priority_mode,excluded,getattr(self.detector,'memory',None))
            result.details['shadow_planner']=decision
            self.trace('shadow_decision',analysis_id=result.analysis_id,decision=decision)
        if excluded:self.trace('attempted_routes_excluded',nodes=sorted(excluded),analysis_id=result.analysis_id)
        result.details['scan_kind']='FULL_SCAN' if self.opening.full_scans>scans_before else 'FAST_VERIFY'
        result.details['route_update']='LOCAL_REPLAN' if getattr(getattr(self.opening,'route',None),'replans',0)>replans_before else 'ROUTE_REUSE'
        if self.diagnostics:
            with self.performance.span('diagnostic_record','io',action=self.actions):
                self.diagnostics.record(frame.image,result,self.detector,frame,plan,phase=self.state.value,full=self.settings.detailed_diagnostics)
        self.recent_frames.append((frame,result,plan))
        self.performance.levels.read(frame,result,self.opening.full_scans>scans_before,
            getattr(getattr(self.opening,'route',None),'replans',0)-replans_before,
            getattr(self.opening,'local_library_matches',0)-matches_before)
        if result.screen_state==ScreenState.BLOODWEB:self.last_bloodweb=(frame,result)
        # Dialogs appear around level transitions. Mid-level, with two or more
        # nodes left and nothing unresolved, the next capture may skip that check.
        remaining=sum(n.state in (NodeState.AVAILABLE,NodeState.LOCKED) for n in result.nodes)
        self.screen.dialog_check_needed=not (result.screen_state==ScreenState.BLOODWEB and remaining>=2
            and type(result.level) is int and result.level_confidence>=.95)
        self.performance.add('detector_total',detector_time,'aggregate',analysis_id=result.analysis_id,action=self.actions,
            started_at=started,ended_at=detector_ended,scan_kind=result.details['scan_kind'])
        for name,seconds in result.details.get('timings',{}).items():
            if name!='detector_total':self.performance.add(name,seconds,'aggregate' if name in ('geometry_cache_check','recognition_stage1','recognition_stage2','memory_update') else 'compute',analysis_id=result.analysis_id,action=self.actions)
        for sample in self.performance.samples:
            if sample['sequence']>=first_sample:sample['analysis_id']=result.analysis_id
        self.trace('analysis',frame=frame.metadata(),screen=result.screen_state.value,next_node=plan.next_node,
                scan_kind=result.details['scan_kind'],route_update=result.details['route_update'],
                local_library_matches=getattr(self.opening,'local_library_matches',0)-matches_before,
                nodes=[dict(id=n.id,x=n.x,y=n.y,type=n.type.value,state=n.state.value,
                    geometry=n.geometry_confidence,state_score=n.state_confidence,icon=n.icon_id,reason=n.match_reason) for n in result.nodes],analysis_id=result.analysis_id)
        self.event(self.state,'',(frame,result,plan))
        return frame,result,plan

    def keep_fallback_frame(self,frame,reason):
        """Optional background PNG of a fallback frame, at most 40 per run."""
        count=getattr(self,'fallback_frames_kept',0)
        if count>=40 or not self.evidence_writer or not self.report.path:return
        self.fallback_frames_kept=count+1
        import json
        from src.utils.images import write_image
        folder=self.report.path.with_suffix('')/'opening_fallbacks';image=frame.image.copy()
        name=f'{count:03d}_{reason}'
        meta=dict(reason=reason,frame=frame.metadata(),node=getattr(self.opening,'last_checked_node',None),
            state=getattr(self.opening,'last_checked_state',None))
        def write():
            folder.mkdir(parents=True,exist_ok=True);write_image(folder/(name+'.png'),image)
            (folder/(name+'.json')).write_text(json.dumps(meta,default=str),encoding='utf-8')
        self.evidence_writer.submit(('opening_fallback',count),write)

    def attempted_nodes(self,frame,result):
        # Positions are physical, so re-cropping the same ROI cannot bypass
        # the block. They remain blocked until a positively proven new web.
        return {n.id for n in result.nodes if n.state not in (NodeState.CENTER,NodeState.PURCHASED) and
                any(math.dist(frame.to_screen(n.x,n.y),p)<max(8,n.radius*.5) for p in self.attempted)}

    def save_failure_evidence(self,reason):
        if not self.diagnostics:return
        # Heavy images are written after input has stopped, never on the normal
        # successful-purchase path. The compact action audit remains continuous.
        with self.performance.span('failure_diagnostics','aggregate',action=self.actions):
            for frame,result,plan in self.recent_frames:
                import copy
                from dataclasses import fields
                from src.core.models import Analysis
                evidence=Analysis(**{f.name:copy.deepcopy(getattr(result,f.name)) for f in fields(Analysis)})
                evidence.details['parent_analysis_id']=result.analysis_id
                evidence.details['attempted_screen_points']=[list(p) for p in self.attempted]
                with self.performance.span('failure_record','io',action=self.actions):
                    self.diagnostics.record(frame.image,evidence,self.detector,frame,plan,phase='failure',reason=reason,full=self.settings.detailed_diagnostics)
                self.trace('failure_evidence',analysis_id=evidence.analysis_id,parent_analysis_id=result.analysis_id)
            failed=getattr(self,'failed_frame',None)
            if failed:
                image,result,code=failed
                with self.performance.span('failure_record','io',action=self.actions):
                    self.diagnostics.record(image,result,self.detector,phase='capture_failed',reason=code,full=self.settings.detailed_diagnostics)

    @measured('recover_read')
    def recover_read(self,deadline=None,final_transition=False,retry_stale=False):
        deadline=deadline or time.monotonic()+15
        stale_retries=0
        hover_parked=False
        while True:
            recovery_start=time.perf_counter()
            try:
                value=self.read()
                if value[1].screen_state!=ScreenState.UNKNOWN and not value[1].details.get('transient_overlay'):
                    self.consecutive_errors=0;return value
                reason='item_tooltip' if value[1].details.get('transient_overlay') else 'roi_invalid' if 'roi_invalid' in value[1].diagnostics else 'node_detection_failed' if value[1].nodes else 'bloodweb_not_found'
            except ScreenError as exc:
                if exc.code=='stale_frame' and retry_stale and stale_retries<2:
                    stale_retries+=1
                    self.trace('stale_frame_discarded',reason=exc.code,retry=stale_retries)
                elif exc.code not in ('bloodweb_not_found','roi_invalid','item_tooltip','frame_unstable','bloodweb_occluded'): raise
                reason=exc.code
                if reason in ('bloodweb_not_found','bloodweb_occluded'):
                    from src.core.reward_popups import dismiss_if_present
                    if dismiss_if_present(self):
                        # The notice is gone or going; its time is not a recovery failure.
                        self.consecutive_errors=0;deadline=max(deadline,time.monotonic()+5)
                        self.wait(.3);continue
                if final_transition and reason=='bloodweb_not_found':raise ScreenError('bloodweb_transition_started') from exc
            self.emit(State.RECOVERING,reason)
            if reason=='item_tooltip' and not hover_parked and not self.settings.dry_run and hasattr(self.screen,'park_hover'):
                hover_parked=self.screen.park_hover(self.mouse,self.stop,self.gate)
                if hover_parked:self.trace('hover_recovery_park',clicks=0)
            self.consecutive_errors+=1
            if reason not in ('item_tooltip','frame_unstable','stale_frame'):
                if hasattr(self.screen,'reset_region'):self.screen.reset_region()
                if hasattr(self.detector,'invalidate'):self.detector.invalidate()
            self.performance.add('recovery_check',time.perf_counter()-recovery_start,'aggregate',action=self.actions,reason=reason)
            if time.monotonic()>=deadline: raise ScreenError('recovery_timeout',reason)
            if self.consecutive_errors>=self.settings.max_consecutive_errors:raise ScreenError('recovery_timeout','consecutive_errors:'+reason)
            self.wait_stage='tooltip_wait' if reason=='item_tooltip' else 'animation_wait'
            gap=.5-(time.monotonic()-getattr(self,'last_capture_at',time.monotonic()))
            self.wait(max(0,gap));self.wait_stage='poll_wait'

    @measured('wait_next_level')
    def wait_next_bloodweb(self,frame,result):
        from src.core.scene_memory import record_scene
        from src.core.level_transition import new_level
        reference=record_scene(frame,result,self.attempted)
        self.screen.dialog_check_needed=True
        self.mouse.release();self.emit(State.WAIT_NEXT_BLOODWEB,'loading_next_bloodweb')
        if hasattr(self.screen,'reset_layout'):self.screen.reset_layout()
        elif hasattr(self.screen,'reset_region'):self.screen.reset_region()
        if hasattr(self.detector,'invalidate'):self.detector.invalidate()
        if hasattr(self.screen,'fast_center_retry'):self.screen.fast_center_retry=True
        deadline=time.monotonic()+min(15,self.settings.refresh_timeout);previous=None;stable_count=0;stale_retries=0
        started=time.perf_counter()
        try:
            while time.monotonic()<deadline:
                self.gate();self.wait_stage='level_transition_wait'
                self.wait(max(0,.2-(time.monotonic()-getattr(self,'last_capture_at',time.monotonic()))));self.wait_stage='poll_wait'
                try:fresh,web,plan=self.read()
                except ScreenError as exc:
                    if exc.code=='stale_frame' and stale_retries<2:
                        stale_retries+=1
                        self.trace('stale_frame_discarded',reason=exc.code,retry=stale_retries,source='level_transition')
                        previous=None;stable_count=0;continue
                    if exc.code not in ('bloodweb_not_found','roi_invalid','frame_unstable','item_tooltip'):raise
                    self.trace('transition_observation',reason=exc.code);previous=None;stable_count=0;continue
                self.gate()
                if time.monotonic()>=deadline:raise ScreenError('next_bloodweb_timeout')
                if self.settings.bloodpoint_checks and fresh.bloodpoints==0:raise ScreenError('insufficient_bp')
                if web.screen_state==ScreenState.TUTORIAL:
                    from src.core.modal_recovery import dismiss_tutorial
                    fresh,web,plan=dismiss_tutorial(self,fresh,web,deadline=deadline)
                if web.screen_state in (ScreenState.PRESTIGE,ScreenState.PRESTIGE_READY,ScreenState.REWARD):
                    if web.screen_state==ScreenState.PRESTIGE_READY and self.settings.auto_prestige and self.prestige_approved:
                        self.emit(State.PRESTIGE,'prestige_ready')
                    else:self.emit(State.MANUAL_ACTION,'player_action_required')
                    # Hand the certified special screen back to run(). Waiting
                    # here used to swallow PRESTIGE_READY until timeout.
                    current=(fresh,web,None)
                    stable_count=stable_count+1 if self.stable_scene(previous,current) else 1;previous=current
                    if stable_count>=2:return fresh,web,plan
                    continue
                self.emit(State.WAIT_NEXT_BLOODWEB,'loading_next_bloodweb')
                if not new_level(reference,fresh,web):previous=None;stable_count=0;continue
                target=next((n for n in web.nodes if n.id==plan.next_node),None)
                current=(fresh,web,target)
                stable_count=stable_count+1 if self.stable_scene(previous,current) else 1;previous=current
                if stable_count<2:continue
                self.attempted.clear();self.completed_layout=None;self.transition_verified=False
                # Persist the twice-observed new map even if Stop arrives
                # before its first purchase. None left the GUI's older saved
                # pending scene untouched and resurrected it on the next Start.
                from src.core.scene_memory import record_scene
                prior_outcome=(self.scene_record or {}).get('last_outcome')
                self.scene_record=record_scene(fresh,web,[])
                if prior_outcome:self.scene_record['last_outcome']=prior_outcome
                self.persisted_scene=None
                self.trace('new_level_confirmed',analysis_id=web.analysis_id,observations=stable_count,source='wait_next_bloodweb',level=web.level)
                self.emit(State.SCANNING,'continue_upgrading')
                return fresh,web,plan
            raise ScreenError('next_bloodweb_timeout')
        finally:self.performance.add('next_bloodweb_wait',time.perf_counter()-started,'aggregate',action=self.actions)

    def finish_level_attempt(self,frame,result):
        newframe,newresult,plan=self.wait_next_bloodweb(frame,result)
        self.pending_action=False;self.unconfirmed_level_actions+=1
        if self.scene_record:
            self.scene_record['pending']=None
            if newresult.screen_state==ScreenState.PRESTIGE_READY:
                self.scene_record['terminal_transition']={'screen':'prestige_ready','level':50,
                    'stamps':[f.captured_at for f,r,p in list(self.recent_frames)[-2:]]}
        self.last_purchase_outcome='level_changed_unconfirmed'
        self.trace('purchase_outcome',outcome=self.last_purchase_outcome,purchase_confirmed=False,analysis_id=newresult.analysis_id,
            test_budget_used=self.actions+self.unconfirmed_level_actions)
        return newframe,newresult,plan

    @staticmethod
    def stable_scene(previous,current):
        if previous is None: return False
        af,ar,at=previous; bf,br,bt=current
        if af.window.signature!=bf.window.signature or ar.screen_state!=br.screen_state: return False
        if af.captured_at==bf.captured_at:return False
        if ar.level is not None and br.level is not None and ar.level!=br.level:return False
        if bool(at)!=bool(bt):return False
        if at and (at.state!=bt.state or (at.icon_id and bt.icon_id and at.icon_id!=bt.icon_id) or
                   math.dist(af.to_screen(at.x,at.y),bf.to_screen(bt.x,bt.y))>max(5,at.radius*.2)): return False
        if not ar.nodes or not br.nodes: return ar.ok_box==br.ok_box
        if max(len(ar.nodes),len(br.nodes))>min(len(ar.nodes),len(br.nodes))*1.4: return False
        matches=sum(any(math.dist(af.to_screen(a.x,a.y),bf.to_screen(b.x,b.y))<max(6,a.radius*.25)
                        for b in br.nodes) for a in ar.nodes)
        return matches>=max(1,len(ar.nodes)*.7)

    def check(self,frame,x,y):
        if self.stop.is_set() or self.pause.is_set(): raise InterruptedError
        self.live_safety()
        self.check_budget()
        try:
            # The explicit no-capture burst has a separate four-second lease;
            # ordinary individual inputs retain their original two seconds.
            point=(self.screen.validate_click(frame,x,y,max_age=4) if getattr(self,'burst_inputs_active',False)
                and hasattr(self.screen,'backend') else self.screen.validate_click(frame,x,y))
            self.trace('click_validation',point=point,frame=frame.metadata(),focus=True)
            return point
        except ScreenError as exc:
            self.trace('click_validation',reason=exc.code,detail=exc.detail,focus=exc.code!='game_focus_lost')
            raise

    def check_held(self,frame,x,y):
        if self.stop.is_set() or self.pause.is_set(): raise InterruptedError
        self.live_safety()
        self.check_budget()
        # While held, revalidate ownership/focus/geometry; the original down
        # event has already passed the fresh-frame check.
        if hasattr(self.screen,'backend'):
            return self.screen.validate_click(frame,x,y,max_age=None)
        return self.screen.validate_click(frame,x,y)

    @staticmethod
    def same_node(result,node):
        matches=[n for n in result.nodes if math.hypot(n.x-node.x,n.y-node.y)<max(8,node.radius*.55)]
        return min(matches,key=lambda n:math.hypot(n.x-node.x,n.y-node.y)) if matches else None

    @staticmethod
    def changed_layout(previous,current):
        af,ar=previous;bf,br=current
        if af.window.signature!=bf.window.signature:return False
        old=[n for n in ar.nodes if n.state!=NodeState.CENTER]
        new=[n for n in br.nodes if n.state!=NodeState.CENTER]
        if not old or not new:return False
        matches=sum(any(math.dist(af.to_screen(a.x,a.y),bf.to_screen(b.x,b.y))<max(6,a.radius*.25) for b in new) for a in old)
        new_matches=sum(any(math.dist(af.to_screen(a.x,a.y),bf.to_screen(b.x,b.y))<max(6,a.radius*.25) for a in old) for b in new)
        return matches<len(old)*.7 and new_matches<len(new)*.7

    def retire_terminal_single(self,frame,result):
        """A positive terminal web retires a saved single without purchase credit."""
        from dataclasses import asdict
        memory=self.persisted_scene or {};pending=memory.get('pending') or {}
        if (not pending or any(pending.get(k) for k in ('chain','batch','automatic','chain_blocked_reason')) or
                memory.get('level')!=50 or memory.get('level_confidence',0)<.95 or
                result.screen_state!=ScreenState.PRESTIGE_READY or result.level!=50 or result.level_confidence<.95 or
                not result.details.get('prestige_ready') or not frame.live or frame.probe_only or not frame.window.foreground or
                not self.approved or not self.hotkeys_ready or self.signature!=frame.window.signature or
                not self.input_ready_check or not self.input_ready_check()):
            self.saved_terminal_single=None;return False
        old=memory.get('window',{});current=asdict(frame.window)
        if any(old.get(k)!=current[k] for k in ('client','monitor','dpi')):return False
        if not 0<=time.monotonic()-frame.captured_at<=2:raise ScreenError('stale_frame')
        previous=getattr(self,'saved_terminal_single',None)
        self.saved_terminal_single=(frame,result,None)
        if not (previous and frame.captured_at>previous[0].captured_at and self.stable_scene(previous,(frame,result,None))):return False
        self.trace('saved_single_terminal_retired',previous_scene=memory,previous_purchase_confirmed=False,
            stamps=[previous[0].captured_at,frame.captured_at],old_pid=old.get('pid'),new_pid=frame.window.pid)
        self.persisted_scene=self.scene_record=None;self.pending_action=False;self.attempted.clear()
        self.unconfirmed_level_actions+=1
        return True

    def recheck_contained_chain_block(self,frame,result):
        """Review this specific geometric false alarm with no paid input."""
        import copy
        from src.core.scene_memory import same_window,chain_matching_node,chain_ring_certificate,chain_unexpected_points
        memory=self.persisted_scene;pending=memory.get('pending') or {};chain=pending.get('chain') or {}
        unexpected=(pending.get('chain_outcome') or {}).get('unexpected_purchases',[])
        def reject():raise ScreenError('chain_unexpected_purchase','saved_chain_contract_block')
        if (pending.get('chain_blocked_reason')!='chain_unexpected_purchase' or not unexpected or
                not frame.live or frame.probe_only or frame.confirmation_only or not frame.window.foreground or
                not same_window(memory,frame) or result.screen_state!=ScreenState.BLOODWEB or
                result.level!=memory.get('level') or result.level_confidence<.95 or
                type(frame.bloodpoints) is not int or type(memory.get('bp_before')) is not int or
                type(chain.get('total_bp')) is not int or memory['bp_before']-frame.bloodpoints!=chain['total_bp']):reject()
        if not 0<=time.monotonic()-frame.captured_at<=2:
            self.contained_block_observation=None
            retries=getattr(self,'contained_block_stale_retries',0)+1
            self.contained_block_stale_retries=retries
            self.trace('stale_frame_discarded',source='chain_geometry_recheck',retry=retries,paid_inputs=0)
            if retries>2:raise ScreenError('stale_frame','chain_geometry_recheck_timeout')
            return False
        if chain_unexpected_points(memory,frame,result):reject()
        certificates=[]
        for point in chain.get('physical_points',[]):
            node=chain_matching_node(memory,frame,result,point)
            if not node or node.state!=NodeState.PURCHASED or node.state_confidence<.8:reject()
            certificate=chain_ring_certificate(memory,frame,node,point)
            if certificate:certificates.append(certificate)
        if (not certificates or any(not any(math.dist(p,c['point'])<5 for c in certificates) for p in unexpected)):reject()
        previous=getattr(self,'contained_block_observation',None)
        current=(frame,result,None);self.contained_block_observation=current
        if not previous or frame.captured_at<=previous[0].captured_at or not self.stable_scene(previous,current):
            self.trace('chain_geometry_block_recheck',paid_inputs=0,observations=1,certificates=certificates)
            return False
        review=copy.deepcopy(memory)
        original_reason=review['pending'].pop('chain_blocked_reason')
        review['pending']['chain_geometry_review']=dict(original_reason=original_reason,
            original_unexpected=unexpected,stamps=[previous[0].captured_at,frame.captured_at],
            paid_inputs=0,reservation_refunded=False)
        if not self.outcome_journal:reject()
        outcome=self.outcome_journal.reconcile(review,[(previous[0],previous[1]),(frame,result)],'chain_geometry_recheck')
        self.click_lifecycle.record('VERIFY',canonical_operation_id=outcome['operation_id'],
            points=outcome['points'],states=outcome['states'],stamps=outcome['stamps'],result='Purchased')
        review['last_outcome']=outcome;review['pending']=None
        self.scene_record=review;self.persisted_scene=None;self.pending_action=False
        self.trace('chain_geometry_block_resolved',operation_id=outcome['operation_id'],
            original_reason=original_reason,paid_inputs=0,reservation_refunded=False,certificates=certificates)
        return True

    def recheck_saved_scene(self,frame,result):
        from src.core.scene_memory import same_window,layout_changed
        from src.core.purchase_outcome import physical_node
        memory=self.persisted_scene
        blocked=(memory.get('pending') or {}).get('chain_blocked_reason')
        if blocked=='chain_unexpected_purchase':return self.recheck_contained_chain_block(frame,result)
        if blocked:raise ScreenError(blocked,'saved_chain_contract_block')
        replacement=not same_window(memory,frame)
        comparison=memory
        if replacement:
            from dataclasses import asdict
            from src.core.window_geometry import WindowGeometryManager
            import copy
            old=memory.get('window',{});current_window=asdict(frame.window)
            # A new process does not prove a new web. Compare only same physical
            # geometry after the current Start has established its own window.
            proof=self.calibration_window_proof
            calibration_context=bool(getattr(self,'calibrating',False) and proof and
                self.calibration_target and proof['target']==self.calibration_target and
                self.calibration_target[2]==frame.window.signature and self.calibration_target[3]==frame.roi and
                0<=time.monotonic()-proof['created']<=60 and frame.live and not frame.probe_only and
                frame.window.foreground and self.input_ready_check and self.input_ready_check())
            if (self.signature!=frame.window.signature or not (self.approved or calibration_context) or not self.hotkeys_ready):
                raise ScreenError('window_changed','saved_purchase_scene_window')
            geometry_changed=any(old.get(k)!=current_window[k] for k in ('client','monitor','dpi'))
            if geometry_changed and (memory.get('pending') or calibration_context):
                raise ScreenError('window_changed','saved_pending_geometry_requires_evidence')
            comparison=WindowGeometryManager.project(memory,frame.window)
        previous=getattr(self,'saved_scene_observation',None)
        if previous and frame.captured_at<=previous[0].captured_at:return False
        current=(frame,result,None)
        count=getattr(self,'saved_scene_stable_count',0)+1 if self.stable_scene(previous,current) else 1
        self.saved_scene_observation=current;self.saved_scene_stable_count=count
        if not memory.get('pending') and not getattr(self,'calibrating',False):
            from src.core.scene_memory import foreign_web
            reason=foreign_web(comparison,frame,result)
            if reason:
                # No unresolved input exists, so another web's clicks protect nothing
                # here; two fresh reads must agree before they are dropped.
                if count<2:return False
                self.trace('saved_scene_other_web_retired',reason=reason,saved_level=memory.get('level'),level=result.level,
                    attempted=memory.get('attempted'),purchase_credit=0,paid_inputs=0,analysis_id=result.analysis_id)
                self.scene_record=None;self.persisted_scene=None;self.pending_action=False;self.attempted.clear()
                self.saved_scene_observation=None;self.saved_scene_stable_count=0;self.saved_geometry_match=False
                return True
        if replacement and not memory.get('pending') and self.approved:
            from src.core.window_geometry import WindowGeometryManager
            matches=WindowGeometryManager.resolved_scene_matches(comparison,frame,result)
            if (not matches and not getattr(self,'calibrating',False) and frame.live and not frame.probe_only
                    and frame.window.foreground and not frame.confirmation_only):
                # No unresolved input exists, so an old map protects nothing.
                # Fresh states decide what is bought; nothing is credited.
                self.trace('saved_scene_discarded',previous_window=memory.get('window'),current_window=asdict(frame.window),
                    level=memory.get('level'),attempted=memory.get('attempted'),purchase_credit=0,paid_inputs=0)
                self.scene_record=None;self.persisted_scene=None;self.attempted.clear()
                self.saved_scene_observation=None;self.saved_scene_stable_count=0;self.saved_geometry_match=False
                self.geometry_recoveries=0
                return True
            signature=tuple((n.state,n.icon_id) for old in comparison.get('nodes',[]) for n in result.nodes
                if math.dist(old['point'],frame.to_screen(n.x,n.y))<max(5,old['radius']*.2)) if matches else None
            prior_match=signature is not None and signature==getattr(self,'saved_geometry_match',None)
            self.saved_geometry_match=signature
            if matches:
                if not prior_match or count<2:return False
                from src.core.scene_memory import record_scene
                self.attempted=[tuple(p) for p in comparison.get('attempted',[])]
                rebound=record_scene(frame,result,self.attempted)
                if memory.get('last_outcome'):rebound['last_outcome']=memory['last_outcome']
                rebound['geometry_rebind']=dict(previous_window=memory['window'],observations=2,purchase_credit=0)
                self.trace('window_geometry_rebound',previous_window=memory['window'],current_window=comparison['window'],observations=2,paid_inputs=0)
                self.scene_record=rebound;self.persisted_scene=None
                self.geometry_recoveries=0
                return True
        from src.core.level_transition import new_level,header_transition,known_glyph_transition,restarted_incomplete_scene,pending_target_on_proven_next_level,pending_automatic_on_next_level,idle_forward_transition,level_header_moved
        pending=memory.get('pending')
        from src.core.scene_memory import transition_view
        observed=transition_view(memory) if not replacement else comparison
        changed=(known_glyph_transition(comparison,frame,result) or restarted_incomplete_scene(comparison,frame,result) or
            (not pending and (header_transition(comparison,frame,result) or idle_forward_transition(comparison,frame,result)))) if replacement else new_level(observed,frame,result)
        native_chain=bool(pending and pending.get('chain') and not pending.get('batch'))
        if native_chain and not replacement:
            from src.core.level_transition import pristine_next_level
            # A complete pristine next map is independent of the old terminal
            # slot. It retires input, not any unobserved per-node purchase.
            changed=pristine_next_level(memory,frame,result)
            key=pending.get('operation_id')
            previous_proof=getattr(self,'saved_chain_transition_proven',None)
            self.saved_chain_transition_proven=(key,changed)
            if changed and previous_proof!=(key,True):count=1;self.saved_scene_stable_count=1
        if pending and pending.get('batch') and changed:raise ScreenError('purchase_result_unknown','group_transition_unresolved')
        if replacement:
            # Both independent observations must prove the transition. Similar
            # geometry alone must not lend an unproven first frame its count.
            proven=getattr(self,'saved_scene_transition_proven',False)
            self.saved_scene_transition_proven=changed
            if changed and not proven:count=1;self.saved_scene_stable_count=1
            if count<2:return False
            if not changed:
                level=result.level if result.level is not None else frame.level
                if type(memory.get('level')) is int and type(level) is int and abs(level-memory['level'])>2 and result.level_confidence>=.95:
                    # A click saved far from this level cannot belong to this web; nearby
                    # levels stay unresolved because the header may be misread.
                    # (live 9.11: a survivor's old level 50 prestige on level 17).
                    self.trace('saved_scene_other_level_retired',saved_level=memory['level'],level=level,
                        pending=pending,purchase_credit=0,paid_inputs=0)
                    self.scene_record=None;self.persisted_scene=None;self.pending_action=False;self.attempted.clear()
                    self.saved_scene_observation=None;self.saved_scene_stable_count=0
                    return True
                raise ScreenError('window_changed','saved_purchase_scene_not_proven_new')
            self.trace('saved_scene_replaced',old_pid=memory['window'].get('pid'),new_pid=frame.window.pid,
                observations=count,previous_purchase_confirmed=False,analysis_id=result.analysis_id)
            memory=comparison
        if pending and changed and not replacement:
            automatic=pending_automatic_on_next_level(memory,frame,result)
            if automatic and not getattr(self,'saved_automatic_transition_proven',False):
                count=1;self.saved_scene_stable_count=1
            self.saved_automatic_transition_proven=automatic
            old_target=physical_node(frame,result,pending['point'],pending['radius'])
            center_pending=any(n['state']==NodeState.CENTER and math.dist(n['point'],pending['point'])<=5
                for n in memory.get('nodes',[]))
            if center_pending or pending.get('automatic'):changed=automatic
            # A surviving old target cannot prove a level transition. A new
            # glyph at the same location must itself be positively identified.
            elif not native_chain and old_target and (not pending.get('icon') or not old_target.icon_id or old_target.icon_id==pending['icon']):
                if not pending_target_on_proven_next_level(observed,frame,result):changed=False
        header_level_moved=bool(pending and not replacement and not changed and not native_chain and not pending.get('batch')
            and level_header_moved(memory,previous,result))
        if header_level_moved:
            # The pending click finished the level (or the Entity took the rest):
            # both new observations read the same new level. Retire it, never repeat it.
            changed=True
            self.trace('saved_scene_level_header_transition',old_level=memory.get('level'),new_level=result.level,
                operation_id=pending.get('operation_id'),purchase_credit=0,paid_inputs=0)
        reused_unknown_transition=bool(pending and not replacement and not pending.get('icon')
            and not any(pending.get(k) for k in ('chain','batch','automatic','chain_blocked_reason'))
            and header_transition(observed,frame,result) and known_glyph_transition(observed,frame,result))
        if reused_unknown_transition:
            key=pending.get('operation_id');prior_proof=getattr(self,'saved_single_transition_proven',None)
            self.saved_single_transition_proven=(key,bool(changed))
            if changed and prior_proof!=(key,True):count=1;self.saved_scene_stable_count=1
        if pending and not changed:
            if type(memory.get('level')) is int and result.level is None:
                self.pending_scene_confirmations=0
                self.trace('saved_scene_header_wait',paid_inputs=0,analysis_id=result.analysis_id)
                return False
            if (type(memory.get('level')) is int and type(result.level) is int and abs(result.level-memory['level'])>2
                    and result.level_confidence>=.95):
                # Same rule as for a new game window: a click saved far from this level
                # cannot belong to this web. Survivors share one saved scene, so after
                # switching survivor the old one's last click was restored here (live
                # 11.11: a level 50 centre click on another survivor's level 17).
                if count<2:return False
                self.trace('saved_scene_other_level_retired',saved_level=memory['level'],level=result.level,
                    pending=pending,purchase_credit=0,paid_inputs=0,same_window=True)
                self.scene_record=None;self.persisted_scene=None;self.pending_action=False;self.attempted.clear()
                self.saved_scene_observation=None;self.saved_scene_stable_count=0;self.pending_scene_confirmations=0
                return True
            if pending.get('chain') or pending.get('batch'):
                from src.core.scene_memory import chain_state_signature
                signature=(None if type(memory.get('level')) is int and result.level!=memory['level']
                    else chain_state_signature(memory,frame,result))
                resolved=signature is not None
                previous_signature=getattr(self,'saved_chain_signature',None)
                self.pending_scene_confirmations=(self.pending_scene_confirmations+1 if signature==previous_signature else 1) if resolved else 0
                self.saved_chain_signature=signature
            else:
                found=physical_node(frame,result,pending['point'],pending['radius'])
                resolved=found and found.state in (NodeState.PURCHASED,NodeState.ABSORBED) and found.state_confidence>=.75 and not(pending.get('icon') and found.icon_id and pending['icon']!=found.icon_id)
                self.pending_scene_confirmations=self.pending_scene_confirmations+1 if resolved else 0
            if self.pending_scene_confirmations<2:
                if count>=2 and not resolved:
                    if reused_unknown_transition:
                        key=pending.get('operation_id');retry=getattr(self,'saved_single_transition_retry',None)
                        if retry is None or retry[0]!=key:retry=(key,frame.captured_at,0)
                        retry=(key,retry[1],retry[2]+1);self.saved_single_transition_retry=retry
                        if frame.captured_at-retry[1]<min(4,self.settings.purchase_timeout) and retry[2]<6:
                            if retry[2]==1:
                                if hasattr(self.opening,'cancel'):self.opening.cancel('saved_single_transition_geometry')
                                if hasattr(self.screen,'reset_layout'):self.screen.reset_layout()
                            self.trace('saved_single_transition_retry',operation_id=key,attempt=retry[2],paid_inputs=0)
                            return False
                    # A dim absorbed ring can disappear from one Hough frame.
                    # Spend a bounded read-only window finding two actual
                    # per-node observations; never submit the saved input again.
                    if pending.get('chain') and not pending.get('automatic'):
                        key=pending.get('operation_id')
                        retry=getattr(self,'saved_chain_retry',None)
                        if retry is None or retry[0]!=key:
                            retry=(key,frame.captured_at,0)
                        retry=(key,retry[1],retry[2]+1);self.saved_chain_retry=retry
                        if frame.captured_at-retry[1]<min(4,self.settings.purchase_timeout) and retry[2]<6:
                            self.trace('saved_chain_evidence_retry',operation_id=key,attempt=retry[2],paid_inputs=0)
                            return False
                    raise ScreenError('purchase_result_unknown','previous_start_unresolved')
                return False
            self.scene_record=memory
            self.reconcile_operation([(previous[0],previous[1]),(frame,result)],'saved_scene_recheck')
            memory['pending']=None;self.emit(State.RECOVERING,'pending_purchase_resolved')
        if count<2:return False
        if changed:
            from src.core.level_transition import terminal_single_on_next_level
            if pending and terminal_single_on_next_level(memory,frame,result):
                self.trace('terminal_single_transition_retired',operation_id=pending.get('operation_id'),
                    stamps=[previous[0].captured_at,frame.captured_at],purchase_credit=0,paid_retry=False,reservation_refunded=False)
            if native_chain:
                self.trace('saved_chain_transition_retired',operation_id=pending.get('operation_id'),
                    stamps=[previous[0].captured_at,frame.captured_at],purchase_credit_for_missing_nodes=0,
                    paid_retry=False,reservation_refunded=False)
            if pending and (pending.get('automatic') or header_level_moved):
                self.unconfirmed_level_actions+=1
                self.trace('automatic_resume_transition',previous_purchase_confirmed=False,analysis_id=result.analysis_id)
            self.attempted.clear();self.transition_verified=False
            self.trace('new_level_confirmed',analysis_id=result.analysis_id,observations=count,source='persisted_scene_geometry',level=result.level)
            from src.core.scene_memory import record_scene
            memory=record_scene(frame,result,[])
        self.scene_record=memory;self.persisted_scene=None
        return True

    def try_turbo(self,frame,result,node=None,strict=False):
        if not self.settings.turbo_burst or not node or strict:return None
        if getattr(self,'turbo_unfresh_scene',None)==(frame.window.signature,result.level):
            self.turbo_suppressed=getattr(self,'turbo_suppressed',0)+1;return None
        scene=(frame.window.signature,result.level,tuple(self.priorities),self.settings.priority_mode)
        known=type(result.level) is int and result.level_confidence>=.95
        def reused():self.turbo_suppressed=getattr(self,'turbo_suppressed',0)+1
        mid_burst=self.settings.burst_mid_level
        if known and not mid_burst and getattr(self,'_turbo_closed_scene',None)==scene:
            reused();return None
        changed=any(n.state in (NodeState.PURCHASED,NodeState.ABSORBED) for n in result.nodes)
        if changed and self.settings.burst_mid_level:
            key=(frame.captured_at,id(frame),id(result),tuple(self.priorities),self.settings.priority_mode)
            if getattr(self,'_turbo_checked',None)==key:return None
            self._turbo_checked=key
            from src.core.turbo_burst import dispatch
            return dispatch(self,frame,result)
        if known and changed and getattr(self,'_turbo_evaluated_scene',None)==scene:
            self._turbo_closed_scene=scene;reused()
            self.trace('turbo_opening_closed',level=result.level,reason='opening_already_changed',paid_inputs=0)
            return None
        shape=tuple((n.id,n.state,n.type,n.icon_id,n.rarity,n.confidence>=.86,n.contour_score>=.68,
                     n.geometry_confidence>=.7,n.category_hint,n.event_offering,n.orange_background) for n in result.nodes)
        eligibility=(scene,shape,tuple(result.edges))
        if known and getattr(self,'_turbo_static_rejection',None)==eligibility:
            reused();return None
        key=(frame.captured_at,id(frame),id(result),tuple(self.priorities),self.settings.priority_mode)
        if getattr(self,'_turbo_checked',None)==key:return None
        self._turbo_checked=key
        from src.core.turbo_burst import dispatch
        self._turbo_last_reason=None
        outcome=dispatch(self,frame,result)
        if (not outcome and known and self._turbo_last_reason=='target_geometry_unproven'
                and getattr(self,'_direct_geometry_refreshed_scene',None)!=scene):
            # A forming opening may have a weak future ring. Reobserve once
            # before any hover/price preparation, never between burst inputs.
            self._direct_geometry_refreshed_scene=scene
            fresh=self.recover_read(retry_stale=True)
            self.preparation_replacement=fresh;self.preparation_changed_without_input=True
            self.trace('direct_opening_geometry_recheck',level=result.level,paid_inputs=0,
                old_stamp=frame.captured_at,new_stamp=fresh[0].captured_at,scan_kind=fresh[1].details.get('scan_kind'))
            raise ScreenError('purchase_preparation_changed')
        if known:
            self._turbo_evaluated_scene=scene
            if changed or outcome:self._turbo_closed_scene=scene
            elif self._turbo_last_reason in ('early_perk_entity_risk','identity_or_standard_price_unproven',
                    'target_geometry_unproven','five_known_nonperk_targets_unreachable','path_not_proven','would_finish_level_before_evidence','early_center_or_prestige'):
                self._turbo_static_rejection=eligibility
        return outcome

    def single_after_probe(self,selected_frame,selected_result,selected_node,fresh):
        self.retained_single_target=None
        frame,result,plan=fresh
        node=next((n for n in result.nodes if n.id==plan.next_node),None)
        if node is None:raise ScreenError('no_confident_action')
        from scripts.live_test_v54 import scene_key
        from scripts.native_price_v55 import single_scene_valid
        old_plan=plan_route(selected_result,self.priorities,self.settings.priority_mode)
        if selected_node and old_plan.reason in ('filler','PERSISTENT:filler'):
            key=scene_key(selected_frame,selected_result);key['single_filler_preparation']=True
            selected_point=selected_frame.to_screen(selected_node.x,selected_node.y)
            if single_scene_valid(key,frame,result,plan,selected_point):
                matches=[n for n in result.nodes if math.dist(frame.to_screen(n.x,n.y),selected_point)<=5]
                if len(matches)==1:
                    node=matches[0]
                    self.retained_single_target=(frame.window.signature,result.level,selected_point,frame.captured_at)
                    self.click_lifecycle.record('TARGET_RETAINED',target_id=node.id,point=selected_point,
                        reason='same_known_direct_filler_after_chain_probe')
        self.click_lifecycle.record('TARGET_VALIDATION',target_id=node.id,
            point=frame.to_screen(node.x,node.y),reason='native_chain_fallback_fresh_plan')
        return frame,result,node

    def recover_chain_readonly(self):
        """One cold, bounded observation pass; never submit the pending path."""
        import copy
        scene=self.scene_record or {};pending=scene.get('pending') or {}
        ident=pending.get('operation_id')
        if (not self.pending_action or not pending.get('chain') or pending.get('chain_blocked_reason')
                or not ident or self.stop.is_set() or ident in getattr(self,'late_chain_rechecks',set())):return None
        self.late_chain_rechecks=getattr(self,'late_chain_rechecks',set());self.late_chain_rechecks.add(ident)
        prior=set((pending.get('chain_outcome') or {}).get('confirmed',[]))
        self.persisted_scene=copy.deepcopy(scene)
        self.pending_scene_confirmations=0;self.saved_scene_observation=None;self.saved_scene_stable_count=0;self.saved_chain_signature=None
        if hasattr(self.opening,'cancel'):self.opening.cancel('chain_late_readonly')
        if hasattr(self.screen,'reset_region'):self.screen.reset_region()
        if hasattr(self.detector,'invalidate'):self.detector.invalidate()
        deadline=min(getattr(self,'runtime_deadline',float('inf')),time.monotonic()+min(4,self.settings.purchase_timeout))
        self.trace('chain_late_recheck',operation_id=ident,paid_inputs=0,automatic_retry=False)
        try:
            while time.monotonic()<deadline:
                self.gate();value=self.recover_read(deadline,retry_stale=True)
                if self.recheck_saved_scene(value[0],value[1]):
                    record=(self.scene_record or {}).get('last_outcome') or {}
                    added=max(0,record.get('confirmed_nodes',0)-len(prior))
                    self.actions+=added
                    self.trace('chain_late_reconciled',operation_id=ident,added_confirmed=added,paid_inputs=0,automatic_retry=False)
                    return value
                self.poll()
        except ScreenError as exc:
            if exc.code not in ('purchase_result_unknown','stale_frame'):raise
        return None

    @transaction('single_or_native_chain')
    def buy(self,frame,result,node=None,point=None,strict=False):
        # A native-chain preparation must not consume the pristine opening
        # before the explicitly selected five-input mode has had a chance.
        burst=self.try_turbo(frame,result,node,strict)
        if burst:return burst
        selected_frame,selected_result,selected_node=frame,result,node
        dispatch=getattr(self,'native_chain_dispatch',None)
        if dispatch and node:
            from src.core.direct_buy import prefer_direct_single
            if prefer_direct_single(self,frame,result,node):
                self.trace('chain_skipped',reason='known_single_target_direct_buy',paid_inputs=0)
                dispatch=None
        if dispatch and node and not strict:
            chained=dispatch(frame,result)
            if chained:
                self.native_chain_fallback=None
                return chained
            fresh=getattr(self,'native_chain_fallback',None);self.native_chain_fallback=None
            if fresh:
                frame,result,node=self.single_after_probe(selected_frame,selected_result,selected_node,fresh)
        if node and not strict and not self.settings.dry_run and hasattr(self,'prepare_direct_single'):
            frame,result,node=self.prepare_direct_single(frame,result,node)
        started=time.perf_counter()
        self.retry_permit=None
        try:
            try:return self._buy_once(frame,result,node,point,strict)
            except ScreenError as exc:
                if exc.code!='purchase_missed' or strict or not node:raise
            self.emit(State.RECOVERING,'purchase_recheck')
            self.pending_action=True
            if self.scene_record:self.scene_record['pending']=dict(point=list(frame.to_screen(node.x,node.y)),radius=node.radius,icon=node.icon_id)
            if hasattr(self.screen,'reset_region'):self.screen.reset_region()
            if hasattr(self.detector,'invalidate'):self.detector.invalidate()
            evidence=self.purchase_evidence;deadline=time.monotonic()+min(3,self.settings.purchase_timeout)
            available=0;last=None
            while time.monotonic()<deadline:
                self.poll();fresh,web,plan=self.recover_read(deadline)
                if fresh.window.signature!=frame.window.signature:raise ScreenError('window_changed')
                found,success,missed=evidence.observe(fresh,web,time.monotonic())
                if success:
                    self.reconcile_operation(evidence.red_observations,'worker_late')
                    self.pending_action=False;self.actions+=1;self.last_purchase_outcome='purchased_late'
                    from src.core.priority_targets import perk_eligible,PERK_TARGET
                    self.performance.levels.purchase(node.icon_id in self.priorities or (PERK_TARGET in self.priorities and perk_eligible(node)))
                    if self.scene_record:self.scene_record['pending']=None
                    self.trace('purchase_outcome',outcome='purchased_late',analysis_id=web.analysis_id)
                    return fresh,web,plan
                from src.core.purchase_outcome import unchanged_scene
                available=available+1 if missed and last and unchanged_scene(last,(fresh,web)) else 1 if missed else 0
                last=(fresh,web)
                if available>=2:
                    self.retry_permit=(evidence.point,fresh.to_screen(found.x,found.y),web.analysis_id)
                    self.trace('retry_revalidated',before=evidence.point,corrected=self.retry_permit[1],observations=available,analysis_id=web.analysis_id)
                    self.emit(State.RECOVERING,'purchase_retry_one')
                    try:return self._buy_once(fresh,web,found,None,False,retry=True)
                    except ScreenError as exc:
                        if exc.code=='purchase_missed':raise ScreenError('purchase_retry_failed') from exc
                        raise
            raise ScreenError('purchase_result_unknown','retry_recheck_inconclusive')
        finally:
            self.retry_permit=None
            self.screen.fast_center_retry=False
            self.performance.add('purchase_attempt_total',time.perf_counter()-started,'aggregate',action=self.actions,outcome=self.last_purchase_outcome)

    def _buy_once(self,frame,result,node=None,point=None,strict=False,retry=False):
        if (node is not None and not self.settings.dry_run and hasattr(self.screen,'capture_overlay')
                and (type(result.level) is not int or result.level_confidence<.95)):
            # Preparation can replace the run-loop's previously valid frame.
            # Require its own observed context before creating pending/input.
            raise ScreenError('frame_unstable','pre_input_level_header_unconfirmed')
        if self.pending_action and not retry and self.active_group is None:
            raise ScreenError('purchase_result_unknown','previous_input_unresolved')
        self.check_input_budget()
        if self.settings.max_actions is not None and self.actions+self.unconfirmed_level_actions+self.prestige_actions>=self.settings.max_actions:
            raise ScreenError('action_limit')
        if self.persisted_scene and not self.settings.dry_run:
            raise ScreenError('purchase_result_unknown','scene_validation_required')
        if node and not purchasable(node,result.nodes,result.edges): raise ScreenError('unsafe_node')
        if point and (not self.prestige_approved or result.screen_state not in (ScreenState.PRESTIGE,ScreenState.REWARD)):
            raise ScreenError('prestige_manual')
        if point and result.screen_state==ScreenState.PRESTIGE:
            # Legacy colour-only classification does not authorize a paid centre.
            # PRESTIGE_READY goes through its separate visual/price contract.
            raise ScreenError('prestige_price_unproven')
        if point and result.screen_state==ScreenState.REWARD:
            if not result.ok_box: raise ScreenError('unsafe_node')
            bx,by,bw,bh=result.ok_box
            if not (bx<=point[0]<bx+bw and by<=point[1]<by+bh): raise ScreenError('unsafe_node')
        self.trace('before_purchase',node=node.id if node else None,position=point or (node.x,node.y),
            before=node.state.value if node else result.screen_state.value)
        operation=self.click_lifecycle.current.get()
        if operation:
            current=dict(node=node.id if node else None,icon=node.icon_id if node else None,
                point=point or frame.to_screen(node.x,node.y),level=result.level,
                window=frame.window.signature,frame_stamp=frame.captured_at)
            old=operation['target'] or {};same_point=bool(old.get('point') and current['point'] and math.dist(old['point'],current['point'])<=5)
            same_target=(same_point and old.get('level')==current['level'] and old.get('window')==current['window']
                and not(old.get('icon') and current['icon'] and old['icon']!=current['icon']))
            if old!=current:self.click_lifecycle.record('TARGET_REVALIDATED' if same_target else 'TARGET_CHANGED',
                previous=old,current=current,reason='same_physical_target_fresh_evidence' if same_target else 'fresh_priority_or_availability_changed',
                next_recovery_action='VALIDATE_CURRENT_TARGET')
            operation['target']=current
        if strict: self.snapshot(frame,'before')
        x,y=point or (node.x,node.y)
        position=self.check(frame,x,y)
        if node:
            inner_radius=node.radius*min(frame.roi.width/frame.image.shape[1],frame.roi.height/frame.image.shape[0])*.35
            # A prepared burst target is a still-locked node whose ring is capped near .70.
            if inner_radius<5 or node.geometry_confidence<(.65 if getattr(self,'burst_inputs_active',False) else .7):raise ScreenError('unsafe_node')
            permit=self.retry_permit
            permitted=retry and permit and position==permit[1] and result.analysis_id==permit[2] and math.dist(position,permit[0])<max(8,node.radius*.4)
            if retry and not permitted:raise ScreenError('unsafe_purchase_retry')
            if not permitted and any(math.dist(position,p)<max(8,node.radius*.5) for p in self.attempted):
                raise ScreenError('target_already_attempted')
            if not permitted:self.attempted.append(position)
            self.mouse.target_region=(position,inner_radius)
            self.trace('safe_input_region',center=position,inner_radius=inner_radius,analysis_id=result.analysis_id,retry=retry)
        else:self.mouse.target_region=None
        self.emit(State.PURCHASING)
        self.pending_action=True
        if node:
            from src.core.scene_memory import record_scene
            self.scene_record=record_scene(frame,result,self.attempted,dict(point=list(position),radius=node.radius,icon=node.icon_id),previous=self.scene_record)
            if self.active_group:self.scene_record=self.active_group.scene(frame,result,node)
            self.bind_click_operation()
        duration=self.settings.hold_seconds if self.settings.purchase_mode=='hold' else .05
        now=time.perf_counter()
        if hasattr(self,'last_input_time'):self.performance.add('input_interval',now-self.last_input_time,'interval',action=self.actions)
        self.last_input_time=now
        guard_seconds=0.
        def held_guard():
            nonlocal guard_seconds
            start=time.perf_counter()
            try:return self.check_held(frame,x,y)
            finally:guard_seconds+=time.perf_counter()-start
        hold_start=time.perf_counter()
        self.input_api_seconds=0.
        down_before=getattr(self.mouse,'down_attempt_sequence',None)
        with self.performance.span('mouse_hold','aggregate',action=self.actions):
            # Calibration and ordinary Start both verify after one pulse.
            try:self.purchase_input(position,duration,held_guard,frame,x,y,
                allow_repeats=False)
            except Exception:
                if down_before is not None and self.mouse.down_attempt_sequence==down_before:
                    # The native MouseDown API was never called. Retain every
                    # earlier attempt, but do not invent an unresolved action
                    # when a pre-input ledger/guard failed before this one.
                    self.pending_action=False
                    if node and not retry:self.attempted.remove(position)
                    if self.scene_record:
                        self.scene_record['pending']=None
                        self.scene_record['attempted']=[list(p) for p in self.attempted]
                    if self.active_group:self.active_group.unsubmitted()
                    self.trace('input_not_submitted',point=position,down_api_attempted=False)
                raise
        self.performance.add('input_guard',guard_seconds,action=self.actions)
        self.performance.add('input_hold_wait',time.perf_counter()-hold_start-guard_seconds-self.input_api_seconds,'wait',action=self.actions)
        if self.stop.is_set() or self.pause.is_set(): raise InterruptedError
        self.trace('physical_action_complete',mouse_released=not getattr(self.mouse,'held',False))
        self.click_lifecycle.record('PURCHASE_CONFIRMATION',point=position)
        if self.active_group:self.active_group.sent.append(position)
        if not getattr(self,'burst_inputs_active',False) and hasattr(self.mouse,'park') and hasattr(self.screen,'parking_point'):
            parked=self.screen.parking_point(frame)
            if parked:
                with self.performance.span('cursor_parking','aggregate',action=self.actions):
                    self.mouse.park(parked,self.stop,lambda:self.check_held(frame,x,y))
                self.trace('cursor_parked',point=parked,clicks=0)
        if hasattr(self.screen,'fast_center_retry'):self.screen.fast_center_retry=True
        if self.active_group:
            from src.core.path_planner import RoutePlan
            return frame,result,RoutePlan(reason='group_input_only')
        self.emit(State.WAIT_PURCHASE)
        deadline=time.monotonic()+self.settings.purchase_timeout
        self.target_probe_until=min(deadline,time.monotonic()+.8)
        transition_grace_used=False
        verification_started=time.perf_counter()
        self.last_purchase_outcome='purchase_result_unknown'
        if node:
            from src.core.purchase_outcome import PurchaseEvidence
            evidence=PurchaseEvidence(frame,result,node,time.monotonic());self.purchase_evidence=evidence
        previous_absorbed=sum(n.state==NodeState.ABSORBED for n in result.nodes)
        confirmations=0
        terminal_seen=False
        # First red from cheap local captures; the second from a normal map
        # read, whose pixels also plan the next purchase.
        local_red=False;local_polls=0;local_until=time.monotonic()+3
        while time.monotonic()<deadline:
            self.wait_stage='confirmation_poll_wait'
            if (node and not strict and self.settings.fast_opening and not local_red and time.monotonic()<local_until
                    and hasattr(self.screen,'capture_confirmation')):
                self.wait(max(0,min(.1,self.settings.poll_interval)-(time.monotonic()-self.last_capture_at)))
            else:self.poll()
            self.wait_stage='poll_wait'
            from src.core.level_transition import final_target,new_level,known_glyph_transition,pristine_next_level
            final_attempt=bool(node and not strict and final_target(result,node))
            # A slow centre search can stale an after-capture too. Discard it
            # and recapture within the same confirmation deadline; never click
            # again or count stale pixels as purchase evidence.
            try:
                local=None
                if node and not strict and self.settings.fast_opening and not local_red and time.monotonic()<local_until:
                    from src.core.target_evidence import local_target_check
                    try:local=local_target_check(self,frame,node);local_polls+=1
                    except ScreenError as exc:
                        if exc.code not in ('frame_unstable','item_tooltip','bloodweb_not_found','stale_frame'):raise
                        self.trace('target_confirmation_fallback',reason=exc.code)
                    if local is None:local_until=0
                if local is not None and local[1] is None:continue
                if local is not None:
                    local_red=True
                    self.trace('purchase_red_latency',seconds=round(time.perf_counter()-verification_started,3),local_polls=local_polls,node=node.id)
                newframe,newresult,plan=local or self.recover_read(deadline,final_transition=final_attempt,retry_stale=True)
            except ScreenError as exc:
                if exc.code!='bloodweb_transition_started':raise
                return self.finish_level_attempt(frame,result)
            if strict: self.snapshot(newframe,'after_latest')
            # Coordinate system must remain identical throughout verification.
            if newframe.window.signature!=frame.window.signature:
                raise ScreenError('window_changed')
            if node and not strict:
                from src.core.level_transition import header_transition
                if self.scene_record and header_transition(self.scene_record,newframe,newresult):
                    # Use the same two-map transition contract as a saved
                    # operation. One weak unrelated new rim must not strand an
                    # absent old single after the exact next header appears.
                    # This retires input without awarding a missing purchase.
                    if not self.persisted_scene:
                        import copy
                        self.persisted_scene=copy.deepcopy(self.scene_record)
                    if self.recheck_saved_scene(newframe,newresult):
                        return self.finish_level_attempt(frame,result)
                    if (not transition_grace_used and deadline-time.monotonic()<1
                            and pristine_next_level(self.scene_record,newframe,newresult)):
                        # The first proven next map can arrive just after a
                        # slow transition capture consumes the outcome window.
                        # Allow one bounded read-only second observation; never
                        # relax the frame age or issue the old input again.
                        transition_grace_used=True
                        deadline=min(deadline+2,getattr(self,'runtime_deadline',float('inf')))
                        self.trace('transition_confirmation_grace',deadline=deadline,
                            first_stamp=newframe.captured_at,extra_seconds=2,paid_inputs=0)
                    continue
                from src.core.scene_memory import record_scene
                from src.core.scene_memory import terminal_observation
                observed=terminal_observation(self.scene_record,newframe,newresult) if self.scene_record else None
                if observed:
                    self.scene_record['transition_observation']=observed
                    self.trace('terminal_scene_observed',analysis_id=newresult.analysis_id,
                        stamp=newframe.captured_at,purchase_confirmed=False,paid_inputs=0)
                before_scene=record_scene(frame,result,self.attempted,previous=self.scene_record)
                # Entity completion or a residual locked Unknown can prevent
                # final_target from being exact. Several new known glyphs and
                # the owned-state reset still prove a different scene. Merely
                # disappearing/changing geometry after a non-final buy cannot.
                changed=known_glyph_transition(before_scene,newframe,newresult)
                from src.core.level_transition import absent_single_on_next_level
                pending_scene=self.scene_record or {}
                reset=(pristine_next_level(before_scene,newframe,newresult) or
                    absent_single_on_next_level(pending_scene,newframe,newresult))
                previous_reset=getattr(evidence,'reset_level_observation',None)
                evidence.reset_level_observation=(newframe,newresult,None) if reset else None
                if reset:
                    if (previous_reset and newframe.captured_at>previous_reset[0].captured_at and
                            self.stable_scene(previous_reset,(newframe,newresult,None))):
                        return self.finish_level_attempt(frame,result)
                    # This is a different pristine web, not a red observation
                    # of the old item. Wait for a second independent proof.
                    continue
                if changed or (final_attempt and new_level(before_scene,newframe,newresult)):
                    return self.finish_level_attempt(frame,result)
            if node:
                found,red_success,missed=evidence.observe(newframe,newresult,time.monotonic())
                self.trace('purchase_observation',node=node.id,after=found.state.value if found else 'missing',screen=newresult.screen_state.value)
                if strict and found and found.state==NodeState.ABSORBED:
                    raise ScreenError('calibration_node_absorbed')
                if evidence.absorbed_count>=2:
                    self.pending_action=False;self.last_purchase_outcome='purchase_target_absorbed'
                    if self.scene_record:self.scene_record['pending']=None
                    self.trace('purchase_outcome',outcome=self.last_purchase_outcome,analysis_id=newresult.analysis_id)
                    raise ScreenError('purchase_target_absorbed')
                if missed and not strict:
                    self.pending_action=False;self.last_purchase_outcome='purchase_missed'
                    if self.scene_record:self.scene_record['pending']=None
                    self.performance.add('purchase_verification_missed',time.perf_counter()-verification_started,'aggregate',analysis_id=newresult.analysis_id,action=self.actions)
                    self.trace('purchase_outcome',outcome=self.last_purchase_outcome,analysis_id=newresult.analysis_id,observations=evidence.available_count,retry=retry)
                    raise ScreenError('purchase_missed')
                success=red_success
                terminal=newresult.screen_state in (ScreenState.REFRESH,ScreenState.PRESTIGE,ScreenState.PRESTIGE_READY,ScreenState.TUTORIAL)
                terminal_seen=terminal_seen or terminal
                if final_attempt and terminal:
                    return self.finish_level_attempt(frame,result)
            else:
                success=newresult.screen_state!=result.screen_state and newresult.screen_state!=ScreenState.UNKNOWN
            if success:
                confirmations=evidence.red_count if node else confirmations+1
            elif node and newresult.screen_state==ScreenState.BLOODWEB and found is None:
                # Hough can temporarily lose a shrinking purchased ring during
                # Entity/hover effects. A missing detection supplies no evidence
                # and cannot contradict an earlier explicit red purchase.
                # It never counts as a confirmation and cannot cause a retry.
                pass
            else:
                confirmations=0
                # Available during the animation is fresh state evidence, not
                # a reason to destroy the map. Compact checks own invalidation.
                if newresult.screen_state!=ScreenState.BLOODWEB and hasattr(self.detector,'invalidate'):self.detector.invalidate()
            if sum(n.state==NodeState.ABSORBED for n in newresult.nodes)>previous_absorbed: self.emit(State.ENTITY)
            if confirmations>=2:
                self.reconcile_operation(evidence.red_observations if node else [(f,r) for f,r,p in list(self.recent_frames)[-2:]],'worker')
                if node:
                    from src.core.target_evidence import save_proof
                    save_proof(self,evidence.red_observations)
                self.pending_action=False
                self.last_purchase_outcome='purchased'
                self.trace('purchase_outcome',outcome='purchased',analysis_id=newresult.analysis_id,retry=retry)
                self.performance.add('purchase_confirmation',time.perf_counter()-verification_started,'aggregate',analysis_id=newresult.analysis_id,action=self.actions)
                self.actions+=1
                from src.core.priority_targets import perk_eligible,PERK_TARGET
                if node:self.performance.levels.purchase(node.icon_id in self.priorities or (PERK_TARGET in self.priorities and perk_eligible(node)))
                if self.scene_record:self.scene_record['pending']=None
                self.transition_verified=terminal_seen
                return newframe,newresult,plan
        if strict:raise ScreenError('purchase_not_confirmed')
        reason=evidence.last_reason if node else 'purchase_result_unknown'
        self.last_purchase_outcome=reason
        self.performance.add('purchase_verification_failed',time.perf_counter()-verification_started,'aggregate',action=self.actions,outcome=reason)
        self.trace('purchase_outcome',outcome=reason,retry=retry)
        raise ScreenError(reason)

    def run(self,calibrate=False):
        self.calibrating=calibrate
        if self.report.path and not self.settings.dry_run:
            from src.core.evidence_writer import EvidenceWriter
            self.evidence_writer=EvidenceWriter()
        start=time.monotonic()
        self.runtime_deadline=start+self.settings.max_runtime if self.settings.max_runtime is not None else float('inf')
        try:
            self.trace('run_settings',settings=asdict(self.settings))
            if not self.settings.dry_run:
                if not self.hotkeys_ready: raise ScreenError('hotkey_registration_failed')
                if not calibrate and not self.approved: raise ScreenError('calibration_required')
            if hasattr(self.detector,'prepare'):
                with self.performance.span('detector_preparation'):self.detector.prepare()
            self.emit(State.SCANNING)
            if not self.settings.dry_run:
                countdown_end=start+self.settings.startup_delay
                remaining=max(0,countdown_end-time.monotonic())
                self.wait_stage='switch_to_game_wait'
                while remaining>0:
                    self.gate()
                    self.emit(State.SCANNING,'prepare_countdown',{'seconds':math.ceil(remaining)})
                    step=min(1,remaining); self.wait(step)
                    remaining=max(0,countdown_end-time.monotonic())
                self.wait_stage='focus_wait'
                if hasattr(self.screen,'backend'):
                    deadline=time.monotonic()+self.settings.focus_timeout;activated_at=None
                    while not (window:=self.screen.backend.info()).foreground:
                        self.gate()
                        if hasattr(self.screen.backend,'activate') and (activated_at is None or time.monotonic()-activated_at>2):
                            # Start switches to the game itself (window activation only, no input).
                            activated_at=time.monotonic()
                            try:ok=self.screen.backend.activate(window)
                            except Exception as exc:ok=False;self.trace('game_window_activation_failed',detail=repr(exc)[:200])
                            self.trace('game_window_activated',result=ok,paid_inputs=0)
                        self.emit(State.SCANNING,'waiting_game_focus')
                        if time.monotonic()>deadline: raise ScreenError('focus_wait_timeout')
                        self.wait(.2)
                self.wait_stage='poll_wait'
                if not calibrate:self.park_cursor_off_web()
            self.recovery and self.recovery.begin(self.progress_mark())
            while True:
                try:
                    self._main_loop(calibrate,start)
                    break
                except ScreenError as exc:
                    if not self.supervise_recovery(exc,calibrate):raise
        except InterruptedError:
            self.emit(State.PAUSED if self.pause.is_set() and not self.stop.is_set() else State.STOPPED,'interrupted')
        except ScreenError as exc:
            try:self.mouse.release()
            except ScreenError as release_error:self.trace('release_failed',reason=release_error.code,detail=release_error.detail)
            self.trace('failure',reason=exc.code,detail=exc.detail)
            if hasattr(self.detector,'invalidate'):self.detector.invalidate()
            if exc.code not in ('action_limit','runtime_limit'):
                try:self.save_failure_evidence(exc.code)
                except Exception as evidence_error:self.trace('failure_evidence_error',detail=str(evidence_error))
            self.emit(State.PAUSED,str(exc),{'error_detail':exc.detail}); self.pause.set()
        except Exception as exc:
            try:self.mouse.release()
            except ScreenError as release_error:self.trace('release_failed',reason=release_error.code,detail=release_error.detail)
            try:self.save_failure_evidence('unexpected_error')
            except Exception as evidence_error:self.trace('failure_evidence_error',detail=str(evidence_error))
            import traceback
            self.trace('unexpected_error',traceback=traceback.format_exc()[-3000:])
            self.emit(State.ERROR,str(exc))
        finally:
            if self.recovery and (self.persisted_scene or {}).get('pending'):self.pending_action=True
            try: self.mouse.release()
            except ScreenError as exc:
                self.trace('release_failed',reason=exc.code,detail=exc.detail)
                self.emit(State.ERROR,exc.code,{'error_detail':exc.detail})
            self.event(self.state,'restore_window',None)
            if hasattr(getattr(self.screen,'backend',None),'close_probe'):
                try:self.screen.backend.close_probe()
                except Exception as exc:self.trace('probe_capture_close_failed',detail=str(exc))
            if self.evidence_writer:
                with self.performance.span('proof_flush_wait','wait'):records=self.evidence_writer.close()
                for row in records:
                    self.performance.add('proof_io_background',row['ended_at']-row['started_at'],'background_io',**row)
                self.trace('proof_writer_finished',tasks=len(records),failures=[r for r in records if r['error']])
            self.trace('run_counters',attempts=self.attempts,confirmed_purchases=self.actions,entity_absorptions=self.entity_actions,transitions=self.transitions,unconfirmed_level_actions=self.unconfirmed_level_actions,opening=self.opening.summary(),
                turbo_suppressed=getattr(self,'turbo_suppressed',0),visual_pipeline=dict(getattr(self.screen,'pipeline_counts',{})))
            try:self.performance.save()
            except OSError as exc:self.trace('performance_write_error',detail=str(exc))

    def _main_loop(self,calibrate,start):
        waiting=None;stable=None;stable_count=0;stable_since=time.monotonic()
        complete_count=0;queued_read=None
        while True:
            warm_start=None
            completed=getattr(self,'levels_completed',0)
            if self.settings.max_levels and completed>=self.settings.max_levels and not self.pending_action:
                self.trace('level_limit',levels=completed,limit=self.settings.max_levels,confirmations=self.transitions)
                self.emit(State.STOPPED,'level_limit',dict(count=completed));return
            if self.settings.max_runtime is not None and time.monotonic()-start>self.settings.max_runtime: raise ScreenError('runtime_limit')
            self.check_input_budget()
            if self.settings.max_actions is not None and self.actions+self.unconfirmed_level_actions+self.prestige_actions>=self.settings.max_actions: raise ScreenError('action_limit')
            if queued_read:frame,result,plan=queued_read;queued_read=None
            else:frame,result,plan=self.read() if self.settings.dry_run else self.recover_read(retry_stale=True)
            if result.screen_state==ScreenState.TUTORIAL:
                if self.settings.dry_run:self.emit(State.DRY_RUN);return
                if calibrate:raise ScreenError('no_calibration_node')
                from src.core.modal_recovery import dismiss_tutorial
                queued_read=dismiss_tutorial(self,frame,result)
                continue
            if self.persisted_scene and not self.settings.dry_run and result.screen_state==ScreenState.REWARD:
                from src.core.scene_memory import same_window,record_scene
                memory=self.persisted_scene;terminal=memory.get('terminal_transition') or {}
                if (not memory.get('pending') and terminal.get('screen')=='prestige_ready'
                        and terminal.get('level')==50 and same_window(memory,frame)):
                    from src.core.modal_recovery import dismiss_reward
                    self.persisted_scene=None
                    try:queued_read=dismiss_reward(self,frame,result,external_ready=True,expected_level=1)
                    except Exception:
                        self.persisted_scene=memory;self.scene_record=memory;raise
                    nf,nr,_=queued_read;self.scene_record=record_scene(nf,nr,[])
                    self.attempted.clear();self.pending_action=False
                    self.trace('external_prestige_reward_resolved',previous_scene=memory,
                        paid_retry=False,purchase_credit=0,analysis_id=nr.analysis_id)
                    stable=None;stable_count=0;continue
            if self.persisted_scene and not self.settings.dry_run and result.screen_state==ScreenState.BLOODWEB:
                if not self.recheck_saved_scene(frame,result):
                    self.emit(State.RECOVERING,'purchase_recheck');self.poll();continue
                self.credit_readonly_resolution()
                if self.opening.active:
                    plan=self.opening.plan(result,self.actions,self.attempted_nodes(frame,result))
            if self.persisted_scene and not self.settings.dry_run:
                if result.screen_state==ScreenState.PRESTIGE_READY and (self.persisted_scene.get('pending') or {}).get('point'):
                    if self.retire_terminal_single(frame,result):
                        stable=None;stable_count=0;continue
                    if getattr(self,'saved_terminal_single',None):self.poll();continue
                from src.core.scene_memory import same_window
                memory=self.persisted_scene;terminal=memory.get('terminal_transition')
                if (not memory.get('pending') and terminal and terminal.get('screen')=='prestige_ready'
                        and terminal.get('level')==50 and result.screen_state==ScreenState.PRESTIGE_READY
                        and same_window(memory,frame)):
                    previous=getattr(self,'terminal_restore_observation',None)
                    self.terminal_restore_observation=(frame,result,None)
                    if not (previous and frame.captured_at>previous[0].captured_at and self.stable_scene(previous,(frame,result,None))):
                        self.poll();continue
                    self.trace('terminal_scene_restored',previous_purchase_confirmed=False,observations=2)
                    self.persisted_scene=self.scene_record=None;self.attempted.clear()
                else:raise ScreenError('purchase_result_unknown','scene_validation_required')
            if self.scene_record and self.attempted and not self.pending_action and not self.settings.dry_run:
                from src.core.level_transition import header_transition
                if header_transition(self.scene_record,frame,result):
                    current=(frame,result,None)
                    previous=getattr(self,'header_transition_observation',None)
                    self.header_transition_observation=current
                    if not self.stable_scene(previous,current):self.poll();continue
                    self.attempted.clear();self.scene_record=None;self.completed_layout=None
                    self.header_transition_observation=None
                    if hasattr(self.detector,'invalidate'):self.detector.invalidate()
                    self.trace('new_level_confirmed',analysis_id=result.analysis_id,observations=2,source='fresh_level_header',level=result.level)
                    stable=None;stable_count=0;continue
                self.header_transition_observation=None
            selected=None
            if calibrate and self.calibration_target:
                x,y,signature,roi=self.calibration_target
                if frame.window.signature!=signature or frame.roi!=roi: raise ScreenError('window_changed')
                selected=min(result.nodes,key=lambda n:math.hypot(n.x-x,n.y-y),default=None)
                if selected is None or math.hypot(selected.x-x,selected.y-y)>max(8,selected.radius*.4) or not purchasable(selected,result.nodes,result.edges):
                    raise ScreenError('calibration_node_unavailable')
                plan.next_node=selected.id
            if self.settings.bloodpoint_checks and frame.bloodpoints==0 and not self.settings.dry_run:
                self.emit(State.INSUFFICIENT_BP); return
            if self.settings.dry_run:
                self.emit(State.DRY_RUN,plan.reason,(frame,result,plan)); return
            if result.screen_state==ScreenState.UNKNOWN:
                self.mouse.release();self.emit(State.RECOVERING,'unknown_screen')
                queued_read=self.recover_read(time.monotonic()+min(15,self.settings.refresh_timeout))
                stable=None;stable_count=0;continue
            from src.core.early_unlock import center_mode
            if (not calibrate and not self.settings.dry_run and self.settings.fast_early_levels
                    and hasattr(self.screen,'capture_overlay') and result.screen_state==ScreenState.BLOODWEB
                    and (type(result.level) is not int or result.level_confidence<.95)):
                # An unreadable early header must not silently select the
                # per-node fallback instead of the requested centre mode.
                since=getattr(self,'header_wait_since',None)
                if since is None:self.header_wait_since=time.monotonic()
                elif time.monotonic()-since>self.settings.purchase_timeout:raise ScreenError('frame_unstable','level_header_unproven')
                self.trace('level_header_retry',paid_inputs=0)
                self.emit(State.WAIT_STABLE,'frame_unstable');stable=None;stable_count=0;self.poll();continue
            self.header_wait_since=None
            early_center=center_mode(self,result,calibrate)
            if early_center and result.level>9:
                from src.core.early_unlock import eligible as center_eligible
                if center_eligible(self,result,frame):self.center_wait_since=None
                else:
                    # Above level 9 the centre is only an optimisation. Live 7.11 20:29: raising
                    # automatic_unlock_unproven here cost four supervisor cycles (18 s) on one
                    # wobbling node. Buy the planned node instead, or re-read for a bounded time.
                    since=getattr(self,'center_wait_since',None)
                    if since is None:self.center_wait_since=since=time.monotonic()
                    self.trace('center_auto_deferred',level=result.level,paid_inputs=0,has_target=plan.next_node is not None)
                    if plan.next_node is not None or time.monotonic()-since>self.settings.purchase_timeout:early_center=False
                    else:
                        self.emit(State.WAIT_STABLE,'frame_unstable');stable=None;stable_count=0
                        self.wait_stage='stability_poll_wait';self.poll();self.wait_stage='poll_wait';continue
            if result.screen_state==ScreenState.REFRESH or (result.screen_state==ScreenState.BLOODWEB and plan.next_node is None and not early_center):
                if calibrate: raise ScreenError('no_calibration_node')
                if any(n.state in (NodeState.AVAILABLE,NodeState.LOCKED,NodeState.UNCERTAIN) for n in result.nodes):
                    if self.recovery:raise ScreenError('no_confident_action')
                    self.emit(State.PAUSED,'no_confident_action'); return
                complete_count+=1
                if complete_count>=2:
                    reference=getattr(self,'last_bloodweb',None)
                    if reference:
                        queued_read=self.wait_next_bloodweb(*reference)
                        f,r,p=queued_read;target=next((n for n in r.nodes if n.id==p.next_node),None)
                        stable=(f,r,target);stable_count=1;complete_count=0;waiting=None;continue
                    if hasattr(self.detector,'invalidate'):self.detector.invalidate()
                stable=None;stable_count=0
                if waiting is None: waiting=time.monotonic(); self.emit(State.FINISHED)
                self.emit(State.WAIT_REFRESH)
                if time.monotonic()-waiting>self.settings.refresh_timeout: raise ScreenError('refresh_timeout')
                self.wait_stage='level_transition_wait';self.poll();self.wait_stage='poll_wait';continue
            waiting=None
            complete_count=0
            # Two independent captures with matching state and target before an action.
            target=next((n for n in result.nodes if n.id==plan.next_node),None)
            if selected: target=selected
            if stable is None: stable_since=time.monotonic()
            current=(frame,result,target)
            stable_count=stable_count+1 if self.stable_scene(stable,current) else 1; stable=current
            if stable_count<2:
                self.emit(State.WAIT_STABLE,'frame_unstable')
                if time.monotonic()-stable_since>self.settings.purchase_timeout: raise ScreenError('recovery_timeout','frame_unstable')
                self.wait_stage='stability_poll_wait';self.poll();self.wait_stage='poll_wait';continue
            if result.screen_state==ScreenState.BLOODWEB:self.performance.levels.confirm(frame,result)
            if self.completed_layout and self.changed_layout(self.completed_layout,(frame,result)):
                self.transition_verified=True
            if self.transition_verified and result.screen_state==ScreenState.BLOODWEB and self.scene_record:
                from src.core.scene_memory import layout_changed
                # REFRESH can also be a transient detection failure. A
                # returning old web must keep every attempted point blocked.
                if not layout_changed(self.scene_record,frame,result):self.transition_verified=False
            if self.transition_verified and result.screen_state==ScreenState.BLOODWEB:
                self.attempted.clear();self.transition_verified=False
                self.scene_record=None
                self.completed_layout=None
                self.trace('new_level_confirmed',analysis_id=result.analysis_id,observations=stable_count,level=result.level)
            if result.screen_state==ScreenState.PRESTIGE_READY:
                if calibrate:raise ScreenError('no_calibration_node')
                if not self.settings.auto_prestige or not self.prestige_approved:
                    self.emit(State.PAUSED,'prestige_manual');return
                from src.core.prestige import activate_ready
                queued_read=activate_ready(self,frame,result)
                # Prestige animation belongs to the completed action. A
                # newly observed Rewards/web scene gets its own stability
                # interval, just as the normal purchase path below does.
                stable=None;stable_count=0;stable_since=time.monotonic();continue
            if result.screen_state in (ScreenState.PRESTIGE,ScreenState.REWARD):
                if calibrate: raise ScreenError('no_calibration_node')
                if not self.prestige_approved: self.emit(State.PAUSED,'prestige_manual'); return
                if not self.settings.auto_prestige: self.emit(State.STOPPED,'prestige_disabled'); return
                if result.screen_state==ScreenState.PRESTIGE:
                    self.emit(State.PAUSED,'prestige_price_unproven');return
                else:
                    self.emit(State.REWARD)
                    if not result.ok_box: raise ScreenError('unknown_screen')
                    from src.core.modal_recovery import dismiss_reward
                    queued_read=dismiss_reward(self,frame,result)
                    self.event(self.state,'restore_window',None)
                    stable=None;stable_count=0;continue
            elif target or early_center:
                from src.core.early_unlock import eligible,purchase_level
                automatic=False
                if early_center:
                    if not eligible(self,result,frame):raise ScreenError('automatic_unlock_unproven')
                    after,after_result,after_plan=purchase_level(self,frame,result);automatic=True
                self.emit(State.READY)
                before=hashlib.sha256(frame.image.tobytes()).hexdigest()
                if not automatic:
                    down_before_buy=getattr(self.mouse,'down_attempt_sequence',None)
                    try:after,after_result,after_plan=self.buy(frame,result,node=target,strict=calibrate)
                    except ScreenError as exc:
                        if (exc.code=='purchase_result_unknown' and not calibrate and self.pending_action):
                            recovered=self.recover_chain_readonly()
                            if recovered:
                                queued_read=recovered;stable=None;stable_count=0;continue
                        if (exc.code in ('cursor_position_mismatch','cursor_outside_node') and not calibrate and not self.pending_action
                                and type(down_before_buy) is int and self.mouse.down_attempt_sequence==down_before_buy):
                            # The cursor moved before MouseDown; nothing was sent. Re-read and retry, 3 times in a row at most.
                            self.consecutive_cursor_moves=getattr(self,'consecutive_cursor_moves',0)+1
                            if self.consecutive_cursor_moves>3:raise ScreenError('mouse_being_moved') from exc
                            self.trace('preinput_cursor_recovery',reason=exc.code,paid_inputs=0,down_api_attempted=False,
                                retry=self.consecutive_cursor_moves)
                            self.emit(State.RECOVERING,exc.code)
                            if hasattr(self,'park_cursor_off_web'):self.park_cursor_off_web()
                            queued_read=self.recover_read(retry_stale=True);stable=None;stable_count=0;continue
                        if exc.code=='purchase_retry_failed' and not calibrate and not self.pending_action:
                            # Verified twice that nothing was bought. Three in a row on
                            # different nodes, with no purchase between: out of bloodpoints.
                            self.verified_misses=getattr(self,'verified_misses',0)+1
                            self.trace('verified_unbought_purchase',count=self.verified_misses,paid_inputs=0)
                            if self.verified_misses>=3:
                                self.emit(State.INSUFFICIENT_BP,'out_of_bloodpoints');return
                            self.emit(State.RECOVERING,'purchase_not_taken')
                            queued_read=self.recover_read(retry_stale=True);stable=None;stable_count=0;continue
                        if (exc.code in ('stale_frame','chain_map_changed') and not calibrate and not self.pending_action
                                and type(down_before_buy) is int
                                and self.mouse.down_attempt_sequence==down_before_buy):
                            self.consecutive_preparation_changes+=1
                            if self.consecutive_preparation_changes>3:raise ScreenError('preparation_recovery_limit') from exc
                            self.preparation_recoveries+=1
                            self.trace('preinput_stale_recovery' if exc.code=='stale_frame' else 'preinput_guard_recovery',
                                reason=exc.code,paid_inputs=0,down_api_attempted=False,
                                reservations_retained=True,retry=self.consecutive_preparation_changes)
                            self.emit(State.RECOVERING,exc.code)
                            queued_read=self.recover_read(retry_stale=True);stable=None;stable_count=0;continue
                        if (exc.code=='purchase_preparation_changed' and not calibrate and not self.pending_action
                                and getattr(self,'preparation_changed_without_input',False)):
                            self.preparation_changed_without_input=False
                            self.consecutive_preparation_changes+=1
                            if self.consecutive_preparation_changes>3:raise ScreenError('preparation_recovery_limit') from exc
                            self.preparation_recoveries+=1
                            self.emit(State.RECOVERING,'purchase_preparation_changed')
                            queued_read=getattr(self,'preparation_replacement',None)
                            self.preparation_replacement=None
                            if queued_read is None:queued_read=self.recover_read(retry_stale=True)
                            stable=None;stable_count=0;continue
                        if exc.code!='purchase_target_absorbed' or calibrate or self.pending_action:raise
                        # Two positive absorbed observations resolve this
                        # attempt. Keep its point blocked and choose a fresh
                        # alternative; never retry the swallowed node.
                        self.emit(State.ENTITY,'purchase_target_absorbed')
                        queued_read=self.recover_read();stable=None;stable_count=0;continue
                if hasattr(self.screen,'fast_center_retry'):self.screen.fast_center_retry=False
                self.consecutive_preparation_changes=0;self.consecutive_cursor_moves=0;self.verified_misses=0
                from src.core.level_transition import complete
                if not calibrate and not after_result.details.get('confirmation_only') and complete(after_result) and (self.settings.max_actions is None or self.actions+self.unconfirmed_level_actions<self.settings.max_actions):
                    after,after_result,after_plan=self.wait_next_bloodweb(after,after_result)
                if after_result.screen_state==ScreenState.BLOODWEB:
                    next_target=next((n for n in after_result.nodes if n.id==after_plan.next_node),None)
                    if next_target is not None:warm_start=(after,after_result,next_target)
                if calibrate:
                    self.receipt=dict(verified=True,user_confirmed=False,window=frame.metadata()['window'],
                        roi=asdict(frame.roi),point=frame.to_screen(target.x,target.y),
                        mode=self.settings.purchase_mode,hold_seconds=self.settings.hold_seconds,
                        before=before,after=hashlib.sha256(after.image.tobytes()).hexdigest())
                    self.emit(State.STOPPED,'calibration_verified',self.receipt); return
            stable=warm_start; stable_count=1 if warm_start else 0; stable_since=time.monotonic()

    def snapshot(self,frame,name):
        if self.report.path is None: return
        from src.utils.images import write_image
        self.snapshot_count=getattr(self,'snapshot_count',0)+1
        filename=f'{name}_{self.snapshot_count:03}.png' if name=='after_latest' else f'{name}.png'
        path=self.report.path.with_suffix('')/filename
        try: write_image(path,frame.image); self.trace('snapshot',path=str(path))
        except OSError as exc: self.trace('snapshot_failed',detail=str(exc))
