"""1v1 chase timer: the scoreboard window, its controller and the '1v1' tab (block D, v1.1.0).

Screen capture and GetAsyncKeyState only: Ctrl / M1 / the hotkeys are read, never blocked or sent.
The scoreboard sits at the top centre of the game, click-through, and only while DBD is in front.
"""
import ctypes
import logging
import threading
import time

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QLineEdit, QSlider, QVBoxLayout, QWidget

from src.core.killer_status import HUD_GONE_SECONDS, column_rect, read_column
from src.core.match_reader import fit_hud, load_layout, with_hud_fit
from src.core.onevone import ACTIONS, ARMED, DEFAULT_KEYS, RUNNING, OneVOne, clean_settings, default_settings, downed, format_time, load_down_templates  # noqa: F401
from src.core.own_perks import load_matchers, own_rect, read_own
from src.core.screen_manager import Rect, ScreenError, WindowsBackend
from src.gui.match_overlay import PerkOverlay, key_code
from src.gui.widgets import BloodButton, ToggleSwitch

log = logging.getLogger(__name__)
TICK_MS = 50          # Repaint of a running time; centiseconds need a smooth clock.
READ_EVERY = 6        # A HUD read about three times a second.
DOWN_READS = 2        # Two reads in a row must show a downed survivor before the clock stops.
VK_CTRL, VK_LBUTTON = 0x11, 0x01
BAR_W, BAR_H, SCORE_W = 620, 58, 124   # Reference design at 1920x1080.


class KeysWatcher:
    """Polls several keys while the game is in front; calls back with a name on each key-down."""
    def __init__(self, keys, is_game_foreground, callback, interval=.015, read=None):
        self.keys = dict(keys); self.is_game_foreground = is_game_foreground; self.callback = callback; self.interval = interval
        self.read = read or (lambda code: bool(ctypes.windll.user32.GetAsyncKeyState(code) & 0x8000))
        self.down = {name: False for name in self.keys}; self.stop = threading.Event(); self.seen = set()
        self.thread = threading.Thread(target=self.run, name='OneVOneKeys', daemon=True)

    def start(self): self.thread.start()

    def step(self):
        front = self.is_game_foreground()
        for name, vk in self.keys.items():
            down = front and self.read(vk)
            if down and not self.down[name]: self.callback(name)
            self.down[name] = down

    def run(self):
        while not self.stop.wait(self.interval):
            try: self.step()
            except Exception as exc:
                key = (type(exc).__name__, str(exc)[:120])
                if key not in self.seen: self.seen.add(key); log.exception('1v1 key watcher step failed; continuing')
                self.stop.wait(1.)

    def close(self):
        self.stop.set()
        if self.thread.is_alive(): self.thread.join(1)


def bar_rect(client_width, client_height, size=1.):
    """Logical-free (physical) rect of the scoreboard inside the client: top centre, scales with height."""
    s = client_height / 1080 * size; w, h = BAR_W * s, (BAR_H + 70) * s
    return QRectF((client_width - w) / 2, 8 * s, w, h)


