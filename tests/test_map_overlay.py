"""Block E: map overlay placement, hiding outside a match, the loading-screen flow and the image cache."""
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from src.core import map_images
from src.core.map_names import Map, clean_settings, default_settings, ocr_lines
from src.core.match_reader import Geometry, load_layout, with_hud_fit
from src.gui.map_overlay import MATCH_GONE_SECONDS, MapController, map_box

ROOT = Path(__file__).resolve().parents[1]
L = load_layout(ROOT)


@pytest.fixture(autouse=True)
def application():
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.mark.parametrize('scale', [1., .9])
@pytest.mark.parametrize('w,h', [(1920, 1080), (2560, 1440), (1280, 720)])
def test_default_box_is_the_top_left_corner_above_the_hud_portraits(w, h, scale):
    layout = L if scale == 1. else with_hud_fit(L, scale, 0)
    x, y, side = map_box(w, h)
    top_portrait = Geometry(layout, (h, w)).hud(layout['hud']['portrait_x'], layout['hud']['portrait_y'][0])[1]
    radius = layout['hud']['portrait_radius'] * h / 1080 * scale
    assert (x, y) == (0, 0) and side < top_portrait - radius


def test_size_slider_scales_the_box():
    assert map_box(1920, 1080, 1.5)[2] == round(1080 * .33 * 1.5) and map_box(1920, 1080, .5)[2] == round(1080 * .33 * .5)


class Backend:
    def info(self): return SimpleNamespace(hwnd=1, foreground=True, client=SimpleNamespace(x=0, y=0, width=1920, height=1080))
    def grab(self, rect): return np.zeros((1080, 1920, 3), np.uint8), None


class Window:
    visible = False
    def hide(self): self.visible = False
    def present(self, client, image, key): self.visible = True; self.key = key


class Ocr:
    words = []
    def recognize(self, images): return [[{'lang': 'en-US', 'words': self.words}]]


def word(text, x, y): return dict(text=text, x=x, y=y, w=20 * len(text), h=30)


def controller():
    state = {'match': False, 'now': 0.}
    c = MapController(ROOT, default_settings(), lambda: False, backend=Backend(), window=Window(), ocr=Ocr(),
                      clock=lambda: state['now'], in_match=lambda image: state['match'])
    c.game_foreground = lambda: True
    from PySide6.QtGui import QImage
    for m in c.matcher.maps: c.images[m.key] = QImage(4, 4, QImage.Format_RGBA8888)   # no downloads in tests
    return c, state


def test_loading_screen_read_then_shown_only_in_match_and_cleared_after():
    c, state = controller()
    Ocr.words = [word('COLDWIND', 10, 10), word('FARM', 200, 12), word('ROTTEN', 10, 60), word('FIELDS', 160, 61)]
    c.step(); assert c.detected.key == 'Rotten Fields' and not c.window.visible      # loading screen: not shown yet
    state['match'] = True; state['now'] = 5.; c.step(); assert c.window.visible and c.window.key == 'Rotten Fields'
    c.on_key('key_down'); assert not c.window.visible                                  # hotkey hides
    c.on_key('key_down'); assert c.window.visible
    state['match'] = False; state['now'] = 7.; c.step(); assert not c.window.visible and c.detected   # scoreboard/menu: hidden, kept
    Ocr.words = []; state['now'] = 7. + MATCH_GONE_SECONDS + 1; c.step(); assert c.detected is None                  # match over: forgotten


def test_uncertain_read_shows_nothing():
    c, state = controller()
    Ocr.words = [word('HADDONFIELD', 10, 10)]
    c.step(); state['match'] = True; c.step()
    assert c.detected is None and not c.window.visible


def test_manual_pick_wins_and_lasts_until_the_match_ends():
    c, state = controller()
    Ocr.words = [word('COAL', 10, 10), word('TOWER', 120, 10)]
    c.step(); c.pick('The Game'); state['match'] = True; c.step()
    assert c.window.key == 'The Game'
    Ocr.words = []; state['match'] = False; state['now'] = 100.; c.step(); assert c.manual is None
    c.pick(None); assert c.current is None


def test_nothing_is_drawn_while_the_game_is_not_in_front():
    c, state = controller(); c.detected = c.matcher.by_key('The Game'); state['match'] = True
    c.game_foreground = lambda: False; c.step(); assert not c.window.visible


def test_ocr_words_are_grouped_into_lines():
    result = {'words': [word('FIELDS', 160, 62), word('ROTTEN', 10, 60), word('FARM', 200, 12), word('COLDWIND', 10, 10)]}
    assert ocr_lines(result) == ['COLDWIND FARM', 'ROTTEN FIELDS']


def test_settings_default_off_and_cleaned():
    assert default_settings()['enabled'] is False
    assert clean_settings({'enabled': 1, 'size': 500, 'key': ' f1 '}) == dict(enabled=False, key='F1', size=100, opacity=85)


class Response(io.BytesIO):
    def __init__(self, body, url): super().__init__(body); self.url = url
    def __enter__(self): return self
    def __exit__(self, *a): pass


def test_download_checks_host_and_md5_and_caches(tmp_path):
    body = b'webp-bytes'; m = Map('X', 'R', (), 'maps/Hens333/Realm/X Map.webp', hashlib.md5(body).hexdigest())
    base = 'https://raw.githubusercontent.com/o/r/refs/heads/b/'
    seen = []
    def opener(request, timeout): seen.append(request.full_url); return Response(body, request.full_url)
    path = map_images.download(tmp_path, base, m, opener)
    assert seen == [base + 'maps/Hens333/Realm/X%20Map.webp'] and map_images.cached(tmp_path, m) == path
    bad = Map('Y', 'R', (), 'maps/Hens333/Realm/Y.webp', '0' * 32)
    with pytest.raises(ValueError, match='md5'): map_images.download(tmp_path, base, bad, opener)
    with pytest.raises(ValueError, match='foreign'): map_images.download(tmp_path, 'https://example.com/', m, opener)
    assert map_images.cached(tmp_path, bad) is None
