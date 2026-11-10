"""DBD's 90 % UI scale: native references, and the warning for sizes without references."""
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.core import bloodweb_header as header
from src.core.bloodweb_detector import BloodwebDetector, find_center
from src.core.icon_library import IconLibrary
from src.core.localization import STRINGS
from src.core.match_reader import supported_ui_scale
from src.core.notice_strings import NOTICE_SECTIONS
from src.core.recovery_supervisor import PAUSE, classify

ROOT = Path(__file__).resolve().parents[1]
FRAME = ROOT / 'references/bloodweb/ui90/source_unknown_level11.png'


@pytest.fixture(scope='module')
def frame():
    return cv2.imread(str(FRAME))


def test_level_is_read_at_90(frame):
    level, confidence = header.read_level(frame, ROOT)
    assert level == 11 and confidence >= .95
    assert header.unsupported_ui_scale(frame, ROOT) is None


def test_centre_has_the_smaller_radius(frame):
    assert find_center(frame, ROOT) == (616, 558, 36)


def test_all_nodes_are_found_at_90(frame):
    x, y, r = find_center(frame, ROOT)
    reach = round(r * 10.5)
    crop = frame[y - reach:y + reach, x - reach:x + reach]
    analysis = BloodwebDetector(IconLibrary(ROOT).build(), 'killer', 'unknown').analyze(crop)
    assert len(analysis.nodes) == 7


@pytest.mark.parametrize('size', [.7, .8])
def test_other_sizes_are_reported(size):
    source = cv2.imread(str(ROOT / 'references/bloodweb/bloodweb_level43_finished_waiting_refresh.png'))
    small = cv2.resize(source, None, fx=size, fy=size, interpolation=cv2.INTER_AREA)
    canvas = np.zeros_like(source)
    canvas[:small.shape[0], :small.shape[1]] = small
    assert header.read_level(canvas, ROOT)[0] is None
    assert abs(header.unsupported_ui_scale(canvas, ROOT) - size) <= .04


def test_black_frame_is_not_a_scale_problem():
    assert header.unsupported_ui_scale(np.zeros((1080, 1920, 3), np.uint8), ROOT) is None


def test_warning_pauses_instead_of_retrying():
    assert classify('ui_scale_unsupported') == PAUSE


@pytest.mark.parametrize('language', ['ru', 'en', 'es', 'de', 'fr'])
def test_note_is_translated(language):
    text = STRINGS[language]
    assert '100' in text['ui_scale_note'] and '90' in text['ui_scale_note']
    assert text['ui_scale_unsupported'] == text['ui_scale_note'] == text['notice_ui_scale']
    assert 'notice_ui_scale' in NOTICE_SECTIONS


@pytest.mark.parametrize('scale,ok', [(1., True), (.97, True), (.9, True), (.87, True), (.8, False), (.7, False), (1.1, False)])
def test_supported_hud_scales(scale, ok):
    assert supported_ui_scale(scale) is ok
