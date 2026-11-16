"""K1 (15.11): the perks overlay kept the previous match's perks when the lobby went unseen; a new match must unlock
without the lobby. Samples: debug/match_samples/perks_2026-11-15 (local only: a Krasue match in which I and the teammate
in row 4 both play Cheryl Mason) and the pair samples of the controller tests. Nicknames here are made up; the checks on
the real screenshots compare what OCR reads on the HUD with what it reads on the Tab of the same match."""
import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import pytest

from src.core import match_identity as mi
from src.core.match_reader import assign_scores, hud_portraits, load_layout, read_tab, score_rows
from src.gui.match_overlay import MATCH
from tests.test_overlay_controller import SAMPLES, FakeMatcher, app, controller, layout, needs_samples  # noqa: F401
from tests.test_overlay_lobby import frames, logged, read_once

ROOT = Path(__file__).resolve().parents[1]
K1 = ROOT / 'debug/match_samples/perks_2026-11-15'
needs_k1 = pytest.mark.skipif(not K1.is_dir(), reason='local samples')
# Match A (13:35, tab_done in debug/overlay/20261115.jsonl): the identity that stayed locked into match B.
MATCH_A = dict(names={1: ['Brambl3Fax007', 'Brambl3Fox007'], 2: ['Mossy Lantern'], 3: ['Moonfire']}, looks={},
               tab_names={1: ['Mossy Lantern'], 2: ['Moonfire'], 3: ['Brambl3Fox007']}, tab_portraits={}, own_rows=[0])
SLOT_ROW_B = {1: 3, 2: 1, 3: 2}   # match B: HUD slot -> Tab row (slot 2 is the second Cheryl Mason, in row 4)


def match_b(ocr, layout):
    """Match B's identity as the locked overlay stored it, from the Tab screenshot (OCR), so no nickname is written here."""
    from src.core.nicknames import read_tab_names
    tab = cv2.imread(str(K1 / 'tab_scoreboard_krasue.png')); tab_names = read_tab_names(ocr, tab, layout, [1, 2, 3])
    return dict(names={slot: list(tab_names[row]) for slot, row in SLOT_ROW_B.items()}, looks={},
                tab_names={row: list(tab_names[row]) for row in (1, 2, 3)}, tab_portraits={}, own_rows=[0])


@pytest.fixture(autouse=True)
def no_match_left_over():
    """MATCH is global: a match 'on' left by these tests would keep other tests' windows mapped."""
    yield
    MATCH.hud_at = None


@pytest.fixture(scope='module')
def ocr(root):
    from src.core.nicknames import NicknameOcr
    client = NicknameOcr(root); client._start()
    if not client.ready: pytest.skip(f'Windows OCR not available: {client.failed}')
    yield client
    client.close()


# --- the identity on the real screenshots (Windows OCR) -------------------------------------------------
@needs_k1
def test_k1_the_hud_of_match_b_is_not_match_a_and_is_match_b(layout, ocr):
    from src.core.nicknames import hud_strips, read_hud_names
    hud = cv2.imread(str(K1 / 'ingame_stale_perks.png'))
    names = read_hud_names(ocr, hud_strips(hud, layout)); looks = hud_portraits(hud, layout)
    verdict, evidence = mi.compare_hud(MATCH_A, names, looks)
    assert verdict == 'different' and all(e['method'] == 'nickname' and e['verdict'] == 'different' for e in evidence.values()), evidence
    assert mi.compare_hud(match_b(ocr, layout), names, looks)[0] == 'same'


@needs_k1
def test_k1_a_tab_press_while_locked_sees_other_nicknames(layout, ocr):
    from src.core.match_reader import tab_rows_identity
    from src.core.nicknames import read_tab_names
    tab = cv2.imread(str(K1 / 'tab_scoreboard_krasue.png')); own, portraits = tab_rows_identity(tab, layout)
    assert own == 0
    rows = [1, 2, 3]; names = read_tab_names(ocr, tab, layout, rows)
    assert mi.compare_tab(MATCH_A, names, {r: portraits[r] for r in rows})[0] == 'different'
    assert mi.compare_tab(match_b(ocr, layout), names, {r: portraits[r] for r in rows})[0] == 'same'


