"""Bounded, structured per-run audit, including precise Windows failure stages."""
from datetime import datetime,timezone
from pathlib import Path
import json
import time


class ActionReport:
    def __init__(self,path=None,flush_interval=0):
        self.path=Path(path) if path else None
        self.entries=[]
        self.flush_interval=flush_interval;self._last_flush=float('-inf')
        self.defer_flush=False

    def add(self,stage,**details):
        self.entries.append(dict(time=datetime.now(timezone.utc).isoformat(),stage=stage,**details))
        self.entries=self.entries[-1000:]
        # Keep cursor/guard detail in memory between important events instead
        # of serializing the full report for every individual Win32 call.
        # Before/after input, outcomes, failures and terminal counters are
        # always flushed, including all preceding detail in the same snapshot.
        critical=stage in ('SendInput','before_purchase','purchase_observation','purchase_outcome','failure',
            'state','run_settings','run_counters','new_level_confirmed','series_operation_reserved',
            'physical_action_complete','automatic_node_confirmed','automatic_level_outcome',
            'chain_before','chain_observation','chain_outcome','chain_unresolved','native_chain_trial_finished')
        now=time.monotonic()
        if self.path and not self.defer_flush and (critical or now-self._last_flush>=self.flush_interval):
            try:
                self.path.parent.mkdir(parents=True,exist_ok=True)
                temporary=self.path.with_suffix('.json.tmp')
                temporary.write_text(self.text(),encoding='utf-8'); temporary.replace(self.path)
                self._last_flush=now
            except OSError as exc:
                # An unavailable log must never prevent emergency mouse release.
                self.entries[-1]['report_write_error']=str(exc)

    def text(self):
        return json.dumps(dict(entries=self.entries),ensure_ascii=False,indent=2,default=str)
