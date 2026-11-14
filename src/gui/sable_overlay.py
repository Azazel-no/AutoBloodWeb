"""Sable Detector (block G, experimental): the alert window and its controller.

Screen capture only. While a HUD portrait is Sable's, a worker thread looks for people a few
times a second and scores them against the outfit library; the alert is a hand-drawn red circle
that follows her and blinks, a photo beside her and a looping siren. Click-through, topmost,
only in a match and only while DBD is in front.
"""
import logging
import random
import threading
import time

import cv2
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPolygonF

from src.core import sable_detector as sd
from src.core.match_reader import hud_portraits
from src.core.sable_model import ModelFetcher
from src.core.screen_manager import Rect, ScreenError
from src.gui.chase_overlay import load_frame, place
from src.gui.match_overlay import PerkOverlay, foreground_handle
from src.gui.meme_audio import LoopSound
from src.gui.meme_watch import MatchWatch

log = logging.getLogger(__name__)
TICK_ALERT, TICK_WATCH, TICK_IDLE = 33, 100, 500   # ms
DETECT_EVERY = .28           # seconds: at most 3-4 detections a second
POLL_EVERY = 1.
DETECT_WIDTH = 1280          # frames are scaled down to this width for the detector
PREVIEW_SECONDS = 6.
RED = QColor(235, 16, 28)


class SableWindow(PerkOverlay):
    """Covers the client; draws the circle (when it is on) and the photo."""
    def __init__(self, root):
        super().__init__(root); self.polygon = None; self.ellipse = None; self.circle_on = False; self.photo = QImage(); self.photo_rect = None; self.ratio = 1.

    def present(self, client, polygon, ellipse, circle_on, photo, photo_rect):
        self.polygon, self.ellipse, self.circle_on, self.photo, self.photo_rect = polygon, ellipse, circle_on, photo, photo_rect
        place(self, client, (0, 0, client.width, client.height)); self.ratio = client.width / max(1, self.width())

    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing); p.setRenderHint(QPainter.SmoothPixmapTransform); r = self.ratio
        if self.photo_rect and not self.photo.isNull():
            x, y, w, h = self.photo_rect; p.drawImage(QRectF(x / r, y / r, w / r, h / r), self.photo)
        if self.circle_on and self.polygon and self.ellipse:
            cx, cy, rw, rh = self.ellipse; p.setPen(Qt.NoPen); p.setBrush(RED)
            p.drawPolygon(QPolygonF([QPointF((cx + x * rw) / r, (cy + y * rh) / r) for x, y in self.polygon]), Qt.WindingFill)


