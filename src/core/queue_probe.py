"""Queue probe (TASKS 2026-11-25, C1): which passive signal tells that DBD found a match?

For a chosen duration, once a second, it records with timestamps into debug/queue_probe/<date_time>/:
  * log: bytes appended to DeadByDaylight.log (the content is encrypted; only growth is usable), rotation;
  * net: the game's TCP rows (new / gone remote endpoints) and UDP local endpoints (new / gone);
  * audio: the peak level of the game's audio session (max of ~10 reads in the second) and its state;
  * window: found, foreground, minimised, title; plus who owns the foreground (to prove we never change it);
  * capture: a Windows.Graphics.Capture frame of the game window at ~1 fps: did it arrive, did it change,
    does the lobby detector (READY/CANCEL button) see the lobby, its mean brightness;
  * markers: the moments the player pressed the marker key ("I hear / see the match found").
Nothing is sent to the game, focus is never changed, the game process is never opened.
SignalSampler is shared with the match-found notifier (src/gui/match_alert_page.py).
"""
import json
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from src.core import queue_signals as qs

AUDIO_READS = 10           # audio reads per second (the found-match sound is short)
DEFAULT_MINUTES = 20
MARKER_KEY = 'CTRL+SHIFT+M'


class SignalSampler:
    """One record of every passive signal per call to sample(), which takes about one second."""

    def __init__(self, root, capture=True, clock=time.monotonic, wait=None):
        self.root = Path(root); self.capture_enabled = capture; self.clock = clock; self.wait = wait or (lambda s: time.sleep(s) or False)
        self.log = qs.LogGrowth(); self.meter = None; self.capture = None; self.backend = None; self.layout = None
        self.previous_tcp = set(); self.previous_udp = set(); self.previous_frame = None; self.pid = None
        self.foreground_before = None; self.calls = 0; self.notes = []
        self.counts = dict(foreground_changes=0, capture_ok=0, capture_failed=0)

    def open(self):
        try: self.meter = qs.AudioMeter()
        except OSError as exc: self.notes.append(f'audio meter unavailable: {exc}')
        if self.capture_enabled:
            from src.core.match_reader import load_layout
            from src.core.screen_manager import WindowsBackend
            self.backend = WindowsBackend(); self.layout = load_layout(self.root)

    def close(self):
        if self.capture is not None:
            try: self.capture.close()
            except Exception: pass
            self.capture = None
        if self.meter is not None:
            try: self.meter.close()
            except Exception: pass
            self.meter = None

    def sample(self):
        started = self.clock(); record = dict(kind='tick', at=datetime.now().isoformat(timespec='milliseconds'))
        if self.pid is None or self.calls % 5 == 0: self.pid = qs.game_pid()   # survives a game restart
        self.calls += 1; pid = self.pid; record['pid'] = pid
        record['log'] = self.log.poll()
        if pid:
            tcp = qs.tcp_rows(pid); udp = qs.udp_rows(pid)
            record['net'] = dict(tcp=len(tcp), udp=len(udp),
                                 tcp_new=sorted(f'{r[1]} {r[2]}' for r in tcp - self.previous_tcp), tcp_gone=sorted(f'{r[1]} {r[2]}' for r in self.previous_tcp - tcp),
                                 udp_new=sorted(udp - self.previous_udp), udp_gone=sorted(self.previous_udp - udp))
            self.previous_tcp, self.previous_udp = tcp, udp
            record['window'] = qs.window_state(pid)
        else:
            self.previous_tcp, self.previous_udp = set(), set()
        foreground = qs.foreground_snapshot(); record['foreground_pid'] = foreground[1]
        if self.foreground_before is not None and foreground != self.foreground_before:
            self.counts['foreground_changes'] += 1; record['foreground_changed'] = True
        self.foreground_before = foreground
        if self.backend is not None and pid: self.read_capture(record)
        peaks = []; states = set()
        while self.clock() - started < 1.:   # the rest of this second: audio reads, ~AUDIO_READS per second
            if self.meter is not None and pid:
                try:
                    reading = self.meter.read(pid); peaks.append(reading['peak'])
                    if reading['state']: states.add(reading['state'])
                except OSError: pass
            if self.wait(max(0., (1. / AUDIO_READS) - .005)): break
        record['audio'] = dict(peak=max(peaks) if peaks else None, mean=round(float(np.mean(peaks)), 4) if peaks else None, states=sorted(states))
        return record

    def read_capture(self, record):
        """One WGC frame of the game window: does it render while covered, and is it the lobby?"""
        from src.core.match_reader import is_lobby
        from src.core.screen_manager import ScreenError
        try:
            window = self.backend.info()
            if self.capture is None or self.capture.signature != window.signature:
                if self.capture is not None: self.capture.close()
                from src.core.window_probe_capture import WindowProbeCapture
                self.capture = WindowProbeCapture(window, bounds=self.backend.probe_bounds)
            image, _ = self.capture.grab(window, timeout=.8)
            small = np.ascontiguousarray(image[::8, ::8]).astype(np.int16)
            changed = None if self.previous_frame is None or self.previous_frame.shape != small.shape else round(float(np.mean(np.abs(small - self.previous_frame))), 2)
            record['capture'] = dict(ok=True, lobby=bool(is_lobby(image, self.layout)), brightness=round(float(image.mean()), 1), change=changed)
            self.counts['capture_ok'] += 1; self.previous_frame = small
        except (ScreenError, OSError, RuntimeError, ImportError) as exc:
            record['capture'] = dict(ok=False, error=getattr(exc, 'code', type(exc).__name__), detail=str(getattr(exc, 'detail', exc))[:200])
            self.counts['capture_failed'] += 1


