"""'Match found' alert and the queue probe on the Settings page (TASKS 2026-11-25, C).

The controller watches passive signals on its own thread (src/core/queue_probe.SignalSampler), fuses them
(src/core/match_alert.MatchFoundDetector) and rings a synthesised tone; optionally it sends "Match found"
to the player's own ntfy topic / webhook. It never changes focus and sends no input: the log line
'Match alert: ... foreground changes seen ...' counts foreground changes made by anybody, so a wait with
zero changes proves the app made none.
"""
import logging
import threading
import time
from datetime import datetime

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QSlider, QSpinBox, QVBoxLayout, QWidget

from src.core import match_alert as ma
from src.core.queue_probe import MARKER_KEY, QueueProbe, SignalSampler
from src.gui.widgets import BloodButton, ToggleSwitch

log = logging.getLogger(__name__)


class MatchAlertController(QObject):
    fired = Signal(str)          # reason, from the watcher thread
    state_changed = Signal(str, dict)
    push_done = Signal(bool, str)

    def __init__(self, root, settings, sampler_factory=None, chime=None):
        super().__init__()
        from pathlib import Path
        self.root = Path(root); self.settings = settings; self.sampler_factory = sampler_factory or (lambda: SignalSampler(root, capture=True))
        self.chime = chime or ma.Chime(); self.stop_event = threading.Event(); self.thread = None; self.fired_at = None
        self.fired.connect(self.on_fired)

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def enable(self):
        if self.running: return
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.watch, args=(self.stop_event,), name='MatchAlert', daemon=True); self.thread.start()

    def disable(self):
        self.stop_event.set(); self.chime.acknowledge(); self.state_changed.emit('alert_state_off', {})

    def watch(self, stop):
        detector = ma.MatchFoundDetector(ma.load_strengths(self.root)); sampler = None; state = None; seen_foreground = 0
        try:
            while not stop.is_set():
                if sampler is None:
                    sampler = self.sampler_factory(); sampler.wait = stop.wait; sampler.open()
                    for note in sampler.notes: log.info('Match alert: %s', note)
                record = sampler.sample()
                if stop.is_set(): break
                now_state = 'alert_state_watching' if record.get('pid') else 'alert_state_waiting_game'
                if now_state != state: state = now_state; self.state_changed.emit(state, {}); log.info('Match alert: %s', state)
                if not record.get('pid'): detector = ma.MatchFoundDetector(detector.strengths); continue   # DBD closed or restarting
                if record.get('foreground_changed'): seen_foreground += 1
                fire, reason = detector.feed(record)
                if fire:
                    in_front = (record.get('window') or {}).get('foreground')
                    if in_front and self.settings.get('background_only', True):
                        log.info('Match alert: %s, not played: DBD is in front', reason); continue
                    log.info('Match alert FIRED: %s (foreground changes seen while waiting: %d, made by AutoBloodWeb: 0)', reason, seen_foreground)
                    self.fired.emit(reason)
        except Exception:
            log.exception('Match alert watcher stopped')
        finally:
            if sampler is not None: sampler.close()

    def on_fired(self, reason):
        self.fired_at = datetime.now(); self.ring()
        self.state_changed.emit('alert_state_fired', dict(time=self.fired_at.strftime('%H:%M:%S')))
        if self.settings.get('push') and self.settings.get('push_target'): self.push()

    def ring(self):
        self.chime.ring(self.settings.get('volume', 70) / 100)

    def acknowledge(self):
        self.chime.acknowledge()

    def push(self):
        target = self.settings.get('push_target', '')
        def run():
            ok, detail = ma.send_push(target); log.info('Match alert push: %s (%s)', 'sent' if ok else 'failed', detail); self.push_done.emit(ok, detail)
        threading.Thread(target=run, name='MatchAlertPush', daemon=True).start()


