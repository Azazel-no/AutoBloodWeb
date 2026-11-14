"""Crosshair window and the tab that configures it. Screen capture only; click-through, never takes focus."""
import logging

from PySide6.QtCore import QObject, QPointF, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import QColorDialog, QComboBox, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from src.core.crosshair import DEFAULT_KEY, FORBIDDEN_KEYS, PALETTE, PRESETS, centre, draw, extent, preset_config
from src.core.match_reader import hud_visible, is_end_scoreboard, is_lobby, is_scoreboard, load_layout
from src.core.own_perks import crop_own, load_matchers, read_own
from src.core.screen_manager import ScreenError, WindowsBackend
from src.gui.match_overlay import KeyWatcher, PerkOverlay, foreground_handle, game_in_front, key_code, overlay_in_front
from src.gui.widgets import BloodButton, ToggleSwitch

log = logging.getLogger(__name__)
LOOK_MS = 500
SLIDERS = (('length', 1, 40), ('thickness', 1, 8), ('gap', 0, 24), ('dot', 0, 10), ('circle', 0, 30), ('outline_width', 1, 4), ('opacity', 10, 100))


def in_match(image, layout, root):
    """The match HUD is on screen: not a lobby, menu, loading screen, scoreboard or end-game table."""
    if is_lobby(image, layout) or is_scoreboard(image, layout) or is_end_scoreboard(image) or not hud_visible(image, layout):
        return False
    return read_own(*crop_own(image), load_matchers(root))[0]  # Menus can look like the nickname column; own perks can't.


class CrosshairWindow(PerkOverlay):
    """A small transparent square around the game's centre; repaints only when asked."""
    def __init__(self, root):
        super().__init__(root);self.config = preset_config('classic_gap');self.placed = None

    def place(self, client):
        screen = QGuiApplication.screenAt(QPointF(client.x, client.y).toPoint()) or QGuiApplication.primaryScreen()
        ratio = screen.devicePixelRatio() if screen else 1.;x, y = centre(client, ratio);half = round(extent(self.config))
        geometry = QRect(round(x) - half, round(y) - half, half * 2, half * 2)
        if geometry != self.placed:
            self.placed = geometry;self.setGeometry(geometry);self.update()
        if not self.isVisible():
            self.show()

    def set_config(self, config):
        self.config = config;self.placed = None;self.update()

    def paintEvent(self, event):
        draw(QPainter(self), self.width() / 2, self.height() / 2, self.config)


class CrosshairController(QObject):
    """Shows the crosshair only while the switch is on, the hotkey has not hidden it, and DBD runs a match."""
    key_event = Signal(str)

    def __init__(self, root, automation_active, key=DEFAULT_KEY, backend=None, window=None, key_reader=None):
        super().__init__();self.automation_active = automation_active;self.backend = backend or WindowsBackend();self.window = window or CrosshairWindow(root)
        self.root = root;self.layout = load_layout(root);self.key = key;self.key_reader = key_reader;self.shown = True;self.game_hwnd = None;self.watcher = None
        self.timer = QTimer(self);self.timer.setInterval(LOOK_MS);self.timer.timeout.connect(self.look);self.key_event.connect(self.toggle)

    def game_foreground(self):
        return game_in_front(self.game_hwnd)

    def enable(self):
        self.shown = True;self.timer.start()
        if self.watcher is None:
            self.watcher = KeyWatcher(key_code(self.key), self.game_foreground, self.key_event.emit, read=self.key_reader);self.watcher.start()

    def disable(self):
        self.timer.stop();self.window.hide()
        if self.watcher:
            self.watcher.close();self.watcher = None

    def set_key(self, key):
        self.key = key
        if self.watcher:
            self.disable();self.enable()

    def toggle(self, action):
        if action == 'key_down':
            self.shown = not self.shown;self.look()

    def look(self):
        try:
            self.step()
        except ScreenError:
            self.game_hwnd = None;self.window.hide()
        except Exception:
            log.exception('Crosshair step failed');self.window.hide()

    def step(self):
        if self.automation_active():  # Any window over the game counts as a cover for purchase checks.
            return self.window.hide()
        window = self.backend.info();self.game_hwnd = window.hwnd
        if not self.shown or not (window.foreground or overlay_in_front()):
            return self.window.hide()
        image, _ = self.backend.grab(window.client)
        if in_match(image, self.layout, self.root):
            self.window.place(window.client)
        else:
            self.window.hide()


class Preview(QWidget):
    def __init__(self):
        super().__init__();self.config = preset_config('classic_gap');self.setFixedHeight(130)

    def paintEvent(self, event):
        p = QPainter(self);p.fillRect(self.rect(), QColor(70, 78, 66));draw(p, self.width() / 2, self.height() / 2, self.config)


