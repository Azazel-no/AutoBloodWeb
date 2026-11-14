"""Memes tab: shared settings (volume, mute, its hotkey) and the per-feature blocks.

Everything here is off by default. The mute hotkey is only read (GetAsyncKeyState in the GUI
layer), never blocked or sent.
"""
from src.core.hotkey_config import parse_hotkey

DEFAULT_MUTE_KEY = 'CTRL+M'   # A combination: it works in any window, so a bare letter would fire while typing.
SECTIONS = ('randomizer', 'chase', 'sable')
CHASE_RANGES = (('size', 50, 200, 100), ('opacity', 30, 100, 100), ('dance_x', 50, 97, 86), ('dance_y', 5, 75, 52), ('dance_size', 15, 60, 34), ('fade', 30, 50, 40))   # fade: tenths of a second
SABLE_RANGES = (('photo_size', 10, 40, 22), ('threshold', 60, 99, 90))   # threshold: the 0..1 identity score in percent


def default_settings():
    return dict(volume=60, muted=False, mute_key=DEFAULT_MUTE_KEY, section='randomizer',
                chase=dict(enabled=False, save_events=False, **{name: default for name, _, _, default in CHASE_RANGES}),
                sable=dict(enabled=False, collect=False, **{name: default for name, _, _, default in SABLE_RANGES}))


def valid_mute_key(text):
    """'ctrl+m' -> 'CTRL+M'; None when it is not a key we can read."""
    key = str(text or '').upper().replace(' ', '')
    try: parse_hotkey(key)
    except ValueError: return None
    return key


def _block(data, ranges):
    data = data if isinstance(data, dict) else {}
    out = dict(enabled=data.get('enabled') is True)
    for name, low, high, default in ranges:
        value = data.get(name); out[name] = value if type(value) is int and low <= value <= high else default
    return out


def clean_settings(data):
    data = data if isinstance(data, dict) else {}; out = default_settings()
    if type(data.get('volume')) is int and 0 <= data['volume'] <= 100: out['volume'] = data['volume']
    out['muted'] = data.get('muted') is True
    out['mute_key'] = valid_mute_key(data.get('mute_key')) or DEFAULT_MUTE_KEY
    if data.get('section') in SECTIONS: out['section'] = data['section']
    out['chase'] = _block(data.get('chase'), CHASE_RANGES); out['sable'] = _block(data.get('sable'), SABLE_RANGES)
    out['chase']['save_events'] = isinstance(data.get('chase'), dict) and data['chase'].get('save_events') is True   # screenshots of starts/refusals, off by default
    out['sable']['collect'] = isinstance(data.get('sable'), dict) and data['sable'].get('collect') is True   # J3: training frames, off by default
    return out


def effective_volume(settings, duck=1.):
    """0..1 for the audio outputs: the meme slider, the mute switch and an optional duck factor."""
    return 0. if settings['muted'] else max(0., min(1., settings['volume'] / 100 * duck))


def combo_down(text, read):
    """True while the combination is held: its key and exactly its Ctrl/Alt/Shift modifiers."""
    modifiers, vk = parse_hotkey(text)
    held = (1 if read(0x12) else 0) | (2 if read(0x11) else 0) | (4 if read(0x10) else 0)
    return bool(read(vk)) and held == modifiers
