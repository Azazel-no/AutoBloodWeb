"""Short per-session report for every Start/Stop run (TASKS 2026-11-25, A5).

The action report keeps only its last 1000 events; this summary is built from every trace
event as it happens and written once when the run ends: levels done with their times,
pauses with their reason, recoveries, unknown nodes, popups handled and why the run stopped.
It is read-only bookkeeping: nothing here can pause or change a run.
"""
from datetime import datetime
import json
import time
from pathlib import Path

FOLDER = 'debug/run_reports'


class RunSummary:
    def __init__(self, role=None, character=None):
        self.started = datetime.now()
        self.started_monotonic = time.monotonic()
        self.role, self.character = role, character
        self.levels = []            # dict(level, seconds) per confirmed new level
        self.level_mark = self.started_monotonic
        self.prestiges = 0
        self.pauses = []            # dict(reason, detail, at)
        self.errors = []
        self.recoveries = []        # dict(reason, attempt, at)
        self.unknown_nodes = []     # dict(level, node, rarity, category, crop)
        self.popups = 0
        self.popup_samples = []
        self.confirmed_purchases = 0
        self.stop_reason = None
        self.last_level = None

    def at(self):
        return round(time.monotonic() - self.started_monotonic, 1)

    def observe(self, stage, details, state=None):
        if stage == 'new_level_confirmed':
            now = time.monotonic()
            level = details.get('level')
            if level is not None and level == self.last_level:
                return
            self.levels.append(dict(level=level, seconds=round(now - self.level_mark, 1), at=self.at()))
            self.level_mark, self.last_level = now, level
        elif stage == 'prestige_confirmed':
            self.prestiges += 1
            self.level_mark = time.monotonic()
        elif stage in ('supervisor_recovery', 'recovery_game_returned', 'recovery_readonly_reconciled'):
            self.recoveries.append(dict(stage=stage, reason=details.get('reason'), attempt=details.get('attempt'), at=self.at()))
        elif stage == 'recovery_exhausted':
            self.recoveries.append(dict(stage=stage, reason=details.get('reason'), limit=details.get('limit'), at=self.at()))
        elif stage == 'unknown_node':
            self.unknown_nodes.append(dict(level=details.get('level'), node=details.get('node'), rarity=details.get('rarity'),
                                           category=details.get('category'), crop=details.get('crop')))
        elif stage == 'modal_input':
            self.popups += 1
        elif stage == 'popup_sample_saved':
            self.popup_samples.append(dict(label=details.get('label'), path=details.get('path')))
        elif stage == 'failure':
            self.stop_reason = details.get('reason')
        elif stage == 'unexpected_error':
            self.errors.append(dict(at=self.at(), traceback=str(details.get('traceback', ''))[-600:]))
            self.stop_reason = 'unexpected_error'
        elif stage == 'run_counters':
            self.confirmed_purchases = details.get('confirmed_purchases', 0)
        elif stage == 'state':
            message = details.get('message')
            if state == 'paused' and message and message != 'interrupted':
                self.pauses.append(dict(reason=message, at=self.at()))
            elif state == 'error':
                self.errors.append(dict(at=self.at(), message=message))
            elif state in ('stopped', 'complete', 'completed', 'idle') and message:
                self.stop_reason = self.stop_reason or message
            if message and state in ('paused', 'stopped'):
                self.stop_reason = self.stop_reason or ('stopped_by_user' if message == 'interrupted' else message)

    def data(self, final_state=None):
        times = [l['seconds'] for l in self.levels]
        return dict(
            started=self.started.isoformat(timespec='seconds'), duration_seconds=self.at(),
            role=self.role, character=self.character,
            levels_done=len(self.levels), prestiges=self.prestiges,
            level_seconds=dict(mean=round(sum(times) / len(times), 1) if times else None,
                               max=max(times) if times else None),
            levels=self.levels, confirmed_purchases=self.confirmed_purchases,
            pauses=self.pauses, errors=self.errors, recoveries=self.recoveries,
            unknown_nodes=self.unknown_nodes, popups_handled=self.popups, popup_samples=self.popup_samples,
            stop_reason=self.stop_reason or final_state, final_state=final_state)

    def text(self, final_state=None):
        d = self.data(final_state)
        lines = [f"AutoBloodWeb run {d['started']}  ({d['duration_seconds']} s)  {d['role'] or ''} {d['character'] or ''}".rstrip(),
                 f"Levels done: {d['levels_done']}   prestiges: {d['prestiges']}   confirmed purchases: {d['confirmed_purchases']}",
                 f"Per-level time: mean {d['level_seconds']['mean']} s, max {d['level_seconds']['max']} s",
                 f"Pauses: {len(d['pauses'])}   errors: {len(d['errors'])}   recoveries: {len(d['recoveries'])}",
                 f"Unknown nodes: {len(d['unknown_nodes'])}   popups handled: {d['popups_handled']}   new popup samples: {len(d['popup_samples'])}",
                 f"Stop reason: {d['stop_reason']}", '']
        if d['levels']:
            lines.append('Levels: ' + ', '.join(f"{l['level']}:{l['seconds']}s" for l in d['levels']))
        lines += [f"Pause at {p['at']} s: {p['reason']}" for p in d['pauses']]
        lines += [f"Error at {e['at']} s: {e.get('message') or e.get('traceback', '')[-200:]}" for e in d['errors']]
        lines += [f"Recovery at {r['at']} s: {r['stage']} {r.get('reason')}" for r in d['recoveries']]
        lines += [f"Unknown node L{u['level']} #{u['node']} {u['rarity']} {u['category']}: {u['crop']}" for u in d['unknown_nodes']]
        return '\n'.join(lines) + '\n'

    def write(self, root, final_state=None):
        folder = Path(root) / FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        stem = self.started.strftime('%Y%m%d_%H%M%S')
        (folder / f'{stem}.json').write_text(json.dumps(self.data(final_state), ensure_ascii=False, indent=1, default=str), encoding='utf-8')
        (folder / f'{stem}.txt').write_text(self.text(final_state), encoding='utf-8')
        return folder / f'{stem}.txt'