@needs_k1
def test_k1_two_cheryl_masons_are_paired_by_nickname(layout, ocr):
    """Me (row 1) and the teammate in row 4 are both Cheryl Mason: the teammate's slot is decided by nickname."""
    from src.core.nicknames import hud_strips, name_scores, read_hud_names, read_tab_names
    tab = cv2.imread(str(K1 / 'tab_scoreboard_krasue.png')); hud = cv2.imread(str(K1 / 'ingame_stale_perks.png'))
    rows = read_tab(tab, FakeMatcher(), layout)
    assert [r.index for r in rows if r.own] == [0]                     # my row is never a teammate candidate
    teammates = [r.index for r in rows if not r.own]
    scores, _ = score_rows(rows, [[p] for p in hud_portraits(hud, layout)])
    names = name_scores(read_tab_names(ocr, tab, layout, teammates), read_hud_names(ocr, hud_strips(hud, layout)))
    pairs, methods = assign_scores(scores, teammates, names=names)
    assert pairs == SLOT_ROW_B                                         # slot 2 = row 4 (the second Cheryl Mason)
    assert 'nickname' in methods[1]


def test_k1_portraits_decide_only_without_nicknames_and_two_slots_must_differ():
    same, _ = mi.judge(['TEAL OTTER'], [], ['TEAl OTTER'], None)
    other, _ = mi.judge(['Mossy Lantern'], [], ['COPPER WREN'], None)
    unsure, _ = mi.judge(['Moonfire'], [], ['Moonfish'], None)
    assert (same, other) == ('same', 'different') and unsure in ('same', 'unknown')
    assert mi.judge([], [], [], None)[0] == 'unknown'
    assert mi.overall(['different', 'same', 'unknown']) == 'unknown' and mi.overall(['different', 'different', 'same']) == 'different'


# --- the controller: no lobby between two matches ------------------------------------------------------
def inline_identity(monkeypatch):
    """Identity checks run inline in tests, like the scoreboard read."""
    import threading
    from src.gui import match_overlay
    real = threading.Thread
    def thread(target=None, args=(), name=None, **kw):
        if name in ('OverlayRead', 'OverlayIdentity'): return SimpleNamespace(start=lambda: target(*args))
        return real(target=target, args=args, name=name, **kw)
    monkeypatch.setattr(match_overlay.threading, 'Thread', thread)


def lock_on(x, name):
    hud, tab = frames(name)
    x.s.frames = [hud]; x.c.poll(); read_once(x, hud, tab)
    assert x.c.phase == 'locked' and x.c.identity is not None
    x.c.identity_seconds = 0.; x.c.next_identity = 0.                   # every poll checks, for the test
    return hud, tab


@needs_samples
def test_k1_another_match_without_a_lobby_unlocks_after_two_checks(controller, monkeypatch):
    x = controller; inline_identity(monkeypatch); lock_on(x, 'pair02')
    new_hud, new_tab = frames('pair05')                                # other characters (portraits only: OCR is off here)
    x.s.frames = [new_hud]; x.c.poll()
    assert x.c.phase == 'locked' and x.c.identity_strikes == 1         # one check is not enough
    x.c.poll()
    assert x.c.phase == 'match' and not x.c.state.slots and not x.o.visible
    event = logged(x, 'new_match_without_lobby')[0]
    assert sum(s['verdict'] == 'different' for s in event['slots'].values()) >= 2 and event['previous_identity']
    reads = len(logged(x, 'tab_read'))
    read_once(x, new_hud, new_tab)                                     # the next Tab reads the new match (pair05 itself
    assert len(logged(x, 'tab_read')) == reads + 1                     # needs nicknames to pair: test_pair05_... )


@needs_samples
def test_k1_the_same_match_stays_locked(controller, monkeypatch):
    x = controller; inline_identity(monkeypatch); hud, _ = lock_on(x, 'pair02')
    for _ in range(6): x.s.frames = [hud]; x.c.poll()
    assert x.c.phase == 'locked' and x.o.visible and not logged(x, 'new_match_without_lobby')


@needs_samples
def test_k1_hud_back_after_a_long_gap_hides_the_perks_at_once_then_decides(controller, monkeypatch):
    x = controller; inline_identity(monkeypatch); hud, _ = lock_on(x, 'pair02')
    MATCH.hud_at = MATCH.clock() - MATCH.HOLD - 5                       # the HUD was gone for longer than Match.HOLD
    checks = []
    monkeypatch.setattr(x.c, 'check_identity', lambda image, force=False: checks.append(force))
    x.s.frames = [hud]; x.c.poll()
    assert x.c.suspect and not x.o.visible and checks == [True]          # cleared before anything is decided
    monkeypatch.undo(); inline_identity(monkeypatch)
    x.c.poll()                                                         # same nicknames/portraits: shown again, still locked
    assert x.c.phase == 'locked' and not x.c.suspect and x.o.visible


