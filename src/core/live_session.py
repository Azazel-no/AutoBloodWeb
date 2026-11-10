"""Optional durable limits around the ordinary GUI worker, with no route policy."""
import json
import time
from pathlib import Path
from src.core.models import NodeState, ScreenState
from src.core.screen_manager import ScreenError
from src.core.series_budget import SeriesBudget


class LiveSession:
    def status(self,engine,state,message):
        if time.monotonic()-getattr(self,'last_status',0)<.3 and state not in ('paused','error','stopped'):return
        self.last_status=time.monotonic()
        presentation=message in ('restore_window','hide_for_dialog')
        if not presentation:self.last_status_message=message
        record=dict(state=state,message=getattr(self,'last_status_message',''),confirmed=engine.actions,commands=engine.attempts,
                    pending=engine.pending_action or bool((engine.persisted_scene or {}).get('pending')),
                    spent=self.budget.data['spent'],report=str(engine.report.path),updated_at=time.time())
        if presentation:record['ui_event']=message
        temp=self.path.parent/'status.tmp'
        try:
            temp.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
            temp.replace(self.path.parent/'status.json')
        except OSError:pass  # Observer only; the pre-input durable ledger is mandatory.

    def __init__(self, path):
        self.path=Path(path).resolve()
        self.authorization=json.loads(self.path.read_text(encoding='utf-8'))
        a=self.authorization
        if (type(a.get('expires_at')) not in (int,float) or type(a.get('started_at')) not in (int,float)
                or not 0<a['expires_at']-a['started_at']<=7200):
            raise ValueError('series_scope_invalid')
        from scripts.live_test_v48 import acquire_scope
        self.lock=acquire_scope(self.path)
        self.budget=SeriesBudget(self.path.parent/'ledger.json',a)
        self.budget.save()

    def install(self, engine):
        a=self.authorization;budget=self.budget
        if not engine.settings.bloodpoint_checks:
            # Only nodes, time and prestiges are limited; the BP counter is not read.
            budget.authorization=dict(budget.authorization,max_bp=None)
        from src.core.bloodpoint_evidence import BloodpointEvidence
        engine.bp_evidence=BloodpointEvidence(budget.data['last_bp'])
        pending=(engine.persisted_scene or {}).get('pending') or {}
        if pending:
            matching=[r for r in budget.data['reservations'] if r.get('operation_id')==pending.get('operation_id')]
            if matching:engine.series_bp_debit_bound=matching[-1]['upper_bound']
        active={'index':None,'operation':None,'actions':0,'prestiges':0,'burst':False}
        original_safety=engine.live_safety
        def scope():
            if (self.path.parent/'stop.request').exists():raise InterruptedError
            if (self.path.parent/'finish.request').exists() and not engine.pending_action:raise InterruptedError
            if time.time()>=a['expires_at']:raise ScreenError('runtime_limit')
            if (engine.detector.role!=a['role'] or engine.settings.character!=a['character']
                    or engine.settings.auto_prestige and not a['allow_prestige']
                    or engine.settings.native_chain and not a['allow_chain']
                    or engine.settings.turbo_burst and not a.get('allow_turbo_burst')):
                raise ScreenError('chain_scope_changed')
        def safety():scope();original_safety()
        engine.live_safety=safety
        engine.settings.max_runtime=min(engine.settings.max_runtime or float('inf'),max(0,a['expires_at']-time.time()))
        original_read=engine.read
        def read():
            value=original_read();frame=value[0]
            if frame.bloodpoints is not None:
                budget.observe(frame.bloodpoints)
                if (active['index'] is not None and not engine.pending_action
                        and not (engine.scene_record or {}).get('pending')
                        and (engine.actions>active['actions'] or engine.prestige_actions>active['prestiges'])):
                    budget.settle(active['index'],frame.bloodpoints)
                    active.update(index=None,operation=None,burst=False)
            return value
        engine.read=read
        def reserve(frame, price, kind, points, operation):
            scope()
            engine.series_bp_debit_bound=price
            active.update(index=budget.reserve(frame.bloodpoints,price,kind,points,operation_id=operation),
                          operation=operation,actions=engine.actions,prestiges=engine.prestige_actions,burst=kind=='batch')
            engine.trace('series_operation_reserved',reservation=active['index'],price_upper_bound=price,kind=kind)
        def burst(group):
            reserve(group.frame,group.price_upper_bound,'batch',
                    [group.frame.to_screen(n.x,n.y) for n in group.targets],group.ident)
        engine.reserve_burst=burst
        original_purchase=engine.mouse.purchase
        def purchase(point,duration,stop,check):
            scope();check()
            frame,result,_=engine.recent_frames[-1]
            pending=(engine.scene_record or {}).get('pending') or {}
            operation=pending.get('operation_id')
            if result.screen_state not in (ScreenState.TUTORIAL,ScreenState.REWARD):
                if not pending:raise ScreenError('purchase_result_unknown','budget_missing_operation')
                repeat_prestige=(pending.get('prestige') and active['index'] is not None
                    and operation==active['operation'] and engine.prestige_actions==active['prestiges']
                    and budget.data['reservations'][active['index']]['kind']=='prestige')
                if repeat_prestige:
                    receipt=budget.data['reservations'][active['index']]
                    if (frame.bloodpoints!=receipt['bp_before'] or pending.get('price')!=receipt['upper_bound']
                            or receipt['points']!=[list(point)]):raise ScreenError('prestige_price_unproven')
                elif not (active['burst'] and engine.active_group is not None
                        and engine.active_group.ident==active['operation']):
                    if pending.get('chain'):
                        chain=pending['chain'];points=chain['physical_points'];kind='chain'
                        price=chain['total_bp'] if type(frame.bloodpoints) is int else None
                    elif pending.get('automatic'):
                        targets=[n for n in result.nodes if n.state in (NodeState.AVAILABLE,NodeState.LOCKED)]
                        points=[frame.to_screen(n.x,n.y) for n in targets];price=None;kind='early_center'
                    elif pending.get('prestige'):
                        if not a['allow_prestige']:raise ScreenError('prestige_manual')
                        price=pending.get('price');points=[point];kind='prestige'
                        if type(price) is not int or not 0<price<=a.get('max_prestige_bp',20000):
                            raise ScreenError('prestige_price_unproven')
                    else:
                        price=getattr(engine,'direct_single_price_bound',None);points=[point];kind='single'
                    reserve(frame,price,kind,points,operation)
                elif list(point) not in budget.data['reservations'][active['index']]['points']:
                    raise ScreenError('group_session_not_ready')
            budget.command()
            return original_purchase(point,duration,stop,check)
        engine.mouse.purchase=purchase
        scope()
        engine.trace('live_session_limits',authorization=str(self.path),expires_at=a['expires_at'],
                     max_bp=a.get('max_bp'),max_nodes=a['max_nodes'],ledger=str(budget.path))