class ScoreWindow(PerkOverlay):
    """Top-centre bar: name | score | name, and the two latest times below."""
    def __init__(self, root):
        super().__init__(root); self.view = None; self.scale = 1.; self.alpha = .9

    def present(self, client, view):
        screen = QGuiApplication.screenAt(QPointF(client.x, client.y).toPoint()) or QGuiApplication.primaryScreen()
        r = screen.devicePixelRatio() if screen else 1.
        rect = bar_rect(client.width, client.height, self.scale)
        geometry = (round((client.x + rect.x()) / r), round((client.y + rect.y()) / r), round(rect.width() / r) + 1, round(rect.height() / r) + 1)
        if self.geometry().getRect() != geometry: self.setGeometry(*geometry)
        self.view = view
        if not self.isVisible(): self.show()
        self.update()

    def paintEvent(self, event):
        if not self.view: return
        names, score, times = self.view
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing); p.setOpacity(self.alpha)
        s = self.width() / BAR_W; side = (BAR_W - SCORE_W) / 2 * s; bar = BAR_H * s
        p.fillRect(QRectF(0, 0, self.width(), bar), QColor(58, 58, 58, 235))
        p.fillRect(QRectF(side, 0, SCORE_W * s, bar), QColor(18, 52, 140, 245))
        p.setPen(QPen(QColor(110, 110, 110), max(1., s))); p.drawLine(QPointF(0, bar), QPointF(self.width(), bar))
        font = QFont('Segoe UI', 1, QFont.Black); font.setPixelSize(max(8, round(24 * s))); p.setFont(font); p.setPen(QColor(240, 240, 240))
        p.drawText(QRectF(0, 0, side, bar), Qt.AlignCenter, names[0])
        p.drawText(QRectF(side + SCORE_W * s, 0, side, bar), Qt.AlignCenter, names[1])
        p.drawText(QRectF(side, 0, SCORE_W * s, bar), Qt.AlignCenter, f'{score[0]} - {score[1]}')
        digits = QFont('Consolas', 1, QFont.Bold); digits.setPixelSize(max(8, round(38 * s))); p.setFont(digits)
        for index, (seconds, timed, losing) in enumerate(times):
            box = QRectF(index * (side + SCORE_W * s), bar + 6 * s, side, 50 * s)
            p.setPen(QColor(240, 80, 80) if losing else QColor(238, 238, 238)); p.drawText(box, Qt.AlignCenter, format_time(seconds))
            if timed: p.fillRect(QRectF(box.x(), box.bottom() + 2 * s, side, max(2., 3 * s)), QColor(40, 80, 190))