class CrosshairPage(QWidget):
    changed = Signal(dict)
    enabled_changed = Signal(bool)

    def __init__(self, settings, save, t):
        super().__init__();self.settings, self.save, self.t = settings, save, t;self.loading = False
        layout = QVBoxLayout(self);layout.setContentsMargins(0, 0, 0, 0);layout.setSpacing(8)
        warning = QLabel(t('cross_warning'));warning.setObjectName('notice');warning.setWordWrap(True);layout.addWidget(warning)
        row = QHBoxLayout();self.enabled = ToggleSwitch(t('cross_enable'));self.enabled.setChecked(settings['enabled'] is True);self.enabled.toggled.connect(self.toggle_enabled);row.addWidget(self.enabled);row.addStretch()
        self.key_label = QLabel();self.key_label.setObjectName('muted');row.addWidget(self.key_label);layout.addLayout(row)
        self.preview = Preview();layout.addWidget(self.preview)
        row = QHBoxLayout();self.preset = QComboBox()
        for name in PRESETS:
            self.preset.addItem(t('cross_' + name), name)
        self.preset.setCurrentIndex(max(0, self.preset.findData(settings['preset'])));self.preset.currentIndexChanged.connect(self.load_preset);row.addWidget(self.preset, 1)
        reset = BloodButton(t('cross_reset'));reset.clicked.connect(self.reset_preset);row.addWidget(reset);layout.addLayout(row)
        grid = QGridLayout();grid.setVerticalSpacing(4);self.sliders = {}
        for index, (key, low, high) in enumerate(SLIDERS):
            grid.addWidget(QLabel(t('cross_' + key)), index, 0);slider = QSlider(Qt.Horizontal);slider.setRange(low, high);slider.valueChanged.connect(self.values_changed);grid.addWidget(slider, index, 1)
            value = QLabel();value.setFixedWidth(28);grid.addWidget(value, index, 2);self.sliders[key] = (slider, value)
        layout.addLayout(grid)
        self.outline = ToggleSwitch(t('cross_outline_toggle'));self.outline.toggled.connect(self.values_changed);layout.addWidget(self.outline)
        row = QHBoxLayout();row.setSpacing(6);self.colour = PALETTE[0]
        for colour in PALETTE:
            swatch = QPushButton();swatch.setFixedSize(26, 26);swatch.setCursor(Qt.PointingHandCursor);swatch.setStyleSheet(f'background:{colour};border:1px solid #58282e;border-radius:6px;')
            swatch.clicked.connect(lambda checked=False, c=colour: self.set_colour(c));row.addWidget(swatch)
        row.addStretch();picker = BloodButton(t('cross_pick'));picker.clicked.connect(self.pick_colour);row.addWidget(picker);layout.addLayout(row)
        layout.addStretch();self.load_preset()

    @property
    def name(self):
        return self.preset.currentData()

    def config(self):
        return preset_config(self.name, self.settings['overrides'].get(self.name))

    def load_preset(self, *args):
        self.loading = True;config = self.config()
        for key, (slider, _) in self.sliders.items():
            slider.setValue(config[key])
        self.outline.setChecked(config['outline']);self.colour = config['color'];self.loading = False;self.settings['preset'] = self.name;self.publish()

    def values_changed(self, *args):
        if self.loading:
            return
        values = {k: s.value() for k, (s, _) in self.sliders.items()};values.update(outline=self.outline.isChecked(), color=self.colour)
        self.settings['overrides'][self.name] = values;self.publish()

    def publish(self):
        config = self.config()
        for key, (slider, value) in self.sliders.items():
            value.setText(str(slider.value()))
        self.preview.config = config;self.preview.update();self.key_label.setText(self.t('cross_key_hint', hotkey=self.settings['key']));self.save();self.changed.emit(config)

    def set_colour(self, colour):
        self.colour = colour;self.values_changed()

    def pick_colour(self):
        chosen = QColorDialog.getColor(QColor(self.colour), self, self.t('cross_pick'))
        if chosen.isValid():
            self.set_colour(chosen.name())

    def reset_preset(self):
        self.settings['overrides'].pop(self.name, None);self.load_preset()

    def toggle_enabled(self, value):
        self.settings['enabled'] = bool(value);self.save();self.enabled_changed.emit(bool(value))


def valid_key(text):
    key = text.strip().upper()
    try:
        key_code(key)
    except (ValueError, KeyError):
        return None
    return None if key in FORBIDDEN_KEYS else key