class QueueProbe:
    def __init__(self, root, minutes=DEFAULT_MINUTES, capture=True, clock=time.monotonic, on_finished=None):
        self.root = Path(root); self.seconds = max(1, int(minutes * 60)); self.capture_enabled = capture
        self.clock = clock; self.on_finished = on_finished
        self.folder = self.root / 'debug/queue_probe' / datetime.now().strftime('%Y-%m-%d_%H%M%S')
        self.stop_event = threading.Event(); self.lock = threading.Lock(); self.markers = []
        self.thread = None; self.started = None; self.seconds_done = 0

    # --- control ----------------------------------------------------------------------------
    def start(self):
        self.folder.mkdir(parents=True, exist_ok=True); self.started = self.clock()
        self.thread = threading.Thread(target=self.run, name='QueueProbe', daemon=True); self.thread.start()
        return self.folder

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive() and threading.current_thread() is not self.thread: self.thread.join(5)

    def mark(self, note='marker key'):
        with self.lock:
            self.markers.append(dict(t=round(self.clock() - (self.started or self.clock()), 2), at=datetime.now().isoformat(timespec='milliseconds'), note=note))

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def remaining(self):
        return 0 if self.started is None else max(0, int(self.seconds - (self.clock() - self.started)))

    # --- the loop ---------------------------------------------------------------------------
    def write(self, record):
        with open(self.folder / 'events.jsonl', 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')

    def run(self):
        sampler = SignalSampler(self.root, self.capture_enabled, self.clock, wait=self.stop_event.wait)
        try:
            sampler.open()
            for note in sampler.notes: self.write(dict(kind='note', text=note))
            self.write(dict(kind='start', at=datetime.now().isoformat(timespec='seconds'), seconds=self.seconds,
                            log_folder=str(qs.log_folder()), marker_key=MARKER_KEY, capture=self.capture_enabled))
            while not self.stop_event.is_set() and self.clock() - self.started < self.seconds:
                t = round(self.clock() - self.started, 2); record = sampler.sample(); record['t'] = t
                with self.lock:
                    record['markers'] = [{k: v for k, v in m.items() if k != 'written'} for m in self.markers if not m.get('written')]
                    for m in self.markers: m['written'] = True
                self.write(record); self.seconds_done += 1
        except Exception as exc:
            import traceback
            self.write(dict(kind='error', text=repr(exc), traceback=traceback.format_exc()[-2000:]))
        finally:
            sampler.close()
            summary = dict(kind='end', at=datetime.now().isoformat(timespec='seconds'), seconds=self.seconds_done, **sampler.counts,
                           markers=[{k: v for k, v in m.items() if k != 'written'} for m in self.markers])
            self.write(summary)
            (self.folder / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
            if self.on_finished:
                try: self.on_finished(self.folder)
                except Exception: pass