class MatchAlertSection(QWidget):
    """Settings card content: the notifier for everybody, the queue probe in developer mode."""

    def __init__(self, root, settings, save, t, key_reader=None):
        super().__init__()
        self.root, self.settings, self.save, self.t = root, settings, save, t; self.probe = None; self.marker = None; self.key_reader = key_reader
        self.controller = MatchAlertController(root, settings)
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(8)
        self.title = QLabel(); self.title.setObjectName('sectionTitle'); layout.addWidget(self.title)
        self.enabled = ToggleSwitch(); self.enabled.setChecked(settings['enabled']); self.enabled.toggled.connect(self.toggle); layout.addWidget(self.enabled)
        self.about = QLabel(); self.about.setObjectName('muted'); self.about.setWordWrap(True); layout.addWidget(self.about)
        self.background = ToggleSwitch(); self.background.setChecked(settings['background_only'])
        self.background.toggled.connect(lambda v: self.update_setting('background_only', bool(v))); layout.addWidget(self.background)
        row = QHBoxLayout(); self.volume_label = QLabel(); row.addWidget(self.volume_label)
        self.volume = QSlider(Qt.Horizontal); self.volume.setRange(0, 100); self.volume.setValue(settings['volume'])
        self.volume.valueChanged.connect(lambda v: self.update_setting('volume', int(v))); row.addWidget(self.volume, 1)
        self.test = BloodButton(); self.test.clicked.connect(self.controller.ring); row.addWidget(self.test)
        self.ack = BloodButton(); self.ack.clicked.connect(self.controller.acknowledge); row.addWidget(self.ack); layout.addLayout(row)
        self.push = ToggleSwitch(); self.push.setChecked(settings['push']); self.push.toggled.connect(lambda v: self.update_setting('push', bool(v))); layout.addWidget(self.push)
        self.push_label = QLabel(); layout.addWidget(self.push_label)
        row = QHBoxLayout(); self.target = QLineEdit(settings['push_target']); self.target.setMaxLength(300)
        self.target.editingFinished.connect(lambda: self.update_setting('push_target', self.target.text().strip())); row.addWidget(self.target, 1)
        self.push_test = BloodButton(); self.push_test.clicked.connect(self.controller.push); row.addWidget(self.push_test); layout.addLayout(row)
        self.push_hint = QLabel(); self.push_hint.setObjectName('muted'); self.push_hint.setWordWrap(True); layout.addWidget(self.push_hint)
        self.state = QLabel(); self.state.setWordWrap(True); layout.addWidget(self.state)
        # --- queue probe (developer mode) ---
        self.probe_box = QWidget(); box = QVBoxLayout(self.probe_box); box.setContentsMargins(0, 8, 0, 0)
        self.probe_title = QLabel(); self.probe_title.setObjectName('sectionTitle'); box.addWidget(self.probe_title)
        self.probe_about = QLabel(); self.probe_about.setObjectName('muted'); self.probe_about.setWordWrap(True); box.addWidget(self.probe_about)
        row = QHBoxLayout(); self.minutes_label = QLabel(); row.addWidget(self.minutes_label)
        self.minutes = QSpinBox(); self.minutes.setRange(1, 120); self.minutes.setValue(settings['probe_minutes'])
        self.minutes.valueChanged.connect(lambda v: self.update_setting('probe_minutes', int(v))); row.addWidget(self.minutes)
        self.probe_button = BloodButton(); self.probe_button.clicked.connect(self.toggle_probe); row.addWidget(self.probe_button); row.addStretch(); box.addLayout(row)
        self.probe_state = QLabel(); self.probe_state.setWordWrap(True); self.probe_state.setTextInteractionFlags(Qt.TextSelectableByMouse); box.addWidget(self.probe_state)
        layout.addWidget(self.probe_box)
        self.state_key = ('alert_state_off', {}); self.probe_timer = QTimer(self); self.probe_timer.setInterval(1000); self.probe_timer.timeout.connect(self.show_probe)
        self.controller.state_changed.connect(self.show_state); self.controller.push_done.connect(self.show_push)
        self.retranslate(t)
        if settings['enabled']: self.controller.enable()

    def update_setting(self, key, value):
        self.settings[key] = value; self.save()

    def toggle(self, value):
        self.update_setting('enabled', bool(value)); (self.controller.enable if value else self.controller.disable)()

    def show_state(self, key, values):
        self.state_key = (key, values); self.state.setText(self.t(key, **values))

    def show_push(self, ok, detail):
        self.state.setText(self.t('alert_push_sent' if ok else 'alert_push_failed', detail=detail))

    def set_developer(self, enabled):
        self.probe_box.setVisible(bool(enabled))

    def toggle_probe(self):
        if self.probe is not None and self.probe.running:
            self.probe.stop(); return self.show_probe()
        self.probe = QueueProbe(self.root, minutes=self.settings['probe_minutes'])
        folder = self.probe.start(); log.info('Queue probe started: %s (%d min)', folder, self.settings['probe_minutes'])
        from src.gui.memes_page import ComboWatcher
        if self.marker: self.marker.close()
        self.marker = ComboWatcher(MARKER_KEY, lambda: self.probe and self.probe.mark('marker key'), read=self.key_reader); self.marker.thread.name = 'QueueProbeMarker'; self.marker.start()
        self.probe_timer.start(); self.show_probe()

    def show_probe(self):
        probe = self.probe
        if probe is None: self.probe_state.setText(''); self.probe_button.setText(self.t('probe_start')); return
        if probe.running:
            self.probe_button.setText(self.t('probe_stop'))
            self.probe_state.setText(self.t('probe_running', seconds=probe.remaining(), markers=len(probe.markers), folder=str(probe.folder)))
        else:
            self.probe_timer.stop(); self.probe_button.setText(self.t('probe_start'))
            if self.marker: self.marker.close(); self.marker = None
            self.probe_state.setText(self.t('probe_done', folder=str(probe.folder)))

    def retranslate(self, t):
        self.t = t
        self.title.setText(t('alert_title')); self.enabled.setText(t('alert_enable')); self.about.setText(t('alert_about'))
        self.background.setText(t('alert_background_only')); self.volume_label.setText(t('alert_volume'))
        self.test.setText(t('alert_test')); self.ack.setText(t('alert_ack')); self.push.setText(t('alert_push'))
        self.push_label.setText(t('alert_push_target')); self.push_test.setText(t('alert_push_test')); self.push_hint.setText(t('alert_push_hint'))
        self.probe_title.setText(t('probe_title')); self.probe_about.setText(t('probe_about', hotkey=MARKER_KEY)); self.minutes_label.setText(t('probe_minutes'))
        self.show_state(*self.state_key); self.show_probe()

    def close(self):
        self.controller.disable()
        if self.probe is not None: self.probe.stop()
        if self.marker: self.marker.close()
