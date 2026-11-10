"""Crosshair presets and drawing. Pure Qt painting; the overlay window lives in src/gui/crosshair_overlay.py."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen, QPolygonF

# length, thickness, gap and radii in logical pixels at 100 % display scale.
BASE = dict(length=8, thickness=2, gap=4, dot=0, circle=0, outline=False, outline_width=1, color='#00ff66', opacity=100)
PRESETS = {
    'classic_gap': dict(shape='cross'),
    'no_gap': dict(shape='cross', gap=0),
    'cross_dot': dict(shape='cross', dot=2),
    'dot': dict(shape='none', dot=3),
    'small_circle': dict(shape='none', circle=7),
    'circle_dot': dict(shape='none', circle=9, dot=2),
    't_shape': dict(shape='t'),
    'square': dict(shape='square', length=8),
    'chevron': dict(shape='chevron', length=9),
    'x': dict(shape='x'),
    'thin_plus': dict(shape='cross', thickness=1, length=12, gap=2),
    'pro_tight': dict(shape='cross', thickness=1, length=4, gap=2),
    'large_open': dict(shape='cross', length=18, gap=10),
    'cross_outline': dict(shape='cross', outline=True),
    'dot_outline': dict(shape='none', dot=3, outline=True),
}
KEYS = ('length', 'thickness', 'gap', 'dot', 'circle', 'outline', 'outline_width', 'color', 'opacity')
PALETTE = ('#ff2030', '#00ff66', '#00e5ff', '#ffe600', '#ffffff', '#ff30d0', '#ff8a00', '#000000')
FORBIDDEN_KEYS = ('F7', 'F8', 'F9')
DEFAULT_KEY = 'F10'


def preset_config(name, overrides=None):
    return {**BASE, **PRESETS[name], **(overrides or {})}


def extent(config):
    """Half-size of the smallest square holding the crosshair, plus a margin."""
    return config['gap'] + config['length'] + max(config['thickness'], config['dot'] + config['circle']) + config['circle'] + config['outline_width'] + 6


def centre(client, ratio):
    """Logical centre of the DBD client area; `client` is in physical pixels."""
    return (client.x + client.width / 2) / ratio, (client.y + client.height / 2) / ratio


def _lines(config, x, y):
    g, l = config['gap'], config['length'];end = g + l
    shape = config['shape']
    if shape in ('cross', 't'):
        out = [((x - end, y), (x - g, y)), ((x + g, y), (x + end, y)), ((x, y + g), (x, y + end))]
        return out + ([((x, y - end), (x, y - g))] if shape == 'cross' else [])
    if shape == 'x':
        d, e = g * .707, end * .707
        return [((x - e, y - e), (x - d, y - d)), ((x + d, y + d), (x + e, y + e)), ((x - e, y + e), (x - d, y + d)), ((x + d, y - d), (x + e, y - e))]
    if shape == 'chevron':
        return [((x - l, y + l * .7), (x, y)), ((x, y), (x + l, y + l * .7))]
    return []


def draw(painter, x, y, config):
    """Outline first, then colour, so the outline only shows around the shape."""
    painter.setRenderHint(painter.RenderHint.Antialiasing)
    painter.setOpacity(config['opacity'] / 100)
    colour = QColor(config['color']);lines = _lines(config, x, y);width = config['thickness'];square = config['shape'] == 'square'
    half = config['length']

    def stroke(pen_width, c, grow):
        pen = QPen(c, pen_width, Qt.SolidLine, Qt.FlatCap);painter.setPen(pen);painter.setBrush(Qt.NoBrush)
        for a, b in lines:
            painter.drawLine(QPointF(*a), QPointF(*b))
        if square:
            painter.drawRect(QRectF(x - half, y - half, half * 2, half * 2))
        if config['circle']:
            painter.drawEllipse(QPointF(x, y), config['circle'], config['circle'])
        if config['dot']:
            painter.setPen(Qt.NoPen);painter.setBrush(c);painter.drawEllipse(QPointF(x, y), config['dot'] + grow, config['dot'] + grow)
    if config['outline']:
        stroke(width + 2 * config['outline_width'], QColor('#000000'), config['outline_width'])
    stroke(width, colour, 0)
    painter.setOpacity(1)
