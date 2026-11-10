"""Randomizer tab: reels that stop left to right, then result cards that settle in the centre."""
from dataclasses import replace
import random

from PySide6.QtCore import QEasingCurve, QParallelAnimationGroup, QPropertyAnimation, QRect, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from src.core.randomizer import ADDON_SLOTS, PERK_SLOTS, Options
from src.gui.info_card import RARITY
from src.gui.widgets import BloodButton, ResetButton, ToggleSwitch

MAX_MS = 2400
FIRST_STOP_MS = 800
REVEAL_MS = 360
REEL = 72
ORDER = ('survivor', 'killer', 'item', 'addon1', 'addon2', 'perk1', 'perk2', 'perk3', 'perk4', 'offering')


def stop_times(count):
    """When each reel stops, left to right, the last one no later than MAX_MS."""
    if count <= 1:
        return [FIRST_STOP_MS] * count
    step = (MAX_MS - FIRST_STOP_MS) / (count - 1)
    return [round(FIRST_STOP_MS + i * step) for i in range(count)]


def card_rects(slots, width, height):
    """{slot: QRect}: the character or item and its add-ons on one row, the rest on a second; centred."""
    first = [s for s in slots if s in ('survivor', 'killer', 'item') or s in ADDON_SLOTS]
    second = [s for s in slots if s not in first]
    big, small, gap = min(160, width // 3), min(112, (width - 60) // 4), 12
    if len(first) + len(second) > 7:
        big, small = min(big, 132), min(small, 104)
    for row in (first, second):
        if row:
            small = min(small, (width - gap * (len(row) - 1) - (big + gap) * sum(s in ('survivor', 'killer', 'item') for s in row)) // sum(s not in ('survivor', 'killer', 'item') for s in row))
    heights = [big + 46] + ([small + 46] if second else [])
    y = max(0, (height - sum(heights) - gap * (len(heights) - 1)) // 2)
    rects = {}
    for row, row_height in zip((first, second), heights):
        sizes = [big if s in ('survivor', 'killer', 'item') else small for s in row]
        x = (width - sum(sizes) - gap * (len(row) - 1)) // 2
        for slot, size in zip(row, sizes):
            rects[slot] = QRect(x, y + row_height - size - 46, size, size + 46);x += size + gap
        y += row_height + gap
    return rects


def reel_rects(slots, width):
    size = min(REEL, (width - 8 * (len(slots) - 1)) // max(1, len(slots)))
    x = (width - size * len(slots) - 8 * (len(slots) - 1)) // 2
    return {s: QRect(x + i * (size + 8), 8, size, size) for i, s in enumerate(slots)}


class Reel(QWidget):
    """Icons scrolling vertically; ghosts of each icon make the motion blur."""
    def __init__(self, pixmaps, parent):
        super().__init__(parent);self.pixmaps = pixmaps;self.offset = 0.;self.speed = 0.

    def set_offset(self, value):
        self.speed = abs(value - self.offset);self.offset = float(value);self.update()

    def paintEvent(self, event):
        p = QPainter(self);p.setRenderHint(QPainter.SmoothPixmapTransform);p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(170, 40, 52, 170), 1.5));p.setBrush(QColor(16, 10, 11, 235));p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 10, 10)
        side = self.height();first = int(self.offset // side);fraction = self.offset - first * side
        p.setClipRect(self.rect().adjusted(2, 2, -2, -2));ghosts = 3 if self.speed > side * .08 else 1
        for row in range(2):
            pixmap = self.pixmaps[(first + row) % len(self.pixmaps)]
            for ghost in range(ghosts):
                p.setOpacity(1. if ghosts == 1 else .5 / (ghost + 1))
                p.drawPixmap(QRect(6, round(6 + row * side - fraction - ghost * self.speed * .3), side - 12, side - 12), pixmap)


class ResultCard(QWidget):
    """Large icon, name and rarity colour over a soft red glow."""
    def __init__(self, slot, choice, root, on_reroll, parent):
        super().__init__(parent);self.slot, self.choice = slot, choice
        self.pixmap = QPixmap(str(root / choice.image));self.colour = QColor(RARITY.get(choice.rarity.lower(), '#ece5e5'))
        self.button = ResetButton(self);self.button.clicked.connect(lambda: on_reroll(slot))

    def resizeEvent(self, event):
        self.button.move(self.width() - 30, 2)

    def paintEvent(self, event):
        p = QPainter(self);p.setRenderHint(QPainter.Antialiasing);p.setRenderHint(QPainter.SmoothPixmapTransform)
        side = self.width();glow = QRadialGradient(side / 2, side / 2, side * .7)
        glow.setColorAt(0, QColor(200, 24, 40, 130));glow.setColorAt(1, QColor(200, 24, 40, 0));p.fillRect(self.rect(), glow)
        area = QRect(round(side * .1), round(side * .1), round(side * .8), round(side * .8))
        p.drawPixmap(area, self.pixmap.scaled(area.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        font = QFont(self.font());font.setPixelSize(12);font.setBold(True);p.setFont(font);p.setPen(self.colour)
        p.drawText(QRect(2, side, side - 4, self.height() - side), Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, self.choice.name)


class ExcludeDialog(QDialog):
    """Things the user doesn't own: the game doesn't tell us."""
    def __init__(self, parent, groups, excluded, t):
        super().__init__(parent);self.setWindowTitle(t('rand_exclude'));self.resize(420, 520);self.lists = []
        layout = QVBoxLayout(self);search = QLineEdit();search.setPlaceholderText(t('rand_search'));layout.addWidget(search)
        tabs = QTabWidget();layout.addWidget(tabs)
        for title, choices in groups:
            box = QListWidget();tabs.addTab(box, title);self.lists.append(box)
            for choice in sorted(choices, key=lambda c: c.name):
                item = QListWidgetItem(choice.name);item.setData(Qt.UserRole, choice.id);item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if choice.id in excluded else Qt.Unchecked);box.addItem(item)
        search.textChanged.connect(self.apply_filter)
        done = BloodButton(t('apply'));done.clicked.connect(self.accept);layout.addWidget(done)

    def apply_filter(self, text):
        for box in self.lists:
            for i in range(box.count()):
                box.item(i).setHidden(text.lower() not in box.item(i).text().lower())

    def excluded(self):
        return [b.item(i).data(Qt.UserRole) for b in self.lists for i in range(b.count()) if b.item(i).checkState() == Qt.Checked]


class Stage(QWidget):
    def __init__(self, on_click):
        super().__init__();self.on_click = on_click

    def mousePressEvent(self, event):
        self.on_click()


class RandomizerPage(QWidget):
    def __init__(self, root, pool, settings, save, t, rng=random):
        super().__init__();self.root, self.pool, self.settings, self.save, self.t, self.rng = root, pool, settings, save, t, rng
        self.result = {};self.reels = {};self.cards = {};self.running = [];self.group = None
        outer = QVBoxLayout(self);outer.setContentsMargins(0, 0, 0, 0);outer.setSpacing(8)
        row = QHBoxLayout();self.role = QComboBox();self.role.addItem(t('killer'), 'killer');self.role.addItem(t('survivor'), 'survivor')
        self.role.setCurrentIndex(max(0, self.role.findData(settings.get('role', 'killer'))));self.role.currentIndexChanged.connect(self.options_changed);row.addWidget(self.role)
        self.roll_button = BloodButton(t('rand_roll'), primary=True);self.roll_button.setMinimumHeight(42);self.roll_button.clicked.connect(self.roll);row.addWidget(self.roll_button, 1)
        self.copy_button = BloodButton(t('rand_copy'));self.copy_button.clicked.connect(self.copy);self.copy_button.setEnabled(False);row.addWidget(self.copy_button)
        self.exclude_button = BloodButton(t('rand_exclude'));self.exclude_button.clicked.connect(self.edit_exclusions);row.addWidget(self.exclude_button);outer.addLayout(row)
        self.toggles = {}
        row = QHBoxLayout();row.setSpacing(14)
        for key in ('perks', 'character', 'offering', 'limited', 'ptb'):
            toggle = ToggleSwitch(t('rand_opt_' + key));toggle.setChecked(settings.get(key) is True);toggle.toggled.connect(self.options_changed);self.toggles[key] = toggle;row.addWidget(toggle)
        row.addStretch();outer.addLayout(row)
        self.stage = Stage(self.skip);outer.addWidget(self.stage, 1);self.options_changed()

    @property
    def options(self):
        return Options(role=self.role.currentData(), excluded=frozenset(self.settings.get('exclude', [])), **{k: t.isChecked() for k, t in self.toggles.items()})

    def options_changed(self, *args):
        killer = self.role.currentData() == 'killer'
        self.toggles['perks'].setVisible(killer);self.toggles['character'].setVisible(not killer)
        self.settings.update(role=self.role.currentData(), **{k: t.isChecked() for k, t in self.toggles.items()});self.save()

    def edit_exclusions(self):
        pool, t = self.pool, self.t
        groups = ((t('killer'), pool.killers), (t('survivor'), pool.survivors), (t('perks'), pool.perks['killer'] + pool.perks['survivor']))
        dialog = ExcludeDialog(self, groups, set(self.settings.get('exclude', [])), t)
        if dialog.exec():
            self.settings['exclude'] = dialog.excluded();self.save()

    def clear_stage(self):
        for widget in [*self.reels.values(), *self.cards.values()]:
            widget.hide();widget.deleteLater()
        self.reels, self.cards = {}, {}

    def roll(self):
        self.stop_animation();self.result = self.pool.roll(self.options, self.rng);self.clear_stage();self.copy_button.setEnabled(True)
        slots = [s for s in ORDER if s in self.result];rects = reel_rects(slots, self.stage.width())
        self.running = []
        for slot, delay in zip(slots, stop_times(len(slots))):
            final = self.result[slot].image;images = [i for i in self.pool.spin_images(slot, self.result, self.options, 14, self.rng) if i != final][:13] + [final]
            reel = Reel([QPixmap(str(self.root / i)) for i in images], self.stage);reel.setGeometry(rects[slot]);reel.show();self.reels[slot] = reel
            side = rects[slot].height();end = side * (len(images) * 3 - 1)  # Lands on the final icon after three turns.
            animation = QVariantAnimation(self, startValue=0., endValue=float(end), duration=delay);animation.setEasingCurve(QEasingCurve.OutCubic)
            animation.valueChanged.connect(reel.set_offset);animation.start();self.running.append(animation)
        self.running[-1].finished.connect(self.reveal)

    def stop_animation(self):
        for animation in self.running:
            try:
                animation.finished.disconnect()
            except (RuntimeError, TypeError):
                pass
            animation.stop()
        self.running = []
        if self.group:
            self.group.stop();self.group = None

    def skip(self):
        if self.running:
            self.stop_animation();self.reveal(animate=False)
        elif self.group:
            self.group.setCurrentTime(REVEAL_MS)

    def reveal(self, animate=True):
        self.running = [];starts = {s: r.geometry() for s, r in self.reels.items()}
        for reel in self.reels.values():
            reel.hide();reel.deleteLater()
        self.reels = {};self.build_cards(starts if animate else None)

    def build_cards(self, starts=None):
        for card in self.cards.values():
            card.hide();card.deleteLater()
        self.cards = {};slots = [s for s in ORDER if s in self.result];finals = card_rects(slots, self.stage.width(), self.stage.height())
        self.group = QParallelAnimationGroup(self) if starts else None
        for slot in slots:
            card = ResultCard(slot, self.result[slot], self.root, self.reroll_slot, self.stage);self.cards[slot] = card
            card.setGeometry(starts[slot] if starts else finals[slot]);card.show()
            if starts:
                move = QPropertyAnimation(card, b'geometry', duration=REVEAL_MS, startValue=starts[slot], endValue=finals[slot]);move.setEasingCurve(QEasingCurve.OutCubic);self.group.addAnimation(move)
        if self.group:
            self.group.finished.connect(self.reveal_done);self.group.start()

    def reveal_done(self):
        self.group = None

    def reroll_slot(self, slot):
        if self.running or self.group:
            return
        self.result = self.pool.reroll(self.result, slot, self.options, self.rng);self.build_cards()

    def result_text(self):
        labels = dict(killer=self.t('killer'), survivor=self.t('survivor'), item=self.t('items'), offering=self.t('offerings'))
        return '\n'.join(f"{labels.get(s, self.t('addons') if s in ADDON_SLOTS else self.t('perks').split(' /')[0])}: {self.result[s].name}" for s in ORDER if s in self.result)

    def copy(self):
        QApplication.clipboard().setText(self.result_text())

    def resizeEvent(self, event):
        if self.cards and not self.group:
            self.build_cards()

    def hideEvent(self, event):
        if self.running:
            self.stop_animation();self.reveal(animate=False)
