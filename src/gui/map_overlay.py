"""Map overlay (block E): the current map's callout image in the top-left corner during a match.

The map is read from the loading screen (bottom-left map name, Windows OCR) or picked by hand.
Click-through, topmost, only in a match and only while DBD is in front. Screen capture only.
Images: Hens333, downloaded on first use (src/core/map_images.py).
"""
import logging
import time

import cv2
import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtWidgets import QComboBox, QGridLayout, QLabel, QSlider, QVBoxLayout, QWidget

from src.core.map_images import CREDIT, Fetcher, cached
from src.core.map_names import DEFAULT_KEY, MapMatcher, clean_settings, default_settings, load_maps, ocr_lines  # noqa: F401
from src.core.match_reader import is_lobby, load_layout
from src.core.screen_manager import Rect, ScreenError, WindowsBackend
from src.gui.match_overlay import KeyWatcher, PerkOverlay, foreground_handle, key_code
from src.gui.widgets import ToggleSwitch

log = logging.getLogger(__name__)
TICK_MS = 500
OCR_EVERY = 2.            # seconds between loading-screen reads
MATCH_GONE_SECONDS = 8.   # the HUD must be gone this long before the match counts as over
HIDE_SECONDS = 1.         # Tab table, menus, end screen: hidden once the HUD is gone this long (no flicker on one missed read)
NAME_ROI = (0., .62, .55, .34)   # x, y, w, h as fractions of the client: the loading screen's bottom-left text
BOX = .33                 # default image box: a third of the client height, above the HUD portraits (top one at ~38 %)
def map_box(client_width, client_height, size=1.):
    """(x, y, side) inside the client: the top-left corner, scaled with the window height."""
    side = round(client_height * BOX * size)
    return 0, 0, min(side, client_width)


def name_region(image):
    h, w = image.shape[:2]; x, y, rw, rh = NAME_ROI
    return image[round(h * y):round(h * (y + rh)), round(w * x):round(w * (x + rw))]


def prepare(region):
    """Light text on a dark loading screen: grey, enlarged, inverted for Windows OCR."""
    grey = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    scale = max(1., 900 / max(1, grey.shape[0]))
    grey = cv2.resize(grey, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return 255 - grey


def to_qimage(path):
    data = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_UNCHANGED)
    if data is None: return QImage()
    if data.ndim == 2: data = cv2.cvtColor(data, cv2.COLOR_GRAY2BGRA)
    elif data.shape[2] == 3: data = cv2.cvtColor(data, cv2.COLOR_BGR2BGRA)
    data = np.ascontiguousarray(cv2.cvtColor(data, cv2.COLOR_BGRA2RGBA))
    return QImage(data.data, data.shape[1], data.shape[0], data.strides[0], QImage.Format_RGBA8888).copy()


class MapWindow(PerkOverlay):
    def __init__(self, root):
        super().__init__(root); self.image = QImage(); self.alpha = .85; self.scale = 1.; self.key = None

    def present(self, client, image, key):
        screen = QGuiApplication.screenAt(QPointF(client.x, client.y).toPoint()) or QGuiApplication.primaryScreen()
        r = screen.devicePixelRatio() if screen else 1.
        x, y, side = map_box(client.width, client.height, self.scale)
        geometry = (round((client.x + x) / r), round((client.y + y) / r), round(side / r), round(side / r))
        if self.geometry().getRect() != geometry: self.setGeometry(*geometry)
        self.key = key; self.image = image
        if not self.isVisible(): self.show()
        self.update()

    def paintEvent(self, event):
        if self.image.isNull(): return
        p = QPainter(self); p.setRenderHint(QPainter.SmoothPixmapTransform); p.setOpacity(self.alpha)
        size = self.image.size().scaled(self.size(), Qt.KeepAspectRatio)
        p.drawImage(QRectF(0, 0, size.width(), size.height()), self.image)


