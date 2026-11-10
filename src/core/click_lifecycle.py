"""Durable operation/intent journal; observations never authorize input."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime,timezone
from functools import wraps
from pathlib import Path
import json,threading,time,uuid
from src.core.screen_manager import ScreenError


class ClickLifecycle:
    def __init__(self,path=None):
        self.path=Path(path) if path else None
        self.current=ContextVar('click_operation',default=None)
        self.lock=threading.Lock();self.rows=[];self.error=None

    def record(self,stage,**details):
        op=self.current.get()
        row=dict(time=datetime.now(timezone.utc).isoformat(),qpc=time.perf_counter(),
            thread_id=threading.get_native_id(),operation_id=op['id'] if op else None,
            intent=op['intent'] if op else None,stage=stage)
        row.update(details)
        with self.lock:
            self.rows.append(row);self.rows=self.rows[-1000:]
            if self.path:
                try:
                    self.path.parent.mkdir(parents=True,exist_ok=True)
                    with self.path.open('a',encoding='utf-8') as stream:
                        stream.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')
                except OSError as exc:self.error=str(exc)
        return row

    @contextmanager
    def operation(self,intent,target=None,**details):
        parent=self.current.get();op=dict(id=uuid.uuid4().hex,intent=intent,target=target,
            prepared_at=None,down=False,down_attempted=False,up=False,result=None,cancelled=False)
        op['canonical_id']=parent.get('canonical_id') if parent else None
        token=self.current.set(op)
        self.record('SELECT',target=target,parent_id=parent['id'] if parent else None,
            canonical_operation_id=op['canonical_id'],**details)
        try:
            self.record('PREPARE')
            yield op
        except BaseException as exc:
            reason=getattr(exc,'code',type(exc).__name__)
            uncertain=op['down'] or op['down_attempted']
            op['result']=op['result'] or ('InputSentUnconfirmed' if uncertain else
                'TargetChanged' if getattr(exc,'detail',None)=='higher_priority_observed' else 'CancelledByGuard')
            if not op['cancelled'] and intent in ('ACTUAL_PURCHASE','MODAL_DISMISS'):
                self.input_event('input_cancelled',cancel_reason=reason,cancelled_stage='VERIFY' if uncertain else 'PREPARE',
                    next_recovery_action='OBSERVE_WITHOUT_RETRY' if uncertain else 'TARGET_REVALIDATION')
            self.record('operation_cancelled',cancel_reason=reason,next_recovery_action=
                'OBSERVE_WITHOUT_RETRY' if uncertain else 'TARGET_REVALIDATION')
            raise
        finally:
            self.record('FINISH',result=op['result'] or ('InputSentUnconfirmed' if op['down'] or op['down_attempted'] else 'InputNotSent'),
                down_sent=op['down'],up_sent=op['up'],target=op['target'])
            if parent:
                parent['down']=parent['down'] or op['down'];parent['up']=parent['up'] or op['up']
                parent['down_attempted']=parent['down_attempted'] or op['down_attempted']
            self.current.reset(token)

    def input_event(self,stage,operation=None,**details):
        op=operation or self.current.get()
        if op:
            if stage=='PREPARED':op['prepared_at']=time.perf_counter()
            if stage=='MOUSE_DOWN':op['down']=True;op['down_attempted']=True
            if stage=='INPUT_API_FAILED' and details.get('flag')==2:op['down_attempted']=True
            if stage=='MOUSE_UP':op['up']=True
            if stage=='input_cancelled':
                op['cancelled']=True
                details['elapsed_since_preparation']=time.perf_counter()-op['prepared_at'] if op['prepared_at'] else None
                details['target_id']=(op.get('target') or {}).get('node')
        if op:details.update(operation_id=op['id'],intent=op['intent'])
        return self.record(stage,**details)


def transaction(kind,intent='ACTUAL_PURCHASE'):
    """Observe the existing production transaction, including its preparation."""
    def decorate(fn):
        @wraps(fn)
        def wrapped(engine,frame,result,*args,**kwargs):
            journal=getattr(engine,'click_lifecycle',None)
            if journal is None:return fn(engine,frame,result,*args,**kwargs)
            node=kwargs.get('node',args[0] if args else None)
            point=kwargs.get('point');coordinate_error=None
            if hasattr(node,'x'):
                try:point=frame.to_screen(node.x,node.y)
                except ScreenError as exc:
                    # Logging must not replace the controller's earlier safety
                    # decision. This diagnostic point never authorizes input.
                    coordinate_error=exc.code
            target=dict(node=getattr(node,'id',None),icon=getattr(node,'icon_id',None),point=point,
                level=result.level,window=frame.window.signature,frame_stamp=frame.captured_at)
            if coordinate_error:target.update(coordinate_error=coordinate_error,local_point=[node.x,node.y])
            before_actions=getattr(engine,'actions',0)
            with journal.operation(intent,target,kind=kind,preparation_id=getattr(engine,'last_preparation_id',None)):
                try:value=fn(engine,frame,result,*args,**kwargs)
                except BaseException:raise
                outcome=getattr(engine,'last_purchase_outcome',None)
                journal.current.get()['result']={'purchased':'Purchased','purchased_late':'Purchased',
                    'purchase_target_absorbed':'EntityConsumed','purchase_missed':'FailedWithVerifiedNoPurchase'}.get(outcome)
                if getattr(engine,'actions',0)==before_actions:journal.current.get()['result']=None
                if intent=='MODAL_DISMISS':journal.current.get()['result']='ModalDismissed'
                return value
        return wrapped
    return decorate


@contextmanager
def probe(engine,intent,point):
    engine.hover_probes=getattr(engine,'hover_probes',0)+1
    journal=getattr(engine,'click_lifecycle',None)
    if journal is None:yield;return
    with journal.operation(intent,dict(point=point)) as op:
        yield
        op['result']='ProbeComplete'


def price_preparation(fn):
    @wraps(fn)
    def wrapped(review,frame,result,plan,targets,kind='single'):
        engine=review.engine;journal=getattr(engine,'click_lifecycle',None)
        engine.hover_probes=getattr(engine,'hover_probes',0)+1
        # Count preparation intents, including a later guard cancellation.
        # Frame-less adapter probes must retain their original control flow.
        if frame is not None and result is not None:
            counts=getattr(engine,'price_probes_by_scene',None)
            if counts is None:counts={};engine.price_probes_by_scene=counts
            key=(frame.window.signature,result.level);counts[key]=counts.get(key,0)+1
        if journal is None:return fn(review,frame,result,plan,targets,kind)
        points=[frame.to_screen(n.x,n.y) for n in targets]
        with journal.operation('PRICE_PROBE',dict(points=points,level=result.level),kind=kind) as op:
            value=fn(review,frame,result,plan,targets,kind)
            op['result']='Prepared';review.permit['preparation_operation_id']=op['id']
            engine.last_preparation_id=op['id']
            return value
    return wrapped


def probe_transaction(intent):
    def decorate(fn):
        @wraps(fn)
        def wrapped(engine,frame,result,*args,**kwargs):
            center=next((n for n in result.nodes if getattr(n.state,'value',n.state)=='center'),None)
            point=frame.to_screen(center.x,center.y) if center else None
            with probe(engine,intent,point):return fn(engine,frame,result,*args,**kwargs)
        return wrapped
    return decorate
