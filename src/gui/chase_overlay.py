"""Chase meme (block F3): the timer strip, the dancer and the controller that runs them.

Screen capture only: two small regions (the horns beside the own portrait, the glow on the
character's back) are read while a survivor match is on screen. The windows are click-through,
topmost and shown only while DBD is in front. Nothing is sent to the game.
"""
import logging
import random
import threading
import time

import cv2
import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QImage, QPainter

from src.core import chase_meme as cm
from src.core.screen_manager import Rect, ScreenError
from src.gui.match_overlay import PerkOverlay, foreground_handle
from src.gui.meme_audio import Sound
from src.gui.meme_watch import MatchWatch

log = logging.getLogger(__name__)
TICK_CHASE, TICK_MATCH, TICK_IDLE = 33, 100, 500   # ms: painting a chase, watching the horns, waiting for a match
HORN_EVERY, HEART_EVERY, POLL_EVERY, DOWN_EVERY = .1, .2, 1., .3   # seconds between reads
HUD_LOST = 2.5               # seconds without the match HUD before a running chase is stopped
STRIP_W, STRIP_H, MARGIN = 330, 72, 22             # the timer strip at 1080p
CENTRE_KEEPOUT = .62         # the dancer never starts left of this share of the width
PREVIEW_SECONDS = 14.


def timer_box(client_width, client_height, size=1.):
    """(x, y, w, h) inside the client: the top-right corner, scaled with the window height."""
    s = client_height / 1080 * size; w, h, m = round(STRIP_W * s), round(STRIP_H * s), round(MARGIN * client_height / 1080)
    return max(0, client_width - w - m), m, min(w, client_width), h


def dance_box(client_width, client_height, settings, aspect):
    """(x, y, w, h) of the dancer: centred on the configured point, kept right of the screen centre and inside the client.

    `aspect` is the clip's width / height."""
    h = round(client_height * settings['dance_size'] / 100); w = round(h * aspect)
    limit = client_width * (1 - CENTRE_KEEPOUT)
    if w > limit: w = round(limit); h = round(w / aspect)
    x = round(client_width * settings['dance_x'] / 100 - w / 2); y = round(client_height * settings['dance_y'] / 100 - h / 2)
    x = min(max(x, round(client_width * CENTRE_KEEPOUT)), client_width - w); y = min(max(y, 0), client_height - h)
    return x, y, w, h


def place(window, client, box):
    """Moves an overlay to a physical box inside the client; Qt geometry is logical."""
    screen = QGuiApplication.screenAt(QPointF(client.x, client.y).toPoint()) or QGuiApplication.primaryScreen()
    r = screen.devicePixelRatio() if screen else 1.; x, y, w, h = box
    geometry = (round((client.x + x) / r), round((client.y + y) / r), max(1, round(w / r)), max(1, round(h / r)))
    if window.geometry().getRect() != geometry: window.setGeometry(*geometry)
    if not window.isVisible(): window.show()
    window.update()


class TimerWindow(PerkOverlay):
    """Thin white digits on a black strip, with the reference clip's kick-and-settle glitch."""
    def __init__(self, root):
        super().__init__(root); self.seconds = 0.; self.alpha = 1.

    def present(self, client, seconds, size, alpha):
        self.seconds = seconds; self.alpha = alpha; place(self, client, timer_box(client.width, client.height, size))

    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing); p.setRenderHint(QPainter.TextAntialiasing); p.setOpacity(self.alpha)
        w, h = self.width(), self.height(); p.fillRect(QRectF(0, 0, w, h), QColor(0, 0, 0)); p.setClipRect(QRectF(0, 0, w, h))
        g = cm.timer_glitch(self.seconds); digits = h * .66
        font = QFont('Segoe UI Light'); font.setWeight(QFont.Light); font.setPixelSize(100)
        fit = min(w * .86 / max(1., QFontMetricsF(font).horizontalAdvance("00:00.00")), h * .0094)   # The digits fill the strip, as in the reference.
        font.setPixelSize(max(6, round(100 * fit * g.zoom))); p.setFont(font)
        box = QRectF(g.dx * digits, g.dy * digits, w, h); text = cm.format_timer(self.seconds); level = round(255 * g.light)
        if g.fringe > 0:   # Colour fringes of a fast move.
            for colour, shift in ((QColor(255, 60, 60, 110), -2.5), (QColor(60, 220, 255, 110), 2.5)):
                p.setPen(colour); p.drawText(box.translated(shift * g.fringe, shift * g.fringe * .6), Qt.AlignCenter, text)
        if g.smear > 0:    # Vertical motion blur: faint copies along the move.
            steps = 18; reach = digits * .30 * g.smear
            for index in range(steps):
                p.setPen(QColor(level, level, level, 34)); p.drawText(box.translated(0, reach * (index / (steps - 1) - .5) * 2), Qt.AlignCenter, text)
        else:
            p.setPen(QColor(level, level, level)); p.drawText(box, Qt.AlignCenter, text)


