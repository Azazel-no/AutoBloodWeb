"""Labelling tool for Sable Detector training frames (J3, 14.11).

Shows the frames saved by the collection mode one by one with the person boxes the detector found.
Keys:  S  this box is Sable        N  this box is not Sable        Tab  next box
       Right / Space  next frame   Left  previous frame              Delete  remove the box
       Drag with the left mouse button to draw a box the detector missed (it becomes Sable).
       A  every remaining box of this frame is not Sable (fast for negatives), then next frame.
Labels are written into each frame's .json ("labels": one entry per box: "sable" / "not_sable").
"""
import json
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout, QWidget

COLOURS = {'sable': QColor(60, 220, 90), 'not_sable': QColor(230, 60, 60), None: QColor(250, 210, 40)}


class Frames:
    """The saved frames and their sidecar records; unlabelled ones first."""
    def __init__(self, folder):
        sidecars = sorted(Path(folder).rglob('*.json'))
        self.items = [s for s in sidecars if s.with_suffix('.jpg').is_file()]
        self.items.sort(key=lambda s: (json.loads(s.read_text(encoding='utf-8')).get('labels') is not None, str(s)))

    def __len__(self): return len(self.items)

    def load(self, index):
        record = json.loads(self.items[index].read_text(encoding='utf-8'))
        record.setdefault('boxes', [])
        if record.get('labels') is None or len(record['labels']) != len(record['boxes']): record['labels'] = [None] * len(record['boxes'])
        return record

    def save(self, index, record):
        self.items[index].write_text(json.dumps(record, indent=1), encoding='utf-8')


def set_label(record, box_index, label):
    record['labels'][box_index] = label


def add_box(record, box):
    record['boxes'].append(dict(box=[int(v) for v in box], confidence=None, score=None, drawn=True)); record['labels'].append('sable')


def remove_box(record, box_index):
    del record['boxes'][box_index]; del record['labels'][box_index]


class Canvas(QWidget):
    def __init__(self, dialog):
        super().__init__(); self.dialog = dialog; self.drag = None; self.setMinimumSize(960, 540); self.setFocusPolicy(Qt.StrongFocus)

    def scale(self):
        image = self.dialog.image
        return min(self.width() / max(1, image.width()), self.height() / max(1, image.height())) if not image.isNull() else 1.

    def paintEvent(self, event):
        p = QPainter(self); d = self.dialog; s = self.scale()
        p.fillRect(self.rect(), QColor(10, 8, 8))
        if d.image.isNull(): return
        p.drawImage(QRectF(0, 0, d.image.width() * s, d.image.height() * s), d.image)
        for i, (entry, label) in enumerate(zip(d.record['boxes'], d.record['labels'])):
            x, y, w, h = entry['box']; pen = QPen(COLOURS[label], 4 if i == d.selected else 2); p.setPen(pen)
            p.drawRect(QRectF(x * s, y * s, w * s, h * s))
            p.drawText(QPointF(x * s + 3, y * s - 4), f"{i + 1}: {label or '?'}" + (f"  {entry['score']:.2f}" if entry.get('score') is not None else ''))
        if self.drag:
            (a, b) = self.drag; p.setPen(QPen(COLOURS['sable'], 2, Qt.DashLine)); p.drawRect(QRectF(a, b).normalized())

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton: self.drag = (event.position(), event.position())

    def mouseMoveEvent(self, event):
        if self.drag: self.drag = (self.drag[0], event.position()); self.update()

    def mouseReleaseEvent(self, event):
        if not self.drag: return
        rect = QRectF(*self.drag).normalized(); s = self.scale(); self.drag = None
        if rect.width() > 8 and rect.height() > 8:
            add_box(self.dialog.record, (rect.x() / s, rect.y() / s, rect.width() / s, rect.height() / s))
            self.dialog.selected = len(self.dialog.record['boxes']) - 1; self.dialog.store()
        self.update()

    def keyPressEvent(self, event):
        self.dialog.key(event.key())


class LabelerDialog(QDialog):
    def __init__(self, folder, t=lambda key, **v: key, parent=None):
        super().__init__(parent); self.t = t; self.frames = Frames(folder); self.index = 0; self.selected = 0
        self.record = dict(boxes=[], labels=[]); self.image = QImage()
        self.setWindowTitle(t('meme_sable_label')); self.resize(1100, 720)
        layout = QVBoxLayout(self); self.info = QLabel(); self.info.setWordWrap(True); layout.addWidget(self.info)
        self.canvas = Canvas(self); layout.addWidget(self.canvas, 1)
        help_text = QLabel(t('meme_sable_label_help')); help_text.setObjectName('muted'); help_text.setWordWrap(True); layout.addWidget(help_text)
        self.open(0)

    def open(self, index):
        if not len(self.frames):
            self.info.setText(self.t('meme_sable_label_empty')); return
        self.index = max(0, min(index, len(self.frames) - 1)); self.record = self.frames.load(self.index); self.selected = 0
        self.image = QImage(str(self.frames.items[self.index].with_suffix('.jpg')))
        done = sum(1 for s in self.frames.items if json.loads(s.read_text(encoding='utf-8')).get('labels') is not None)
        self.info.setText(f"{self.index + 1} / {len(self.frames)} · {self.frames.items[self.index].parent.name}/{self.frames.items[self.index].stem}"
                          f" · {'Sable in match' if self.record.get('sable_in_match') else 'no Sable in match'} · labelled {done}")
        self.canvas.update(); self.canvas.setFocus()

    def store(self):
        self.frames.save(self.index, self.record); self.canvas.update()

    def key(self, key):
        boxes = self.record['boxes']
        if key in (Qt.Key_Right, Qt.Key_Space): self.store(); self.open(self.index + 1)
        elif key == Qt.Key_Left: self.store(); self.open(self.index - 1)
        elif key == Qt.Key_Tab and boxes: self.selected = (self.selected + 1) % len(boxes); self.canvas.update()
        elif key in (Qt.Key_S, Qt.Key_N) and boxes:
            set_label(self.record, self.selected, 'sable' if key == Qt.Key_S else 'not_sable'); self.store()
            if self.selected < len(boxes) - 1: self.selected += 1; self.canvas.update()
        elif key == Qt.Key_A:
            self.record['labels'] = [label or 'not_sable' for label in self.record['labels']]; self.store(); self.open(self.index + 1)
        elif key == Qt.Key_Delete and boxes:
            remove_box(self.record, self.selected); self.selected = max(0, self.selected - 1); self.store()


def main():
    import sys
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / 'debug/sable_frames'
    LabelerDialog(folder).exec()


if __name__ == '__main__':
    main()
