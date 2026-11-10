"""Bounded, ordered optional evidence IO; never input or budget journals."""
from concurrent.futures import ThreadPoolExecutor
import threading,time


class EvidenceWriter:
    def __init__(self,capacity=2):
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='bloodweb-proof')
        self.slots=threading.Semaphore(capacity);self.lock=threading.Lock()
        self.records=[];self.closed=False

    def submit(self,identity,write):
        if self.closed:raise RuntimeError('evidence_writer_closed')
        self.slots.acquire()
        def work():
            started=time.perf_counter();error=None
            try:write()
            except Exception as exc:error=f'{type(exc).__name__}: {exc}'
            finally:
                with self.lock:self.records.append(dict(identity=identity,started_at=started,ended_at=time.perf_counter(),error=error))
                self.slots.release()
        self.pool.submit(work)

    def failures(self):
        with self.lock:return [r for r in self.records if r['error']]

    def close(self):
        self.closed=True;self.pool.shutdown(wait=True)
        with self.lock:return list(self.records)