class MapController(QObject):
    """Reads the map on the loading screen, shows it during the match, forgets it when the match ends."""
    key_event = Signal(str)
    image_ready = Signal()
    changed = Signal()

    def __init__(self, root, settings, automation_active, backend=None, window=None, ocr=None, clock=time.monotonic, in_match=None, key_reader=None):
        super().__init__()
        self.root = root; self.settings = settings; self.automation_active = automation_active
        self.backend = backend or WindowsBackend(); self.window = window or MapWindow(root); self.clock = clock
        maps, base, _ = load_maps(root); self.matcher = MapMatcher(maps)
        self.fetcher = Fetcher(root, base, lambda m, path: self.image_ready.emit())
        self.layout = load_layout(root); self.ocr = ocr; self.in_match_check = in_match; self.key_reader = key_reader
        self.detected = None; self.manual = None; self.matched = False; self.match_seen_at = None
        self.last_ocr = -1e9; self.shown = True; self.watcher = None; self.game_hwnd = None; self.client = None; self.uncertain = set()
        self.images = {}
        self.timer = QTimer(self); self.timer.setInterval(TICK_MS); self.timer.timeout.connect(self.tick)
        self.key_event.connect(self.on_key); self.image_ready.connect(self.tick)

    def enable(self):
        if self.ocr is None:
            from src.core.nicknames import NicknameOcr
            self.ocr = NicknameOcr(self.root); self.ocr.start()
        self.timer.start()
        if self.watcher is None:
            self.watcher = KeyWatcher(key_code(self.settings['key']), self.game_foreground, self.key_event.emit, read=self.key_reader); self.watcher.start()

    def disable(self):
        self.timer.stop(); self.window.hide()
        if self.watcher: self.watcher.close(); self.watcher = None

    def restart_key(self):
        if self.watcher: self.disable(); self.enable()

    def game_foreground(self):
        return bool(self.game_hwnd) and foreground_handle() == self.game_hwnd

    def on_key(self, action):
        if action == 'key_down': self.shown = not self.shown; self.tick()

    @property
    def current(self):
        return self.manual or self.detected

    def pick(self, key):
        """Manual choice from the tab: for this (or the next) match only; None returns to detection."""
        self.manual = self.matcher.by_key(key) if key else None; self.changed.emit(); self.tick()

    def tick(self):
        try: self.step()
        except ScreenError: self.game_hwnd = None; self.window.hide()
        except Exception:
            log.exception('Map overlay step failed'); self.window.hide()

    def match_now(self, image):
        if self.in_match_check: return self.in_match_check(image)
        from src.gui.crosshair_overlay import in_match
        return in_match(image, self.layout, self.root)

    def step(self):
        if self.automation_active(): return self.window.hide()
        window = self.backend.info(); self.game_hwnd = window.hwnd; self.client = window.client
        if not window.foreground: return self.window.hide()
        image, _ = self.backend.grab(window.client); now = self.clock()
        if self.match_now(image):
            self.matched = True; self.match_seen_at = now
        else:
            if self.matched and now - self.match_seen_at > MATCH_GONE_SECONDS: self.end_match()
            if not self.matched and now - self.last_ocr >= OCR_EVERY and not is_lobby(image, self.layout):
                self.last_ocr = now; self.read_name(image)
        self.show_map(now)

    def end_match(self):
        log.info('Map overlay: match over, %s cleared', self.current.key if self.current else 'nothing')
        self.matched = False; self.detected = None; self.manual = None; self.changed.emit()

    def read_name(self, image):
        results = self.ocr.recognize([prepare(name_region(image))])[0] if self.ocr else []
        lines = [line for result in results for line in ocr_lines(result)]
        if not lines: return
        hit = self.matcher.match_lines(lines)
        if hit:
            if self.detected is None or hit.map.key != self.detected.key:
                log.info('Map overlay: %s (%s) read as "%s", distance %d', hit.map.key, hit.map.realm, hit.text, hit.distance)
                self.detected = hit.map; self.changed.emit()
        else:
            text = ' | '.join(lines)[:120]
            if text not in self.uncertain and len(self.uncertain) < 200:
                self.uncertain.add(text); log.info('Map overlay: no confident map in "%s"', text)

    def show_map(self, now=None):
        m = self.current; now = self.clock() if now is None else now
        live = self.matched and self.match_seen_at is not None and now - self.match_seen_at <= HIDE_SECONDS
        if not (self.shown and live and m and self.game_foreground()): return self.window.hide()
        image = self.images.get(m.key)
        if image is None:
            path = cached(self.root, m) or self.fetcher.request(m)
            if path is None: return self.window.hide()   # Downloading; image_ready repaints.
            image = self.images[m.key] = to_qimage(path)
        self.window.scale = self.settings['size'] / 100; self.window.alpha = self.settings['opacity'] / 100
        self.window.present(self.client, image, m.key)


