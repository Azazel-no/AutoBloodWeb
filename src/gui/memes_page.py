"""The 'Memes' tab: shared volume / mute controls and three sections (Randomizer, Chase meme, Sable Detector).

MemeHub owns what the sections share: the settings block, the meme pack on disk and the global
mute hotkey. The hotkey is only read (GetAsyncKeyState), never blocked or sent, and it is polled
only while a meme feature is switched on.
"""
import ctypes
import logging
import threading

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QSlider, QStackedWidget, QVBoxLayout, QWidget

from src.core import meme_pack
from src.core.memes import CHASE_RANGES, SABLE_RANGES, SECTIONS, combo_down, effective_volume
from src.gui.widgets import BloodButton, ToggleSwitch

log = logging.getLogger(__name__)


class ComboWatcher:
    """Polls one key combination in any window; calls back once per press."""
    def __init__(self, combo, callback, interval=.03, read=None):
        self.combo = combo; self.callback = callback; self.interval = interval
        self.read = read or (lambda code: bool(ctypes.windll.user32.GetAsyncKeyState(code) & 0x8000))
        self.down = False; self.stop = threading.Event(); self.logged = False
        self.thread = threading.Thread(target=self.run, name='MemeMuteKey', daemon=True)

    def start(self): self.thread.start()

    def step(self):
        down = combo_down(self.combo, self.read)
        if down and not self.down: self.callback()
        self.down = down

    def run(self):
        while not self.stop.wait(self.interval):
            try: self.step()
            except Exception:
                if not self.logged: self.logged = True; log.exception('Meme mute key watcher failed; continuing')
                self.stop.wait(1.)

    def close(self):
        self.stop.set()
        if self.thread.is_alive(): self.thread.join(1)


class MemeHub(QObject):
    """Shared state of the meme features: settings, pack, volume and the mute hotkey."""
    volume_changed = Signal()      # slider, mute switch or hotkey
    pack_changed = Signal()
    mute_pressed = Signal()        # from the watcher thread; queued to the GUI thread

    def __init__(self, settings, save, folder=None, key_reader=None):
        super().__init__()
        self.settings = settings; self.save = save; self.folder = meme_pack.pack_dir(folder); self.key_reader = key_reader
        self.pack = meme_pack.Pack(self.folder); self.watcher = None; self.users = set()
        self.mute_pressed.connect(self.toggle_mute)

    def reload(self):
        """Makes sure the folder and its README exist, then re-reads the pack."""
        try: meme_pack.ensure(self.folder)
        except OSError: log.exception('Meme pack folder could not be prepared: %s', self.folder)
        self.pack = meme_pack.load(self.folder); self.pack_changed.emit()
        return self.pack

    def open_folder(self):
        self.reload(); QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder)))

    def volume(self, duck=1.):
        return effective_volume(self.settings, duck)

    def set_volume(self, value):
        self.settings['volume'] = int(value); self.save(); self.volume_changed.emit()

    def set_muted(self, muted):
        if self.settings['muted'] != bool(muted):
            self.settings['muted'] = bool(muted); self.save(); self.volume_changed.emit()

    def toggle_mute(self):
        self.set_muted(not self.settings['muted']); log.info('Memes %s by hotkey', 'muted' if self.settings['muted'] else 'unmuted')

    def use(self, name, active):
        """A feature switches on or off; the mute key is watched only while one is on."""
        (self.users.add if active else self.users.discard)(name)
        if self.users and self.watcher is None:
            self.watcher = ComboWatcher(self.settings['mute_key'], self.mute_pressed.emit, read=self.key_reader); self.watcher.start()
        elif not self.users and self.watcher:
            self.watcher.close(); self.watcher = None

    def restart_key(self):
        if self.watcher:
            self.watcher.close(); self.watcher = ComboWatcher(self.settings['mute_key'], self.mute_pressed.emit, read=self.key_reader); self.watcher.start()

    def close(self):
        self.users.clear()
        if self.watcher: self.watcher.close(); self.watcher = None


def unit(key, value):
    """Slider text: the fade is kept in tenths of a second, everything else in percent."""
    return f'{value / 10:.1f} s' if key == 'fade' else f'{value} %'


