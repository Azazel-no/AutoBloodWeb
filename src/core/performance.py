"""Small perf_counter samples linked to immutable analyses and action reports."""
from contextlib import contextmanager,nullcontext
from collections import defaultdict
from pathlib import Path
import json,statistics,time,functools,threading


def phase(owner,stage,kind='aggregate'):
    performance=getattr(getattr(owner,'engine',owner),'performance',None)
    return performance.span(stage,kind) if performance is not None else nullcontext()


def measured(stage,kind='aggregate'):
    """Record actual nested intervals, including helper modules used by Start."""
    def decorate(function):
        @functools.wraps(function)
        def call(owner,*args,**kwargs):
            performance=getattr(getattr(owner,'engine',owner),'performance',None)
            if performance is None:return function(owner,*args,**kwargs)
            details={}
            with performance.span(stage,kind,details=details):
                result=function(owner,*args,**kwargs)
                if stage=='map_read' and isinstance(result,tuple) and len(result)>1:
                    details['analysis_id']=getattr(result[1],'analysis_id',None)
                return result
        return call
    return decorate


class Performance:
    def __init__(self,path=None,action_report=None):
        self.path=Path(path) if path else None;self.action_report=str(action_report) if action_report else None
        self.samples=[];self.sequence=0
        self.levels=LevelPerformance(self.checkpoint)
        self._stack=threading.local();self._stream=None;self._flushed=0
        self.anchor=dict(perf=time.perf_counter(),unix=time.time())

    def checkpoint(self):
        """Retain completed level boundaries even if a native process dies."""
        if not self.path:return
        started=time.perf_counter();error=None
        try:
            if self._stream:self._stream.flush()
            path=self.path.with_suffix('.checkpoint.json');path.parent.mkdir(parents=True,exist_ok=True)
            temporary=path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(dict(clock_anchor=self.anchor,levels=self.levels.report(),last_sequence=self.sequence)),encoding='utf-8')
            temporary.replace(path)
        except OSError as exc:error=str(exc)
        ended=time.perf_counter()
        self.add('performance_checkpoint',ended-started,'io',started_at=started,ended_at=ended,error=error)

    def add(self,name,seconds,kind='compute',analysis_id=None,action=None,**details):
        sample=dict(sequence=self.sequence,stage=name,seconds=max(0,float(seconds)),kind=kind,analysis_id=analysis_id,action=action,**details)
        sample.setdefault('recorded_at',time.perf_counter())
        sample.setdefault('parent_span',getattr(self._stack,'current',None))
        self.samples.append(sample)
        self.sequence+=1
        self.samples=self.samples[-5000:]
        if self.path:
            try:
                if self._stream is None:
                    self.path.parent.mkdir(parents=True,exist_ok=True)
                    self._stream=self.path.with_suffix('.timeline.jsonl').open('a',encoding='utf-8')
                self._stream.write(json.dumps(sample,default=str)+'\n')
                if time.monotonic()-self._flushed>=1:self._stream.flush();self._flushed=time.monotonic()
            except OSError:
                # Telemetry never prevents releasing an input or stopping.
                pass

    @contextmanager
    def span(self,name,kind='compute',**details):
        completed_details=details.pop('details',None)
        start=time.perf_counter();parent=getattr(self._stack,'current',None)
        ident=f'{threading.get_ident()}:{start:.9f}';self._stack.current=ident
        try:yield
        finally:
            end=time.perf_counter();self._stack.current=parent
            if completed_details:details.update(completed_details)
            self.add(name,end-start,kind,started_at=start,ended_at=end,span_id=ident,parent_span=parent,**details)

    def report(self):
        grouped=defaultdict(list)
        for sample in self.samples:grouped[sample['stage']].append(sample['seconds'])
        return dict(action_report=self.action_report,clock='time.perf_counter',clock_anchor=self.anchor,samples=self.samples,levels=self.levels.report(),
            summary={name:dict(count=len(values),mean=statistics.mean(values),median=statistics.median(values),p95=percentile(values,.95),max=max(values),total=sum(values)) for name,values in grouped.items()},
            kinds={kind:sum(s['seconds'] for s in self.samples if s['kind']==kind) for kind in ('compute','capture','wait','io')},
            note='Aggregate/interval stages are not added to leaf compute/capture/wait/io totals; nested stage subtotals must not be counted twice.')

    def save(self):
        if not self.path:return
        if self._stream:self._stream.flush()
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.json.tmp');temporary.write_text(json.dumps(self.report(),indent=2),encoding='utf-8');temporary.replace(self.path)


def percentile(values,q):
    """Linear percentile, defined also for a single observation."""
    ordered=sorted(values);position=(len(ordered)-1)*q;lo=int(position);hi=min(lo+1,len(ordered)-1)
    return ordered[lo]+(ordered[hi]-ordered[lo])*(position-lo)


class LevelPerformance:
    """Confirmed level boundaries only; a run's first/last fragments are partial."""
    def __init__(self,checkpoint=None):
        self.current=None;self.finished=[];self.pending=[];self.checkpoint=checkpoint

    def read(self,frame,result,full,replans,local_matches):
        row=dict(level=result.level,signature=frame.window.signature,stamp=frame.captured_at,
            full=int(full),fast=int(not full),replans=replans,local_matches=local_matches)
        if self.current and (row['level'],row['signature'])==(self.current['level'],self.current['signature']):
            self._add(row)
        else:self.pending=(self.pending+[row])[-20:]

    def _add(self,row):
        for name in ('full','fast','replans','local_matches'):self.current[name]+=row[name]

    def confirm(self,frame,result,transition=False):
        if result.level is None:return
        key=(result.level,frame.window.signature)
        if self.current and key==(self.current['level'],self.current['signature']):return
        if self.current:
            self.current.update(complete=bool(transition),ended_at=frame.captured_at,
                elapsed=frame.captured_at-self.current['started_at'])
            old=self.current['bp_start'];new=frame.bloodpoints
            self.current['bp_spent']=old-new if type(old) is int and type(new) is int and old>=new else None
            self.finished.append(self.current)
        self.current=dict(level=result.level,signature=frame.window.signature,started_at=frame.captured_at,
            observed_from_transition=bool(transition),complete=False,elapsed=None,bp_start=frame.bloodpoints,
            bp_spent=None,commands=0,purchases=0,priority_items=0,chains=0,full=0,fast=0,replans=0,local_matches=0,
            waits={},pauses=[])
        for row in self.pending:
            if (row['level'],row['signature'])==key:self._add(row)
        self.pending=[]
        if self.checkpoint:self.checkpoint()

    def input(self):
        if self.current:self.current['commands']+=1

    def purchase(self,priority=False,count=1):
        if self.current:
            self.current['purchases']+=count
            if priority:self.current['priority_items']+=count

    def wait(self,stage,seconds):
        if self.current:self.current['waits'][stage]=self.current['waits'].get(stage,0)+seconds

    def pause(self,reason):
        if self.current:self.current['pauses'].append(reason)

    def report(self):
        records=self.finished+([self.current] if self.current else [])
        summary={}
        for label,lo,hi in (('1-9',1,9),('10-49',10,49),('50',50,50)):
            values=[r['elapsed'] for r in self.finished if r['observed_from_transition'] and r['complete'] and lo<=r['level']<=hi]
            summary[label]=dict(count=len(values),median=statistics.median(values) if values else None,
                p95=percentile(values,.95) if len(values)>=2 else None,max=max(values,default=None))
        return dict(records=[{k:v for k,v in r.items() if k!='signature'} for r in records],summary=summary,
            note='Only complete levels observed from a confirmed transition enter timing statistics; first/last fragments do not.')