class MapPage(QWidget):
    enabled_changed = Signal(bool)
    settings_changed = Signal()
    picked = Signal(object)

    def __init__(self, root, settings, save, t):
        super().__init__(); self.settings, self.save, self.t = settings, save, t
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(8)
        warning = QLabel(t('cross_warning')); warning.setObjectName('notice'); warning.setWordWrap(True); layout.addWidget(warning)
        self.enabled = ToggleSwitch(t('map_enable')); self.enabled.setChecked(settings['enabled']); self.enabled.toggled.connect(self.toggle_enabled); layout.addWidget(self.enabled)
        about = QLabel(t('map_about')); about.setObjectName('muted'); about.setWordWrap(True); layout.addWidget(about)
        grid = QGridLayout(); grid.setVerticalSpacing(6)
        grid.addWidget(QLabel(t('map_pick')), 0, 0); self.choice = QComboBox(); self.choice.addItem(t('map_auto'), None)
        maps, _, _ = load_maps(root)
        for m in sorted(maps, key=lambda m: (m.realm, m.key)): self.choice.addItem(f'{m.realm} — {m.key}', m.key)
        self.choice.activated.connect(lambda index: self.picked.emit(self.choice.itemData(index))); grid.addWidget(self.choice, 0, 1, 1, 2)
        for row, (name, low, high) in enumerate((('size', 50, 150), ('opacity', 30, 100)), start=1):
            grid.addWidget(QLabel(t('vs_' + name)), row, 0); slider = QSlider(Qt.Horizontal); slider.setRange(low, high); slider.setSingleStep(5); slider.setValue(settings[name])
            value = QLabel(f'{settings[name]} %'); value.setFixedWidth(48); slider.valueChanged.connect(lambda v, n=name, l=value: self.slider_changed(n, v, l))
            grid.addWidget(slider, row, 1); grid.addWidget(value, row, 2)
        layout.addLayout(grid)
        self.key_label = QLabel(); self.key_label.setObjectName('muted'); layout.addWidget(self.key_label)
        self.state = QLabel(); self.state.setWordWrap(True); layout.addWidget(self.state)
        credit = QLabel(f'<b>{CREDIT}</b> — {t("map_credit")}'); credit.setWordWrap(True); credit.setOpenExternalLinks(True); layout.addWidget(credit)
        layout.addStretch(); self.publish()

    def publish(self):
        self.key_label.setText(self.t('cross_key_hint', hotkey=self.settings['key']))

    def show_state(self, controller):
        if controller is None: return self.state.setText(self.t('map_off'))
        m = controller.current
        if m is None: text = self.t('map_state_none')
        else: text = self.t('map_state_manual' if controller.manual else 'map_state_detected', name=f'{m.realm} — {m.key}')
        self.state.setText(text)
        if controller.manual is None and self.choice.currentIndex() != 0: self.choice.setCurrentIndex(0)

    def slider_changed(self, name, value, label):
        self.settings[name] = value; label.setText(f'{value} %'); self.save(); self.settings_changed.emit()

    def toggle_enabled(self, value):
        self.settings['enabled'] = bool(value); self.save(); self.enabled_changed.emit(bool(value))