class SableController(QObject):
    changed = Signal()
    detected = Signal(object)      # from the worker thread: (hit or None, all candidates, capture time)
    model_ready = Signal()

    def __init__(self, root, settings, hub, is_killer, automation_active, backend=None, window=None, clock=time.monotonic, sound=LoopSound,
                 watch=None, detector=None, library=None, templates=None, fetcher=None, foreground=None, rng=None, threaded=True):
        super().__init__()
        self.root = root; self.settings = settings; self.hub = hub; self.automation_active = automation_active; self.clock = clock
        self.watch = watch or MatchWatch(root, backend, is_killer, clock, name='Sable Detector'); self.backend = self.watch.backend
        self.window = window or SableWindow(root); self.sound = sound; self.siren = None; self.rng = rng or random.Random(); self.foreground = foreground or foreground_handle
        self.detector = detector; self.library = library; self.threaded = threaded
        self.gate = sd.SableGate(sd.load_gate_templates(root) if templates is None else templates); self.alert = sd.Alert(); self.smooth = sd.SmoothBox()
        self.fetcher = fetcher or ModelFetcher(root, lambda path: self.model_ready.emit())
        self.polygon = None; self.photo = QImage(); self.photo_rect = None; self.enabled = False; self.preview_until = None
        self.next_poll = self.next_detect = 0.; self.worker = None; self.stop_worker = threading.Event(); self.want = threading.Event(); self.last_scores = ''
        self.timer = QTimer(self); self.timer.setInterval(TICK_IDLE); self.timer.timeout.connect(self.tick)
        self.detected.connect(self.on_result); self.model_ready.connect(self.load_model); hub.volume_changed.connect(self.apply_volume)

    # --- lifecycle --------------------------------------------------------------------------
    def enable(self):
        self.enabled = True; self.hub.use('sable', True); self.hub.reload(); self.load_library(); self.load_model(); self.timer.start()
        if self.threaded and self.worker is None:
            self.stop_worker.clear(); self.worker = threading.Thread(target=self.work, name='SableDetector', daemon=True); self.worker.start()
        self.changed.emit()

    def disable(self):
        self.enabled = False; self.timer.stop(); self.want.clear(); self.stop_worker.set()
        if self.worker and self.worker.is_alive(): self.worker.join(1.5)
        self.worker = None; self.hide('switched off'); self.alert.reset(); self.gate.reset(); self.hub.use('sable', False)
        if self.siren: self.siren.close(); self.siren = None
        self.changed.emit()

    def load_library(self):
        path = self.root / 'assets/sable/library.npz'
        if self.library is None and path.is_file():
            try: self.library = sd.Library.load(path)
            except Exception: log.exception('Sable Detector: the outfit library is unreadable')

    def load_model(self):
        if self.detector is not None or not self.enabled: return
        path = self.fetcher.request()
        if path is None: return
        try: self.detector = sd.PersonDetector(path); log.info('Sable Detector: person model loaded')
        except Exception: log.exception('Sable Detector: the person model could not be loaded')
        self.changed.emit()

    @property
    def ready(self):
        return self.detector is not None and self.library is not None and self.library.ready

    def game_foreground(self):
        return bool(self.watch.game_hwnd) and self.foreground() == self.watch.game_hwnd

    @property
    def alerting(self):
        return self.alert.state == sd.ALERT or self.preview_until is not None

    # --- detection --------------------------------------------------------------------------
    def capture_ok(self, now):
        """Frames are taken while the circle is off, so our own drawing is never in them."""
        return self.alert.state != sd.ALERT or not sd.blink_on(now - self.alert.shown_at)

    def detect_once(self):
        """One capture and one pass of the detector. Runs on the worker thread (or inline in tests)."""
        client = self.watch.client; now = self.clock()
        if client is None or not self.ready or not self.capture_ok(now): return
        image, _ = self.backend.grab(Rect(client.x, client.y, client.width, client.height))
        scale = min(1., DETECT_WIDTH / image.shape[1])
        people = self.detector.detect(image if scale == 1. else cv2.resize(image, None, fx=scale, fy=scale))
        people = [(tuple(round(v / scale) for v in box), confidence) for box, confidence in people]
        ignore = [self.photo_rect] if self.photo_rect else []
        hit, hits = sd.find_sable(image, people, self.library, self.settings['threshold'] / 100, self.watch.current_role(), ignore)
        self.detected.emit((hit, hits, now))

    def work(self):
        seen = set()
        while not self.stop_worker.wait(DETECT_EVERY):
            if not self.want.is_set(): continue
            try: self.detect_once()
            except ScreenError: pass
            except Exception as exc:
                key = (type(exc).__name__, str(exc)[:120])
                if key not in seen: seen.add(key); log.exception('Sable Detector worker failed; continuing')
                self.stop_worker.wait(1.)

    def on_result(self, result):
        hit, hits, at = result
        if not self.want.is_set() or self.preview_until is not None: return
        scores = ' '.join(f'{h.score:.2f}(h{h.parts["head"]:.2f} t{h.parts["torso"]:.2f} l{h.parts["legs"]:.2f})' for h in sorted(hits, key=lambda h: -h.score)[:4])
        if scores != self.last_scores and hits: self.last_scores = scores; log.info('Sable Detector scores: %s, threshold %.2f', scores, self.settings['threshold'] / 100)
        event = self.alert.feed(hit.box if hit else None, self.clock())
        if hit and self.alert.state == sd.ALERT: self.smooth.update(hit.box)
        if event == 'show': self.show(hit)
        elif event == 'hide': self.hide('Sable not seen for %.1f s' % sd.GONE_SECONDS)

    # --- alert ------------------------------------------------------------------------------
    def show(self, hit=None, reason=None):
        seed = self.rng.randrange(1 << 30); self.polygon = sd.hand_circle(seed)[0]; photos = self.hub.pack.photos
        self.photo = load_frame(self.rng.choice(photos)) if photos else QImage()
        if hit is not None: self.smooth.reset(); self.smooth.update(hit.box)
        log.info('Sable Detector ALERT: %s', reason or f'score {hit.score:.2f} on {sd.HITS_TO_START} detections in a row, box {hit.box}')
        if self.siren is None and self.hub.pack.siren: self.siren = self.sound(self.hub.pack.siren, self)
        if self.siren: self.siren.set_volume(self.hub.volume()); self.siren.play()
        self.changed.emit()

    def hide(self, reason):
        visible = self.polygon is not None
        self.polygon = None; self.photo_rect = None; self.smooth.reset(); self.window.hide()
        if self.siren: self.siren.stop()
        if visible: log.info('Sable Detector alert over: %s', reason); self.changed.emit()

    def apply_volume(self):
        if self.siren: self.siren.set_volume(self.hub.volume())

    def paint(self, now, started):
        client = self.watch.client; box = self.smooth.box
        if client is None or box is None or self.polygon is None: return
        aspect = self.photo.width() / max(1, self.photo.height()) if not self.photo.isNull() else 1.
        self.photo_rect = sd.photo_box(client.width, client.height, box, self.settings['photo_size'], aspect) if not self.photo.isNull() else None
        self.window.present(client, self.polygon, sd.circle_box(box), sd.blink_on(now - started), self.photo, self.photo_rect)

    # --- loop -------------------------------------------------------------------------------
    def tick(self):
        try: self.step()
        except ScreenError:
            self.stop_alert('DBD window not found'); self.watch.lost(); self.gate.reset(); self.set_interval(TICK_IDLE)
        except Exception:
            log.exception('Sable Detector step failed'); self.stop_alert('error')

    def set_interval(self, value):
        if self.timer.interval() != value: self.timer.setInterval(value)

    def stop_alert(self, reason):
        self.want.clear()
        if self.alert.stop() or self.polygon is not None: self.hide(reason)

    def step(self):
        now = self.clock()
        if self.preview_until is not None:
            if now >= self.preview_until: self.end_preview()
            else: self.paint(now, self.preview_until - PREVIEW_SECONDS)
            return
        if self.automation_active(): self.stop_alert('Bloodweb automation is running'); return self.set_interval(TICK_IDLE)
        watch = self.watch
        if now >= self.next_poll:
            self.next_poll = now + POLL_EVERY; was = (watch.in_match, self.gate.open); watch.poll()
            if not watch.in_match and self.gate.open: log.info('Sable Detector: match over, gate closed')
            if not watch.in_match: self.gate.reset()
            elif watch.hud_visible and not self.gate.open and self.gate.templates: self.read_gate()
            if was != (watch.in_match, self.gate.open): self.changed.emit()
        if not watch.game_hwnd or not self.game_foreground(): self.stop_alert('DBD is not in front'); return self.set_interval(TICK_IDLE)
        if not (watch.in_match and self.gate.open and self.ready): self.stop_alert('no Sable in this match'); return self.set_interval(TICK_IDLE)
        self.want.set()
        if not self.threaded and now >= self.next_detect: self.next_detect = now + DETECT_EVERY; self.detect_once()
        if self.alert.state == sd.ALERT:
            if self.alert.check(now) == 'hide': self.hide('Sable not seen for %.1f s' % sd.GONE_SECONDS)
            else: self.paint(now, self.alert.shown_at)
        self.set_interval(TICK_ALERT if self.alert.state == sd.ALERT else TICK_WATCH)

    def read_gate(self):
        client = self.watch.client
        band, _ = self.backend.grab(Rect(client.x, client.y, round(client.width * .3), client.height))
        if self.gate.feed(hud_portraits(band, self.watch.layout), self.watch.current_role()):
            log.info('Sable Detector: Sable is in this match (HUD slot %d, portrait score %.2f): detector on', self.gate.slot + 1, self.gate.best)

    # --- preview ----------------------------------------------------------------------------
    def preview(self):
        """Shows the alert for a few seconds around a made-up figure: for checking the look and the volume."""
        if self.preview_until is not None: return self.end_preview()
        try: self.watch.poll()
        except ScreenError: self.watch.lost()
        if self.watch.client is None:
            from types import SimpleNamespace
            from PySide6.QtGui import QGuiApplication
            g = QGuiApplication.primaryScreen().geometry(); r = QGuiApplication.primaryScreen().devicePixelRatio()
            self.watch.client = SimpleNamespace(x=round(g.x() * r), y=round(g.y() * r), width=round(g.width() * r), height=round(g.height() * r))
        client = self.watch.client; now = self.clock(); self.hub.reload()
        h = client.height * .42; box = (client.width * .68, client.height * .30, h * .38, h)
        self.smooth.reset(); self.smooth.update(box); self.show(reason='preview'); self.preview_until = now + PREVIEW_SECONDS
        self.set_interval(TICK_ALERT); self.timer.start()

    def end_preview(self):
        self.preview_until = None; self.hide('preview over')
        if not self.enabled: self.timer.stop()

    def state(self):
        """Key for the tab's status line."""
        if self.library is None or not self.library.ready or self.detector is None: return 'meme_sable_no_model'
        if not self.gate.templates: return 'meme_sable_no_template'
        if self.alert.state == sd.ALERT: return 'meme_sable_state_alert'
        if not self.watch.in_match: return 'meme_sable_state_waiting'
        return 'meme_sable_state_watching' if self.gate.open else 'meme_sable_state_no_sable'