class FeatureSection(QWidget):
    """One overlay's settings: the ban-risk warning, the switch, what it does, sliders, the pack state."""
    enabled_changed = Signal(bool)
    settings_changed = Signal()

    preview = Signal()

    def __init__(self, name, settings, ranges, hub, t, ready, previewable=False):
        super().__init__(); self.name, self.settings, self.hub, self.t, self.ready = name, settings, hub, t, ready
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(8)
        warning = QLabel(t('cross_warning')); warning.setObjectName('notice'); warning.setWordWrap(True); layout.addWidget(warning)
        self.enabled = ToggleSwitch(t(f'meme_{name}_enable')); self.enabled.setChecked(settings['enabled']); self.enabled.toggled.connect(self.toggle_enabled); layout.addWidget(self.enabled)
        about = QLabel(t(f'meme_{name}_about')); about.setObjectName('muted'); about.setWordWrap(True); layout.addWidget(about)
        grid = QGridLayout(); grid.setVerticalSpacing(6); self.sliders = {}
        for row, (key, low, high, _) in enumerate(ranges):
            grid.addWidget(QLabel(t(f'meme_{name}_{key}')), row, 0); slider = QSlider(Qt.Horizontal); slider.setRange(low, high); slider.setSingleStep(1); slider.setValue(settings[key])
            value = QLabel(unit(key, settings[key])); value.setFixedWidth(48); slider.valueChanged.connect(lambda v, k=key, l=value: self.slider_changed(k, v, l))
            grid.addWidget(slider, row, 1); grid.addWidget(value, row, 2); self.sliders[key] = slider
        layout.addLayout(grid)
        if previewable:
            self.preview_button = BloodButton(t('meme_preview')); self.preview_button.setToolTip(t('meme_preview_hint')); self.preview_button.clicked.connect(self.preview.emit); layout.addWidget(self.preview_button)
        self.pack_note = QLabel(); self.pack_note.setObjectName('notice'); self.pack_note.setWordWrap(True); layout.addWidget(self.pack_note)
        self.state = QLabel(); self.state.setWordWrap(True); layout.addWidget(self.state)
        layout.addStretch(); hub.pack_changed.connect(self.show_pack); self.show_pack()

    def show_pack(self):
        """An empty pack is a message on the tab, never an error."""
        self.pack_note.setVisible(not self.ready(self.hub.pack)); self.pack_note.setText(self.t('meme_pack_empty'))

    def slider_changed(self, key, value, label):
        self.settings[key] = value; label.setText(unit(key, value)); self.hub.save(); self.settings_changed.emit()

    def toggle_enabled(self, value):
        self.settings['enabled'] = bool(value); self.hub.save(); self.enabled_changed.emit(bool(value))


class MemesPage(QWidget):
    """Shared controls on top; the section buttons switch what is below them."""
    def __init__(self, hub, t, randomizer):
        super().__init__(); self.hub, self.t = hub, t; settings = hub.settings
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(8)
        card = QFrame(); card.setObjectName('card'); box = QVBoxLayout(card); box.setContentsMargins(10, 9, 10, 9); box.setSpacing(7)
        row = QHBoxLayout(); row.addWidget(QLabel(t('meme_volume')))
        self.volume = QSlider(Qt.Horizontal); self.volume.setRange(0, 100); self.volume.setSingleStep(5); self.volume.setValue(settings['volume']); self.volume.setToolTip(t('meme_volume_hint'))
        self.volume_value = QLabel(f"{settings['volume']} %"); self.volume_value.setFixedWidth(48); self.volume.valueChanged.connect(self.volume_moved)
        row.addWidget(self.volume, 1); row.addWidget(self.volume_value); box.addLayout(row)
        row = QHBoxLayout(); self.mute = ToggleSwitch(); self.mute.setChecked(settings['muted']); self.mute.toggled.connect(hub.set_muted); row.addWidget(self.mute); row.addStretch()
        self.folder_button = BloodButton(t('meme_open_folder')); self.folder_button.clicked.connect(hub.open_folder); row.addWidget(self.folder_button); box.addLayout(row)
        self.pack_status = QLabel(); self.pack_status.setObjectName('muted'); self.pack_status.setWordWrap(True); box.addWidget(self.pack_status)
        layout.addWidget(card)
        row = QHBoxLayout(); row.setSpacing(6); self.tabs = QButtonGroup(self); self.tabs.setExclusive(True); self.tab_buttons = {}
        for index, name in enumerate(SECTIONS):
            button = BloodButton(t('meme_section_' + name)); button.setCheckable(True); self.tabs.addButton(button, index); row.addWidget(button, 1); self.tab_buttons[name] = button
        layout.addLayout(row)
        self.stack = QStackedWidget(); layout.addWidget(self.stack, 1)
        self.randomizer = randomizer; self.stack.addWidget(randomizer)
        self.chase = FeatureSection('chase', settings['chase'], CHASE_RANGES, hub, t, lambda pack: pack.chase_ready, previewable=True); self.stack.addWidget(self.chase)
        self.sable = FeatureSection('sable', settings['sable'], SABLE_RANGES, hub, t, lambda pack: pack.sable_ready, previewable=True); self.stack.addWidget(self.sable)
        self.tabs.idClicked.connect(self.open_section)
        hub.volume_changed.connect(self.show_volume); hub.pack_changed.connect(self.show_pack)
        self.publish(); self.show_pack(); self.open_section(SECTIONS.index(settings['section']), save=False)

    def open_section(self, index, save=True):
        name = SECTIONS[index]; self.stack.setCurrentIndex(index)
        for key, button in self.tab_buttons.items(): button.primary = key == name; button.setChecked(key == name); button.update()
        if save and self.hub.settings['section'] != name: self.hub.settings['section'] = name; self.hub.save()

    def publish(self):
        self.mute.setText(self.t('meme_mute', hotkey=self.hub.settings['mute_key']))

    def volume_moved(self, value):
        self.volume_value.setText(f'{value} %'); self.hub.set_volume(value)

    def show_volume(self):
        if self.mute.isChecked() != self.hub.settings['muted']:
            self.mute.blockSignals(True); self.mute.setChecked(self.hub.settings['muted']); self.mute.blockSignals(False); self.mute.update()

    def show_pack(self):
        pack = self.hub.pack
        self.pack_status.setText(self.t('meme_pack_empty') if pack.empty else self.t('meme_pack_status', **pack.counts()))