class OneVOneController(QObject):
    """Arms on a new match, starts on the role's input, stops when the HUD shows the survivor down."""
    key_event = Signal(str)
    changed = Signal()

    def __init__(self, root, settings, is_killer, automation_active, backend=None, window=None, clock=time.monotonic, key_reader=None):
        super().__init__()
        self.root = root; self.settings = settings; self.is_killer = is_killer; self.automation_active = automation_active
        self.backend = backend or WindowsBackend(); self.window = window or ScoreWindow(root); self.clock = clock; self.key_reader = key_reader
        self.model = OneVOne(list(settings['names'])); self.shown = True; self.watcher = None; self.game_hwnd = None
        self.layout = self.base_layout = load_layout(root); self.hud_scale = 1.; self.hud_fitted = False; self.fit_reads = 0
        self.templates = load_down_templates(root); self.matchers = None
        self.ticks = 0; self.hud_seen_at = None; self.in_match = False; self.role = None; self.down_reads = 0; self.down_at = None; self.client = None
        self.timer = QTimer(self); self.timer.setInterval(TICK_MS); self.timer.timeout.connect(self.tick)
        self.key_event.connect(self.on_key)

    # --- lifecycle --------------------------------------------------------------------------
    def enable(self):
        self.timer.start()
        if self.watcher is None:
            keys = {action: key_code(self.settings['keys'][action]) for action in ACTIONS}
            keys.update(ctrl=VK_CTRL, m1=VK_LBUTTON)
            self.watcher = KeysWatcher(keys, self.game_foreground, self.key_event.emit, read=self.key_reader); self.watcher.start()

    def disable(self):
        self.timer.stop(); self.window.hide()
        if self.watcher: self.watcher.close(); self.watcher = None

    def restart_keys(self):
        if self.watcher: self.disable(); self.enable()

    def game_foreground(self):
        from src.gui.match_overlay import game_in_front
        return game_in_front(self.game_hwnd)

    # --- input ------------------------------------------------------------------------------
    def current_role(self):
        return self.role or ('killer' if self.is_killer() else 'survivor')

    def on_key(self, name):
        m = self.model; now = self.clock()
        if name in ('ctrl', 'm1'):
            if m.state == ARMED and name == ('m1' if self.current_role() == 'killer' else 'ctrl'):
                m.start(now); self.down_reads = 0; log.info('1v1 chase started (%s, %s)', self.current_role(), name)
            else: return
        elif name == 'show': self.shown = not self.shown
        elif name == 'stop': self.finish(now, 'manual')
        elif name == 'reset_round': m.reset_round(); self.down_reads = 0
        elif name == 'undo': m.undo()
        elif name == 'swap': m.swap()
        elif name == 'reset_match': m.reset_match()
        self.changed.emit(); self.paint()

    def finish(self, now, reason):
        result = self.model.stop(now, reason)
        if result: log.info('1v1 chase stopped (%s): player %d, %s', reason, result.survivor + 1, format_time(result.seconds))

    def set_names(self, names):
        self.model.names = list(names); self.paint()

    # --- loop -------------------------------------------------------------------------------
    def tick(self):
        try: self.step()
        except ScreenError: self.game_hwnd = None; self.window.hide()
        except Exception:
            log.exception('1v1 step failed'); self.window.hide()

    def step(self):
        if self.automation_active(): return self.window.hide()
        self.ticks += 1
        if self.ticks % READ_EVERY == 0: self.read()
        self.paint()

    def read(self):
        window = self.backend.info(); self.game_hwnd = window.hwnd; self.client = window.client
        if not window.foreground: return
        client = window.client; now = self.clock()
        statuses, visible = self.grab_column(client)
        if not visible and not self.hud_fitted:
            self.fit_reads = (self.fit_reads + 1) % 3
            if self.fit_reads == 1 and self.try_fit(client): statuses, visible = self.grab_column(client)
        present = False
        if visible:
            present, role = self.read_role(client)
            if present and role: self.role = role
        if visible and present:
            if not self.in_match:   # Lobby/loading -> match: the next Ctrl / M1 starts the clock.
                self.in_match = True
                if self.model.state != RUNNING: self.model.arm(); self.down_reads = 0; log.info('1v1: new match, armed (%s)', self.current_role()); self.changed.emit()
            self.hud_seen_at = now
            if self.model.state == RUNNING:
                if downed(statuses):
                    if not self.down_reads: self.down_at = now   # The chase ended at the first read; the second only confirms it.
                    self.down_reads += 1
                else: self.down_reads = 0
                if self.down_reads >= DOWN_READS: self.finish(self.down_at, 'down'); self.changed.emit()
        elif self.hud_seen_at is not None and now - self.hud_seen_at > HUD_GONE_SECONDS:
            self.in_match = False; self.hud_seen_at = None; self.role = None
            self.layout = self.base_layout; self.hud_scale = 1.; self.hud_fitted = False

    def grab_column(self, client):
        x, y, w, h = column_rect(self.layout, client.width, client.height)
        image, _ = self.backend.grab(Rect(client.x + x, client.y + y, w, h))
        return read_column(image, self.layout, client.width, client.height, self.templates)

    def read_role(self, client):
        if self.in_match and self.role and self.ticks % (READ_EVERY * 10): return True, self.role   # The costly check runs rarely once known.
        if self.matchers is None: self.matchers = load_matchers(self.root)
        ox, oy, ow, oh = own_rect(client.width, client.height, self.hud_scale)
        own, _ = self.backend.grab(Rect(client.x + ox, client.y + oy, ow, oh))
        return read_own(own, client.width, client.height, self.matchers, self.hud_scale)

    def try_fit(self, client):
        """DBD's HUD scale, as the DS timer finds it; the own perks confirm it."""
        band, _ = self.backend.grab(Rect(client.x, client.y, round(client.width * .3), client.height))
        scale, shift, confident = fit_hud(band, self.base_layout, band=True)
        if not confident or (scale == 1. and abs(shift) < 6): return False
        self.layout = with_hud_fit(self.base_layout, scale, shift); self.hud_scale = scale
        if not self.read_role(client)[0]:
            self.layout = self.base_layout; self.hud_scale = 1.; return False
        self.hud_fitted = True; log.info('1v1 timer fitted to HUD scale %.2f shift %s', scale, shift); return True

    def view(self):
        return self.model.names, self.model.score, self.model.shown_times(self.clock())

    def paint(self):
        if not self.shown or self.client is None or not self.game_foreground() or self.automation_active():
            return self.window.hide()
        self.window.scale = self.settings['size'] / 100; self.window.alpha = self.settings['opacity'] / 100
        self.window.present(self.client, self.view())