def tab_nicknames(x, monkeypatch, locked, now):
    """OCR stand-in: the locked rows' nicknames and what this Tab frame shows (portraits alone are often 'unknown')."""
    from src.gui import match_overlay
    x.c.identity['tab_names'] = locked; x.c.ocr.ready = True
    monkeypatch.setattr(match_overlay, 'read_tab_names', lambda ocr, image, layout, rows: {r: v for r, v in now.items() if r in rows})
    monkeypatch.setattr(match_overlay, 'read_hud_names', lambda ocr, strips, slots=(1, 2, 3): {})


MATCH_A_ROWS = {1: ['Mossy Lantern'], 2: ['Moonfire'], 3: ['Brambl3Fox007']}


@needs_samples
def test_k1_a_tab_press_while_locked_on_another_scoreboard_unlocks_and_reads_it(controller, monkeypatch):
    x = controller; inline_identity(monkeypatch); lock_on(x, 'pair02')
    _, new_tab = frames('pair02')
    tab_nicknames(x, monkeypatch, MATCH_A_ROWS, {1: ['TEAL OTTER'], 2: ['COPPER WREN'], 3: ['Gravel Moth']})
    reads = len(logged(x, 'tab_read'))
    x.c.handle_action('key_down')
    assert logged(x, 'tab_ignored')[-1]['reason'] == 'locked' and x.c.tab_check
    x.c.watcher.down = True; x.s.frames = [new_tab]; x.c.poll()          # the poll's own frame while Tab is held
    event = logged(x, 'new_match_at_tab')[0]
    assert all(r['verdict'] == 'different' and r['method'] == 'nickname' for r in event['rows'].values())
    assert len(logged(x, 'tab_read')) == reads + 1 and logged(x, 'tab_pressed')[-1]['after'] == 'unlock'


@needs_samples
def test_k1_a_tab_press_while_locked_on_the_same_scoreboard_is_ignored(controller, monkeypatch):
    x = controller; inline_identity(monkeypatch); _, tab = lock_on(x, 'pair02')
    tab_nicknames(x, monkeypatch, MATCH_A_ROWS, {1: ['Mossy Lantern'], 2: ['Moonfire'], 3: ['Brambl3Fox0O7']})
    reads = len(logged(x, 'tab_read'))
    x.c.handle_action('key_down'); x.c.watcher.down = True; x.s.frames = [tab]; x.c.poll(); x.c.poll()
    assert logged(x, 'tab_locked_same_match') and x.c.phase == 'locked' and len(logged(x, 'tab_read')) == reads
    assert not x.c.tab_check                                           # one look per press


@needs_samples
def test_k1_the_end_scoreboard_clears_the_perks_and_the_next_hud_is_a_new_match(controller, monkeypatch):
    x = controller; inline_identity(monkeypatch); lock_on(x, 'pair02')
    x.s.frames = [cv2.imread(str(SAMPLES / 'endgame_scoreboard_clusters_1920x1080.png'))]; x.c.poll()
    assert x.c.phase == 'ended' and not x.c.state.slots and not x.o.visible and logged(x, 'match_over')
    grabs = []; real = x.c.backend.grab
    x.c.backend.grab = lambda client: grabs.append(1) or real(client)
    x.c.handle_action('key_down'); assert grabs == [] and logged(x, 'tab_ignored')[-1]['reason'] == 'ended'
    x.c.backend.grab = real; hud, tab = frames('pair05'); x.s.frames = [hud]; x.c.poll()
    assert x.c.phase == 'match' and logged(x, 'match_detected')[-1]['previous'] == 'ended'


@needs_samples
def test_k1_logs_say_why_a_lock_survived(controller, monkeypatch):
    """No lobby in the log between two matches (13:47-13:49): DBD not in front and the HUD's absence are logged now."""
    x = controller; inline_identity(monkeypatch); hud, _ = lock_on(x, 'pair02')
    x.s.window = x.s.window._replace(foreground=False) if hasattr(x.s.window, '_replace') else SimpleNamespace(**{**vars(x.s.window), 'foreground': False})
    monkeypatch.setattr('src.gui.match_overlay.overlay_in_front', lambda hwnd=None: False)
    x.c.poll(); x.c.poll()
    assert len(logged(x, 'game_not_in_front')) == 1
    stats = logged(x, 'perks_locked')[0]['identity']
    assert stats is not None and json.dumps(stats)