def load_frame(path):
    """A transparent dance frame as a QImage; an unreadable file is an empty image."""
    data = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_UNCHANGED)
    if data is None: return QImage()
    if data.ndim == 2: data = cv2.cvtColor(data, cv2.COLOR_GRAY2BGRA)
    elif data.shape[2] == 3: data = cv2.cvtColor(data, cv2.COLOR_BGR2BGRA)
    data = np.ascontiguousarray(data)
    return QImage(data.data, data.shape[1], data.shape[0], data.strides[0], QImage.Format_ARGB32).copy()


class FrameLoader:
    """Decodes a dance clip's frames a little ahead of the playhead on its own thread (a frame takes ~14 ms to decode)."""
    AHEAD = 12

    def __init__(self, frames, decode=load_frame):
        self.frames = frames; self.decode = decode; self.ready = {}; self.playhead = 0; self.lock = threading.Lock(); self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, name='MemeDance', daemon=True); self.thread.start()

    def run(self):
        index = 0
        while not self.stop.is_set() and index < len(self.frames):
            with self.lock: playhead = self.playhead
            if index < playhead: index = playhead; continue          # Fell behind: skip to what is needed now.
            if index > playhead + self.AHEAD: self.stop.wait(.005); continue
            image = self.decode(self.frames[index])
            with self.lock:
                self.ready[index] = image
                for old in [k for k in self.ready if k < playhead - 1]: del self.ready[old]
            index += 1

    def frame(self, index):
        """The decoded frame for `index`, or the newest earlier one that is ready; None before the first."""
        with self.lock:
            self.playhead = index
            best = max((k for k in self.ready if k <= index), default=None)
            return self.ready[best] if best is not None else None

    def close(self):
        self.stop.set()


class DanceWindow(PerkOverlay):
    def __init__(self, root):
        super().__init__(root); self.image = QImage(); self.alpha = 1.

    def present(self, client, image, box, alpha=1.):
        self.image = image; self.alpha = alpha; place(self, client, box)

    def paintEvent(self, event):
        if self.image.isNull(): return
        p = QPainter(self); p.setRenderHint(QPainter.SmoothPixmapTransform); p.setOpacity(self.alpha); p.drawImage(QRectF(0, 0, self.width(), self.height()), self.image)