class OneVOnePage(QWidget):
    """The '1v1' tab: switch, nicknames, sliders, the round buttons and the current state."""
    enabled_changed = Signal(bool)
    settings_changed = Signal()
    action = Signal(str)

    def __init__(self, settings, save, t):
        super().__init__(); self.settings, self.save, self.t = settings, save, t
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(8)
        warning = QLabel(t('cross_warning')); warning.setObjectName('notice'); warning.setWordWrap(True); layout.addWidget(warning)
        self.enabled = ToggleSwitch(t('vs_enable')); self.enabled.setChecked(settings['enabled']); self.enabled.toggled.connect(self.toggle_enabled); layout.addWidget(self.enabled)
        about = QLabel(t('vs_about')); about.setObjectName('muted'); about.setWordWrap(True); layout.addWidget(about)
        grid = QGridLayout(); grid.setVerticalSpacing(6); self.names = []
        for index in (0, 1):
            grid.addWidget(QLabel(t('vs_player', number=index + 1)), index, 0)
            field = QLineEdit(settings['names'][index]); field.setMaxLength(24); field.editingFinished.connect(self.names_changed); grid.addWidget(field, index, 1, 1, 2); self.names.append(field)
        self.sliders = {}
        for row, (name, low, high) in enumerate((('opacity', 30, 100), ('size', 50, 200)), start=2):
            grid.addWidget(QLabel(t('vs_' + name)), row, 0); slider = QSlider(Qt.Horizontal); slider.setRange(low, high); slider.setSingleStep(5); slider.setValue(settings[name])
            value = QLabel(f'{settings[name]} %'); value.setFixedWidth(48); slider.valueChanged.connect(lambda v, n=name, l=value: self.slider_changed(n, v, l))
            grid.addWidget(slider, row, 1); grid.addWidget(value, row, 2); self.sliders[name] = slider
        layout.addLayout(grid)
        row = QHBoxLayout(); self.buttons = {}
        for name in ('stop', 'reset_round', 'undo', 'swap', 'reset_match'):
            button = BloodButton(); button.clicked.connect(lambda checked=False, n=name: self.action.emit(n)); row.addWidget(button); self.buttons[name] = button
        layout.addLayout(row)
        self.keys = QLabel(); self.keys.setObjectName('muted'); self.keys.setWordWrap(True); layout.addWidget(self.keys)
        self.state = QLabel(); self.state.setWordWrap(True); layout.addWidget(self.state)
        layout.addStretch(); self.publish()

    def publish(self):
        t = self.t; keys = self.settings['keys']
        for name, button in self.buttons.items(): button.setText(f"{t('vs_' + name)} ({keys[name]})")
        self.keys.setText(t('vs_keys_hint', show=keys['show']))

    def show_state(self, model, role):
        t = self.t; times = model.shown_times(time.monotonic())
        text = t('vs_state_' + model.state, name=model.names[model.survivor], role=t('vs_role_' + role))
        last = ' · '.join(f'{model.names[i]} {format_time(times[i][0])}' for i in (0, 1))
        self.state.setText(f'{text}\n{model.score[0]} - {model.score[1]} · {last}')

    def names_changed(self):
        self.settings['names'] = [f.text().strip()[:24] or f'Player {i + 1}' for i, f in enumerate(self.names)]; self.save(); self.settings_changed.emit()

    def slider_changed(self, name, value, label):
        self.settings[name] = value; label.setText(f'{value} %'); self.save(); self.settings_changed.emit()

    def toggle_enabled(self, value):
        self.settings['enabled'] = bool(value); self.save(); self.enabled_changed.emit(bool(value))
