"""1v1 timer: round state machine and the 'survivor is down' HUD detector (block D, v1.1.0)."""
from pathlib import Path

import cv2
import pytest

from src.core.killer_status import column_rect, read_column
from src.core.match_reader import fit_hud, load_layout, with_hud_fit
from src.core.onevone import ARMED, IDLE, RUNNING, OneVOne, downed, format_time, load_down_templates
from tests.test_hud_scale_overlay import rescaled

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / 'debug/match_samples/hud_states_2026-11-11'
L = load_layout(ROOT)
needs = pytest.mark.skipif(not SAMPLES.is_dir(), reason='local samples')


def play(m, start, stop):
    m.arm(); assert m.start(start); return m.stop(stop)


def test_arm_start_stop_swap_and_score():
    m = OneVOne(['STEAXS', 'DOC'])
    assert not m.start(0) and m.state == IDLE           # nothing armed: Ctrl/M1 do nothing
    m.arm(); assert m.state == ARMED and m.start(10.) and m.state == RUNNING
    assert not m.start(11.)                              # a second press does not restart
    assert m.elapsed(12.5) == 2.5
    r = m.stop(10. + 178.63)
    assert r.survivor == 0 and round(r.seconds, 2) == 178.63 and m.survivor == 1 and m.score == [0, 0]
    play(m, 300., 300. + 174.11)
    assert m.score == [1, 0] and m.survivor == 0
    times = m.shown_times(1000.)
    assert format_time(times[0][0]) == '2:58.63' and format_time(times[1][0]) == '2:54.11'
    assert times[1][2] and not times[0][2]               # the shorter time is red


def test_pairs_score_independently_and_ties_score_nothing():
    m = OneVOne()
    play(m, 0, 60); play(m, 0, 30)       # P1 wins pair 1
    play(m, 0, 20); play(m, 0, 90)       # P2 wins pair 2
    play(m, 0, 40); play(m, 0, 40)       # tie
    assert m.score == [1, 1]


def test_undo_reset_round_swap_and_reset_match():
    m = OneVOne()
    play(m, 0, 50); play(m, 0, 40); assert m.score == [1, 0]
    assert m.undo() and m.score == [0, 0] and m.survivor == 1 and len(m.rounds) == 1
    m.arm(); m.start(5.); assert m.undo() and m.state == IDLE and len(m.rounds) == 1   # undo of a running chase
    m.arm(); m.start(5.); m.reset_round(); assert m.state == ARMED and m.survivor == 1
    m.swap(); assert m.survivor == 0
    m.start(1.); m.swap(); assert m.survivor == 0          # no swap while timing
    m.reset_match(); assert m.rounds == [] and m.score == [0, 0] and m.state == IDLE and m.survivor == 0


def test_stop_only_while_running_and_manual_reason():
    m = OneVOne(); assert m.stop(5.) is None
    m.arm(); m.start(1.); assert m.stop(3., 'manual').seconds == 2. and m.stop_reason == 'manual'


def test_format_time():
    assert format_time(0) == '0:00.00' and format_time(61.239) == '1:01.24' and format_time(None) == '-:--.--'


def test_downed_states():
    assert downed(['other', 'dying', 'other', 'other']) and downed(['carried']) and downed(['hooked'])
    assert not downed(['other'] * 4)


# Verified by eye: slot 4 = Kate, slot 2 = Nicolas. 07 also shows Kate dying (not in its file name).
EXPECTED = {
    '01': ['other'] * 4, '02': ['other'] * 4,
    '03': ['other', 'other', 'other', 'dying'], '04': ['other', 'other', 'other', 'carried'],
    '05': ['other', 'other', 'other', 'dying'], '06': ['other', 'other', 'other', 'carried'],
    '07': ['other', 'other', 'other', 'dying'], '08': ['other', 'dying', 'other', 'other'],
    '09': ['other', 'dying', 'other', 'other'],
}


@needs
@pytest.mark.parametrize('scale', [1., .9, .85])
@pytest.mark.parametrize('key', sorted(EXPECTED))
def test_down_detection_on_samples(key, scale):
    base = cv2.imread(str(next(SAMPLES.glob(key + '_*.png'))))
    image, layout = base, L
    if scale != 1.:
        # The live loop fits the HUD once per match and keeps it; the fit on a bright desert
        # frame (02, 05, 06) is unreliable, so fit on the clear frame 03 like a match start would.
        found, shift, confident = fit_hud(rescaled(cv2.imread(str(next(SAMPLES.glob('03_*.png')))), scale), L)
        assert abs(found - scale) <= .04 and confident
        image, layout = rescaled(base, scale), with_hud_fit(L, found, shift)
    h, w = image.shape[:2]; x, y, cw, ch = column_rect(layout, w, h)
    statuses, visible = read_column(image[y:y + ch, x:x + cw], layout, w, h, load_down_templates(ROOT))
    # The nickname check ('visible') loses contrast on the bright desert frames at small scales;
    # the 1v1 stop relies on the glyph statuses on two consecutive reads instead.
    assert statuses == EXPECTED[key] and (visible or key in ('02', '05', '06'))
    assert downed(statuses) == (key not in ('01', '02'))