class ChaseController(QObject):
    """Watches for a chase in a survivor match and plays the timer, music, voice lines and dances."""
    changed = Signal()

    def __init__(self, root, settings, hub, is_killer, automation_active, backend=None, timer_window=None, dance_window=None,
                 clock=time.monotonic, sound=Sound, rng=None, watch=None, foreground=None, loader=FrameLoader):
        super().__init__()
        self.root = root; self.settings = settings; self.hub = hub; self.automation_active = automation_active; self.clock = clock
        self.watch = watch or MatchWatch(root, backend, is_killer, clock, name='Chase meme'); self.backend = self.watch.backend
        self.timer_window = timer_window or TimerWindow(root); self.dance_window = dance_window or DanceWindow(root)
        self.sound = sound; self.loader = loader; self.frames = None; self.rng = rng or random.Random(); self.foreground = foreground or foreground_handle
        self.detector = cm.ChaseDetector(); self.horns = cm.HornReader(); self.show = cm.ChaseShow(rng=self.rng)
        self.music = None; self.voices = {}; self.dance_sounds = {}; self.dance = None; self.dance_at = None; self.dance_sound = None
        self.next_poll = self.next_horn = self.next_heart = self.next_down = 0.; self.preview_until = None; self.enabled = False; self.chase_at = None; self.last_reason = ''
        self.fading = None            # (fade started at, the timer's last value) while the sound and the windows fade out
        self.down = cm.DownWatch(); self.down_templates = None
        self.timer = QTimer(self); self.timer.setInterval(TICK_IDLE); self.timer.timeout.connect(self.tick)
        hub.volume_changed.connect(self.apply_volume); hub.pack_changed.connect(self.load_pack)

    # --- lifecycle --------------------------------------------------------------------------
    def enable(self):
        self.enabled = True; self.hub.use('chase', True); self.hub.reload(); self.timer.start()

    def disable(self):
        self.enabled = False; self.timer.stop(); self.end('switched off', fade=False); self.hub.use('chase', False); self.close_sounds()

    def close_sounds(self):
        for sound in [*self.voices.values(), *self.dance_sounds.values()] + (self.music.players if self.music else []): sound.close()
        self.voices = {}; self.dance_sounds = {}; self.music = None

    def load_pack(self):
        """New pack contents: the players are rebuilt on the next chase."""
        if not self.active: self.close_sounds()
        pack = self.hub.pack; self.show.voices = list(pack.voices); self.show.dances = list(pack.dances)

    def prepare_sounds(self):
        pack = self.hub.pack
        if self.music is None and pack.music:
            crossfade = .35
            try:
                import json
                value = json.loads((pack.folder / 'pack.json').read_text(encoding='utf-8')).get('music', {}).get('loop_crossfade')
                if isinstance(value, (int, float)) and 0 < value <= 3: crossfade = float(value)
            except (OSError, ValueError, AttributeError): pass
            self.music = cm.MusicLoop(self.sound(pack.music, self), self.sound(pack.music, self), crossfade)
        for voice in pack.voices:
            if voice.name not in self.voices: self.voices[voice.name] = self.sound(voice.path, self)
        for dance in pack.dances:
            if dance.audio and dance.name not in self.dance_sounds: self.dance_sounds[dance.name] = self.sound(dance.audio, self)

    @property
    def active(self):
        return self.chase_at is not None

    def game_foreground(self):
        return bool(self.watch.game_hwnd) and self.foreground() == self.watch.game_hwnd

    # --- loop -------------------------------------------------------------------------------
    def tick(self):
        try: self.step()
        except ScreenError:
            self.end('DBD window not found'); self.watch.lost(); self.set_interval(TICK_IDLE)
        except Exception:
            log.exception('Chase meme step failed'); self.end('error', fade=False)

    def set_interval(self, value):
        if self.timer.interval() != value: self.timer.setInterval(value)

    def step(self):
        now = self.clock()
        if self.automation_active():   # Any window over the game counts as a cover for purchase checks.
            self.end('Bloodweb automation is running', fade=False); return self.set_interval(TICK_IDLE)
        if self.fading is not None: self.fade_step(now)
        if self.preview_until is not None:
            if now >= self.preview_until: self.end('preview over')
            else: self.play(now)
            return
        if now >= self.next_poll:
            self.next_poll = now + POLL_EVERY; was = self.watch.survivor_match; self.watch.poll()
            if was != self.watch.survivor_match or self.active: self.changed.emit()
        watch = self.watch
        if not watch.game_hwnd or not self.game_foreground():
            self.end('DBD is not in front'); return self.set_interval(TICK_IDLE)
        if not watch.survivor_match:
            self.end('not a survivor match'); self.horns.reset(); return self.set_interval(TICK_IDLE)
        if self.active and (watch.hud_seen_at is None or now - watch.hud_seen_at > HUD_LOST):
            self.end(f'match HUD not seen for {HUD_LOST} s (menu, scoreboard or end screen)')
        client = watch.client; event = None
        if watch.hud_visible and now >= self.next_horn:
            self.next_horn = now + HORN_EVERY; x, y, w, h = cm.horn_rect(watch.layout, client.width, client.height)
            region, _ = self.backend.grab(Rect(client.x + x, client.y + y, w, h)); event = self.detector.horns(self.horns.read(region), now)
        if event is None and self.detector.state == cm.CHASE and now >= self.next_heart:
            self.next_heart = now + HEART_EVERY; x, y, w, h = cm.heart_rect(client.width, client.height)
            region, _ = self.backend.grab(Rect(client.x + x, client.y + y, w, h)); event = self.detector.heart(cm.heart_seen(region, client.height), now)
        if event is None and self.detector.state == cm.CHASE and watch.hud_visible and now >= self.next_down:
            self.next_down = now + DOWN_EVERY; state = self.down.feed(self.own_status(client))
            if state: event = self.detector.stop(now, f'own portrait shows {state}')
        if event and event[0] == 'start': self.begin(now, event[1])
        elif event and event[0] == 'stop': self.end(event[1], detector=False)
        if self.active: self.play(now)
        self.set_interval(TICK_CHASE if self.active or self.fading is not None else TICK_MATCH)

    def own_status(self, client):
        """The top HUD portrait's status (dying, carried, hooked, dead, other), as the 1v1 timer reads it."""
        from src.core.killer_status import column_rect, read_column
        from src.core.onevone import load_down_templates
        if self.down_templates is None: self.down_templates = load_down_templates(self.root)
        layout = self.watch.layout; x, y, w, h = column_rect(layout, client.width, client.height)
        image, _ = self.backend.grab(Rect(client.x + x, client.y + y, w, h))
        return read_column(image, layout, client.width, client.height, self.down_templates)[0][0]

    # --- the show ---------------------------------------------------------------------------
    def begin(self, started_at, reason):
        if self.fading is not None: self.stop_all()   # A new chase during the fade starts from the beginning.
        log.info('Chase meme START: %s', reason); self.down.reset()
        self.chase_at = started_at; self.last_reason = reason; self.prepare_sounds(); self.show.voices = list(self.hub.pack.voices); self.show.dances = list(self.hub.pack.dances)
        self.show.start(started_at)
        if self.music: self.music.start(self.hub.volume())
        self.changed.emit()

    def end(self, reason, detector=True, fade=True):
        """The chase is over: the sound and the windows fade out over the configured 3-5 s, then everything
        stops. `fade=False` (switched off, Bloodweb automation, an error) stops at once. The next chase
        always starts fresh at 00:00.00."""
        if detector and self.detector.state == cm.CHASE: self.detector.stop(self.clock(), reason)
        if not (self.active or self.preview_until is not None):
            if not fade and self.fading is not None: self.stop_all()
            return
        now = self.clock()
        if self.preview_until is not None: self.show.dance_chance = cm.DANCE_CHANCE
        log.info('Chase meme STOP: %s (timer %s)%s', reason, cm.format_timer(now - self.chase_at), ', fading out' if fade else '')
        self.fading = (now, now - self.chase_at)
        self.chase_at = None; self.preview_until = None; self.show.stop(); self.down.reset()
        self.last_reason = reason; self.changed.emit()
        if fade: self.fade_step(now)
        else: self.stop_all()

    def fade_seconds(self):
        return self.settings.get('fade', round(cm.FADE_SECONDS * 10)) / 10

    def fade_step(self, now):
        """One frame of the fade: every sound and both windows at the same falling level."""
        started, shown = self.fading; gain = cm.fade_gain(now - started, self.fade_seconds())
        if gain <= 0: return self.stop_all()
        volume = self.hub.volume() * gain
        if self.music: self.music.tick(volume)
        for sound in [*self.voices.values(), *([self.dance_sound] if self.dance_sound else [])]:
            if sound.playing(): sound.set_volume(volume)
        client = self.watch.client
        if client is None or (self.enabled and not self.game_foreground()):
            self.timer_window.hide(); self.dance_window.hide(); return
        self.timer_window.present(client, shown, self.settings['size'] / 100, self.settings['opacity'] / 100 * gain)
        if self.dance is not None:
            index = int((now - self.dance_at) * self.dance.fps)
            image = self.frames.frame(index) if self.frames and index < len(self.dance.frames) else None
            if image is None: self.stop_dance()
            elif not image.isNull(): self.dance_window.present(client, image, dance_box(client.width, client.height, self.settings, image.width() / max(1, image.height())), gain)

    def stop_all(self):
        """Silence and hide everything at once."""
        self.fading = None; self.stop_dance()
        if self.music: self.music.stop()
        for sound in self.voices.values(): sound.stop()
        self.timer_window.hide(); self.dance_window.hide()
        if not self.enabled: self.timer.stop()

    def play(self, now):
        """One frame of a running chase: due voice lines and dances, the music level, the timer and the dancer."""
        client = self.watch.client
        for kind, item in self.show.tick(now):
            if kind == 'voice':
                log.info('Chase meme voice line: %s (weight %d)', item.name, item.weight)
                sound = self.voices.get(item.name)
                if sound: sound.set_volume(self.hub.volume()); sound.play()
            elif kind == 'dance': self.start_dance(item, now)
        if self.music: self.music.tick(self.hub.volume(cm.DUCK if self.show.ducked(now) else 1.))
        self.timer_window.present(client, now - self.chase_at, self.settings['size'] / 100, self.settings['opacity'] / 100)
        if self.dance is not None:
            index = int((now - self.dance_at) * self.dance.fps)
            if index >= len(self.dance.frames): self.stop_dance()
            else:
                image = self.frames.frame(index)
                if image is not None and not image.isNull(): self.dance_window.present(client, image, dance_box(client.width, client.height, self.settings, image.width() / max(1, image.height())))

    def start_dance(self, dance, now):
        log.info('Chase meme dance: %s (%.1f s)', dance.name, dance.duration)
        self.stop_dance(); self.dance = dance; self.dance_at = now; self.dance_sound = self.dance_sounds.get(dance.name); self.frames = self.loader(dance.frames)
        if self.dance_sound: self.dance_sound.set_volume(self.hub.volume()); self.dance_sound.play()

    def stop_dance(self):
        if self.dance_sound: self.dance_sound.stop()
        if self.frames: self.frames.close(); self.frames = None
        self.dance = None; self.dance_at = None; self.dance_sound = None; self.dance_window.hide()

    def apply_volume(self):
        volume = self.hub.volume()
        for sound in [*self.voices.values(), *self.dance_sounds.values()]: sound.set_volume(volume)
        if self.music and self.active: self.music.tick(self.hub.volume(cm.DUCK if self.show.ducked(self.clock()) else 1.))

    # --- preview ----------------------------------------------------------------------------
    def preview(self):
        """Plays the show for a few seconds without a chase: a voice line after 2 s, then a dance. For checking the look and the volume."""
        if self.preview_until is not None or self.active or self.fading is not None: return self.end('preview stopped', fade=False)
        now = self.clock()
        try: self.watch.poll()
        except ScreenError: self.watch.lost()
        if self.watch.client is None:   # No game: use the primary screen so the look can still be checked.
            from types import SimpleNamespace
            g = QGuiApplication.primaryScreen().geometry(); r = QGuiApplication.primaryScreen().devicePixelRatio()
            self.watch.client = SimpleNamespace(x=round(g.x() * r), y=round(g.y() * r), width=round(g.width() * r), height=round(g.height() * r))
        self.hub.reload(); self.begin(now, 'preview'); self.preview_until = now + PREVIEW_SECONDS
        self.show.next_line = now + 2.; self.show.dance_chance = 1.; self.set_interval(TICK_CHASE); self.timer.start()

    def state(self):
        """(key, values) for the tab's status line."""
        if self.active: return 'meme_chase_state_chase', dict(time=cm.format_timer(self.clock() - self.chase_at))
        return ('meme_chase_state_idle' if self.watch.survivor_match else 'meme_chase_state_waiting'), {}
