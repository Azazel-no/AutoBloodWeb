"""G: Sable Detector. Circle shape, blink, smoothing, cooldown, the HUD gate, part matching and detection on clips."""
import math
import random
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from src.core import sable_detector as sd
from src.core import sable_model
from src.core.meme_pack import Pack
from src.core.memes import default_settings

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / 'debug/match_samples/sable_store_outfits.mp4'
CHASE = ROOT / 'debug/match_samples/chase_2026-11-12/survivorpov_chase_horns_obsession.mp4'
MODEL = sable_model.cached(ROOT) or (ROOT / 'build/models' / sable_model.NAME)
LIBRARY = ROOT / 'assets/sable/library.npz'
needs_clips = pytest.mark.skipif(not (STORE.is_file() and CHASE.is_file() and Path(MODEL).is_file()), reason='local samples and the person model')


# --- circle -------------------------------------------------------------------------------------
def test_circle_is_a_wobbly_loop_whose_end_overshoots_into_a_tail_with_varying_thickness():
    polygon, centre, widths = sd.hand_circle(7)
    angles = np.unwrap([math.atan2(-y, x) for x, y in centre]); swept = abs(angles[-1] - angles[0])
    assert 2 * math.pi + math.radians(30) < swept < 2 * math.pi + math.radians(85)        # More than a full turn: the stroke overlaps itself.
    radii = [math.hypot(x, y) for x, y in centre]
    assert .75 < min(radii) and max(radii) < 1.3 and np.std(radii) > .02                  # An uneven ellipse, not a compass circle.
    assert radii[-1] > radii[0] + .08                                                     # The tail runs outside the start of the stroke.
    assert centre[0][1] < -.5 and centre[-1][1] < -.3                                     # Start and tail are at the top.
    assert max(widths) > 3 * min(widths) and widths[0] < widths[len(widths) // 2] > widths[-1]   # Thin ends, thick middle.
    assert len(polygon) == 2 * len(centre) and all(abs(x) < 1.5 and abs(y) < 1.5 for x, y in polygon)


def test_circle_varies_per_detection_not_per_frame():
    assert sd.hand_circle(3) == sd.hand_circle(3)
    a, b = sd.hand_circle(3)[0], sd.hand_circle(4)[0]
    assert max(abs(p[0] - q[0]) + abs(p[1] - q[1]) for p, q in zip(a, b)) > .02
    assert max(abs(p[0] - q[0]) + abs(p[1] - q[1]) for p, q in zip(a, b)) < .8             # A little, not a different drawing.


def test_blink_is_half_a_second_on_and_half_a_second_off():
    states = [sd.blink_on(i / 100) for i in range(300)]
    assert states[0] and states[49] and not states[50] and not states[99] and states[100] and sum(states) == 150


def test_tracking_is_smoothed_and_follows():
    smooth = sd.SmoothBox(); assert smooth.update((100, 100, 50, 150)) == (100., 100., 50., 150.)
    jumped = smooth.update((200, 100, 50, 150)); assert 100 < jumped[0] < 200                # No teleport on one noisy box.
    for _ in range(12): box = smooth.update((200, 120, 60, 180))
    assert box == pytest.approx((200, 120, 60, 180), abs=1.)
    smooth.reset(); assert smooth.update((5, 5, 5, 5)) == (5., 5., 5., 5.)


def test_circle_surrounds_the_figure_and_the_photo_stays_off_the_centre_and_the_hud():
    for width, height in ((1920, 1080), (2560, 1440), (1280, 720)):
        for bx in (.2, .35, .5, .7, .9):
            for bh in (.1, .3, .6):
                h = height * bh; box = (width * bx - h * .2, height * .3, h * .4, h)
                cx, cy, rw, rh = sd.circle_box(box)
                assert cx - rw < box[0] and cx + rw > box[0] + box[2] and cy - rh < box[1] and cy + rh > box[1] + box[3]
                x, y, w, hh = sd.photo_box(width, height, box, 22, 1.4)
                assert x >= width * .19 - 1 and x + w <= width and y >= 0 and y + hh <= height * .70 + 1   # Not on the HUD column, not on the perks.
                assert x + w <= width * .40 + 1 or x >= width * .60 - 1                                    # Never over the screen centre.


# --- alert state machine ------------------------------------------------------------------------
BOX = (800, 300, 120, 360)


def test_alert_needs_consecutive_hits_hides_after_a_second_and_cools_down_for_three():
    a = sd.Alert(); t = 0.
    assert a.feed(BOX, 0.) is None and a.feed(None, .3) is None                            # A miss breaks the run.
    assert a.feed(BOX, .6) is None and a.feed(BOX, .9) is None and a.feed(BOX, 1.2) == 'show' and a.state == sd.ALERT
    assert a.feed(None, 1.5) is None and a.feed(None, 2.1) is None                         # Missed for 0.6 s: still shown.
    assert a.feed(BOX, 2.15) is None and a.check(3.1) is None                              # Seen again: the count starts over.
    assert a.feed(None, 3.2) is None and a.check(3.9) is None
    assert a.check(4.01) == 'hide' and a.state == sd.COOLDOWN                              # 0.8 s after the first miss.
    for i in range(9): assert a.feed(BOX, 4.1 + i * .3) is None                            # 3 s cooldown: nothing, however sure.
    assert a.state == sd.COOLDOWN
    assert a.feed(BOX, 7.05) is None and a.feed(BOX, 7.3) is None and a.feed(BOX, 7.6) == 'show'
    assert a.check(7.6 + sd.GONE_SECONDS + sd.STALL_SECONDS) == 'hide'                     # No results at all (stalled worker): it still ends.
    a.reset(); [a.feed(BOX, t) for t in (0., .3, .6)]
    assert a.stop() == 'hide' and a.state == sd.IDLE and a.stop() is None


# --- HUD gate -----------------------------------------------------------------------------------
def portrait(seed):
    rng = np.random.default_rng(seed); image = cv2.GaussianBlur(rng.integers(0, 255, (96, 96, 3), dtype=np.uint8), (0, 0), 6)
    image = cv2.normalize(image, None, 0, 255, cv2.NORM_MINMAX); yy, xx = np.ogrid[:96, :96]
    return cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32), ((xx - 48) ** 2 + (yy - 48) ** 2) <= (48 * .72) ** 2


def test_gate_opens_only_for_sables_portrait_and_latches_until_the_match_ends():
    sable, others = portrait(1), [portrait(s) for s in (2, 3, 4, 5)]
    gate = sd.SableGate([sable])
    for _ in range(5): assert gate.feed(others, 'killer') is False                         # No Sable: the detector stays off.
    with_sable = [others[0], others[1], sable, others[3]]
    assert gate.feed(with_sable, 'killer') is False and gate.feed(with_sable, 'killer') is True and gate.slot == 2
    assert gate.feed(others, 'killer') is True                                             # Hooked or injured later: the portrait is covered, the gate stays open.
    gate.reset(); assert gate.open is False
    own = [sable, *others[:3]]                                                             # As a survivor the top portrait is the player.
    for _ in range(4): assert gate.feed(own, 'survivor') is False
    assert gate.feed(own, 'killer') is False and gate.feed(own, 'killer') is True
    assert sd.SableGate([]).feed(with_sable, 'killer') is False                            # No template: never open.


def test_gate_templates_are_read_from_the_hud_portraits_folder(tmp_path):
    assert sd.load_gate_templates(tmp_path) == []
    (tmp_path / 'assets/ui/hud_portraits').mkdir(parents=True); cv2.imwrite(str(tmp_path / 'assets/ui/hud_portraits/sable_ward_1.png'), np.full((72, 72, 3), 90, np.uint8))
    cv2.imwrite(str(tmp_path / 'assets/ui/hud_portraits/meg_thomas_1.png'), np.full((72, 72, 3), 90, np.uint8))
    templates = sd.load_gate_templates(tmp_path); assert len(templates) == 1 and templates[0][0].shape == (96, 96, 3)


# --- identity -----------------------------------------------------------------------------------
def figure(head, torso, legs, size=(300, 120)):
    image = np.zeros((size[0], size[1], 3), np.uint8); h = size[0]
    image[:int(h * .2)] = head; image[int(h * .2):int(h * .55)] = torso; image[int(h * .55):] = legs
    return image


PINK, WHITE, BLUE, RED, BLACK, GREEN, ORANGE = (180, 105, 255), (235, 235, 235), (160, 60, 30), (30, 30, 200), (25, 25, 25), (60, 160, 60), (0, 140, 255)


def library_of(*figures):
    pieces = {part: [] for part in sd.PART_WEIGHTS}
    for image in figures:
        for part, feature in sd.person_features(image, (0, 0, image.shape[1], image.shape[0])).items(): pieces[part].append(feature)
    return sd.Library({part: np.array(v, np.float32) for part, v in pieces.items()})


def test_parts_are_matched_independently_so_mixed_outfits_still_score():
    library = library_of(figure(PINK, WHITE, BLUE), figure(WHITE, RED, BLACK)); full = lambda image: (0, 0, image.shape[1], image.shape[0])
    same = figure(PINK, WHITE, BLUE); mixed = figure(PINK, RED, BLUE); other = figure(GREEN, ORANGE, GREEN)   # head of one set, torso of the other
    score_same, _ = library.score(sd.person_features(same, full(same))); score_mixed, parts = library.score(sd.person_features(mixed, full(mixed)))
    score_other, _ = library.score(sd.person_features(other, full(other)))
    assert score_same > .98 and score_mixed > .95 and min(parts.values()) > .9 and score_other < score_mixed - .2
    only_head = figure(PINK, ORANGE, GREEN); total, parts = library.score(sd.person_features(only_head, full(only_head)))
    assert parts['head'] > .9 and total == pytest.approx(.5 * parts['head'] + .3 * parts['torso'] + .2 * parts['legs'])
    assert sd.PART_WEIGHTS == dict(head=.5, torso=.3, legs=.2)
    assert not sd.Library({}).ready and sd.Library({}).score(sd.person_features(same, full(same)))[0] == 0.


def test_library_round_trip(tmp_path):
    library = library_of(figure(PINK, WHITE, BLUE)); library.save(tmp_path / 'sable/library.npz', note=np.array('x'))
    loaded = sd.Library.load(tmp_path / 'sable/library.npz'); assert loaded.ready and np.allclose(loaded.pieces['head'], library.pieces['head'])


def test_find_sable_skips_the_own_character_our_photo_and_tiny_boxes():
    canvas = np.zeros((1080, 1920, 3), np.uint8); sable = figure(PINK, WHITE, BLUE)
    library = library_of(sable)
    def put(x, y, h):
        w = round(h * .4); canvas[y:y + h, x:x + w] = cv2.resize(sable, (w, h), interpolation=cv2.INTER_NEAREST); return (x, y, w, h)
    far, own, photo, tiny = put(1400, 300, 260), put(860, 480, 560), put(300, 200, 300), put(1700, 100, 40)
    people = [(box, .8) for box in (far, own, photo, tiny)]
    hit, hits = sd.find_sable(canvas, people, library, .9, role='survivor', ignore=[photo])
    assert hit.box == far and len(hits) == 1                                               # Own character, the photo and the speck are not candidates.
    hit, hits = sd.find_sable(canvas, people, library, .9, role='killer', ignore=[photo])
    assert len(hits) == 2 and hit.score > .98                                              # A killer has no own survivor on screen.
    assert sd.find_sable(canvas, [(far, .8)], library_of(figure(GREEN, ORANGE, GREEN)), .9)[0] is None
    assert sd.overlap((0, 0, 10, 10), (0, 0, 10, 10)) == pytest.approx(1.) and sd.overlap((0, 0, 10, 10), (20, 20, 5, 5)) == 0.


# --- model download -----------------------------------------------------------------------------
def test_model_comes_only_from_the_yolox_release_and_is_verified(tmp_path):
    assert sable_model.trusted(sable_model.URL) and sable_model.trusted('https://release-assets.githubusercontent.com/x') and not sable_model.trusted('http://github.com/x')
    assert not sable_model.trusted('https://github.com.evil.example/x') and 'ultralytics' not in sable_model.URL.lower()
    class Response:
        def __init__(self, url, body): self.url, self.body = url, body
        def read(self, limit): return self.body[:limit]
        def __enter__(self): return self
        def __exit__(self, *args): return False
    with pytest.raises(ValueError, match='model_size_mismatch'): sable_model.download(tmp_path, lambda request, timeout: Response(sable_model.URL, b'short'))
    with pytest.raises(ValueError, match='model_sha256_mismatch'): sable_model.download(tmp_path, lambda request, timeout: Response(sable_model.URL, bytes(sable_model.SIZE)))
    with pytest.raises(ValueError, match='foreign_redirect'): sable_model.download(tmp_path, lambda request, timeout: Response('https://example.com/x', b''))
    assert sable_model.cached(tmp_path) is None and not sable_model.model_path(tmp_path).exists()


# --- controller ---------------------------------------------------------------------------------
class Backend:
    def __init__(self): self.image = np.zeros((1080, 1920, 3), np.uint8)
    def info(self): return SimpleNamespace(hwnd=1, foreground=True, client=SimpleNamespace(x=0, y=0, width=1920, height=1080))
    def grab(self, rect): return self.image[rect.y:rect.y + rect.height, rect.x:rect.x + rect.width], None


class Window:
    def __init__(self): self.visible = False; self.shown = []
    def hide(self): self.visible = False
    def present(self, client, *args): self.visible = True; self.shown.append(args)


class Siren:
    def __init__(self, path, parent=None): self.on = False; self.volume = None; self.starts = 0
    def play(self): self.on = True; self.starts += 1
    def stop(self): self.on = False
    def set_volume(self, volume): self.volume = volume
    def close(self): self.on = False


class Clock:
    now = 0.
    def __call__(self): return self.now


class FakeDetector:
    """Stands in for YOLOX: 'finds' whatever boxes the test says are on screen."""
    def __init__(self): self.boxes = []; self.calls = 0
    def detect(self, image): self.calls += 1; scale = image.shape[1] / 1920; return [(tuple(round(v * scale) for v in box), .8) for box in self.boxes]


@pytest.fixture
def sable(tmp_path):
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication
    from src.gui.sable_overlay import SableController
    app = QApplication.instance() or QApplication([])
    class Hub(QObject):
        volume_changed = Signal(); pack_changed = Signal()
        def __init__(self):
            super().__init__(); photo = tmp_path / 'a.png'; cv2.imwrite(str(photo), np.full((80, 60, 3), 200, np.uint8))
            self.pack = Pack(tmp_path, photos=[photo], siren=tmp_path / 'siren.mp3'); self.settings = default_settings(); self.users = set()
        def use(self, name, active): (self.users.add if active else self.users.discard)(name)
        def reload(self): return self.pack
        def volume(self, duck=1.): return .6
    backend = Backend(); clock = Clock(); detector = FakeDetector(); figure_image = figure(PINK, WHITE, BLUE); gate_portrait = portrait(1)
    c = SableController(ROOT, default_settings()['sable'], Hub(), lambda: True, lambda: False, backend=backend, window=Window(), clock=clock, sound=Siren,
                        detector=detector, library=library_of(figure_image), templates=[gate_portrait], foreground=lambda: 1, rng=random.Random(2), threaded=False)
    c.watch.role_reader = lambda own, w, h, s: (True, 'killer'); c.watch.column_visible = lambda client: True
    state = SimpleNamespace(sable_in_hud=False)
    c.read_gate = lambda: c.gate.feed([gate_portrait if state.sable_in_hud else portrait(9)] * 4, 'killer')
    c.enabled = True
    def put(box):
        backend.image = np.zeros((1080, 1920, 3), np.uint8); detector.boxes = []
        if box: x, y, w, h = box; backend.image[y:y + h, x:x + w] = cv2.resize(figure_image, (w, h), interpolation=cv2.INTER_NEAREST); detector.boxes = [box]
    def run(seconds, step=1 / 30):
        end = clock.now + seconds
        while clock.now < end: clock.now += step; c.step()
    return SimpleNamespace(c=c, put=put, run=run, state=state, detector=detector, clock=clock)


def test_detector_is_off_without_sable_in_the_hud_and_costs_nothing(sable):
    sable.put((1300, 300, 140, 380)); sable.run(5.)
    assert not sable.c.gate.open and sable.detector.calls == 0 and not sable.c.window.visible and sable.c.state() == 'meme_sable_state_no_sable'


def test_alert_follows_her_blinks_plays_the_siren_and_ends_with_a_cooldown(sable, caplog):
    c = sable.c; sable.state.sable_in_hud = True; sable.put((1300, 300, 140, 380))
    with caplog.at_level('INFO'): sable.run(4.)
    assert c.gate.open and c.alert.state == sd.ALERT and c.window.visible and c.siren.on and c.siren.volume == .6
    assert any('Sable Detector scores' in r.message for r in caplog.records) and any('Sable Detector ALERT' in r.message for r in caplog.records)
    polygon = c.window.shown[-1][0]; assert all(args[0] is polygon for args in c.window.shown)        # One shape per detection: no per-frame jitter.
    on = [args[2] for args in c.window.shown]; assert True in on and False in on                        # It blinks.
    flips = sum(a != b for a, b in zip(on, on[1:])); assert 3 <= flips <= 8                              # About once a second over ~3 s.
    photo_rect = c.window.shown[-1][4]; assert photo_rect and (photo_rect[0] + photo_rect[2] <= 1920 * .40 + 1 or photo_rect[0] >= 1920 * .60 - 1)
    sable.put((1500, 320, 140, 380)); sable.run(3.)
    cx = c.window.shown[-1][1][0]; assert cx == pytest.approx(1570, abs=12)                              # The circle followed her.
    sable.put(None); sable.run(.6); assert c.alert.state == sd.ALERT                                    # Gone for a moment: still shown.
    sable.run(1.); assert c.alert.state == sd.COOLDOWN and not c.window.visible and not c.siren.on      # About a second after she left.
    sable.put((1300, 300, 140, 380)); sable.run(2.2); assert not c.window.visible                       # Cooldown: 3 s.
    sable.run(3.); assert c.alert.state == sd.ALERT and c.window.visible and c.siren.starts == 2
    assert c.window.shown[-1][0] is not polygon                                                          # A new detection: a new variation of the circle.


def test_detection_runs_at_most_four_times_a_second_and_stops_with_the_match(sable):
    c = sable.c; sable.state.sable_in_hud = True; sable.put((1300, 300, 140, 380)); sable.run(2.); before = sable.detector.calls
    sable.run(10.); rate = (sable.detector.calls - before) / 10; assert 1. <= rate <= 4.
    c.watch.column_visible = lambda client: False; sable.run(12.)                                        # HUD gone for good: the match ended.
    assert not c.gate.open and not c.window.visible and not c.siren.on and c.alert.state == sd.IDLE
    calls = sable.detector.calls; sable.run(3.); assert sable.detector.calls == calls


def test_preview_shows_the_alert_without_a_match(sable):
    c = sable.c; c.enabled = False; c.preview(); sable.run(1.)
    assert c.window.visible and c.siren.on and sable.detector.calls == 0
    sable.run(6.); assert not c.window.visible and not c.siren.on


# --- detection on the clips ---------------------------------------------------------------------
@needs_clips
def test_person_model_and_library_on_the_store_and_the_non_sable_clip():
    """Positive: Sable in the store (the library's own source, other frames). Negative: a match without Sable.
    This proves the pipeline end to end; it does not measure in-match recall (no in-match Sable footage yet)."""
    detector = sd.PersonDetector(MODEL); library = sd.Library.load(LIBRARY); assert library.ready
    capture = cv2.VideoCapture(str(STORE)); found = scored = 0
    for second in (7.1, 23.3, 37.7, 52.9, 66.1, 81.3):
        capture.set(cv2.CAP_PROP_POS_MSEC, second * 1000); ok, frame = capture.read(); assert ok
        people = [(b, c) for b, c in detector.detect(frame) if b[0] + b[2] / 2 > 1920 * .45 and b[3] > 400]
        if people:
            found += 1; hit, _ = sd.find_sable(frame, people, library, .88, role='killer'); scored += hit is not None
    assert found >= 5 and scored >= found - 1
    capture = cv2.VideoCapture(str(CHASE)); frames = 0; alarms = 0; alert = sd.Alert()
    while True:
        ok, frame = capture.read()
        if not ok: break
        frames += 1
        if frames % 8: continue
        hit, _ = sd.find_sable(frame, detector.detect(frame), library, .90, role='survivor')
        alarms += alert.feed(hit.box if hit else None, frames / 30) == 'show'
    assert alarms == 0


# --- HUD gate on the real samples (14.11: debug/match_samples/sable_hud, killer_ds slot 3 is Sable too) ---
SABLE_HUD = ROOT / 'debug/match_samples/sable_hud/sable_hud_portraits.png'
needs_hud = pytest.mark.skipif(not SABLE_HUD.is_file(), reason='local samples')


def hud_features(path):
    from src.core.match_reader import fit_hud, hud_portraits, load_layout, with_hud_fit
    image = cv2.imread(str(path)); base = load_layout(ROOT)
    return hud_portraits(image, with_hud_fit(base, *fit_hud(image, base)[:2]))


def test_gate_templates_ship_with_the_app():
    templates = sd.load_gate_templates(ROOT)
    assert len(templates) >= 5 and all(t[0].shape == (96, 96, 3) for t in templates)


@needs_hud
@pytest.mark.parametrize('crop', [0, 1])
def test_gate_opens_on_the_sable_hud_sample_including_the_obsession(crop):
    from src.core.match_reader import portrait_feature
    image = cv2.imread(str(SABLE_HUD)); x = (83, 292)[crop]
    portraits = [portrait_feature(image, (x, y), 36) for y in (63, 151, 239, 328)]
    for role in ('survivor', 'killer'):
        gate = sd.SableGate(sd.load_gate_templates(ROOT))
        gate.feed(portraits, role); assert gate.feed(portraits, role) and gate.best >= sd.GATE_SCORE
    obsession = sd.SableGate(sd.load_gate_templates(ROOT))
    only = [None, portraits[1], None, None]                          # Slot 2 is the Obsession (big horns).
    obsession.feed(only, 'survivor'); assert obsession.feed(only, 'survivor') and obsession.slot == 1


@needs_hud
def test_gate_opens_in_the_killer_match_where_sable_is_the_obsession():
    for name in ('killer_02_slot4_dying', 'killer_03_slot1_hooked_slot4_injured', 'killer_05_slot1_dead_slot2_dying'):
        gate = sd.SableGate(sd.load_gate_templates(ROOT)); portraits = hud_features(ROOT / f'debug/match_samples/killer_ds/{name}.png')
        gate.feed(portraits, 'killer'); assert gate.feed(portraits, 'killer') and gate.slot == 2, name


@needs_hud
def test_gate_stays_closed_in_every_other_sample_match():
    import glob
    checked = 0
    for path in sorted(glob.glob(str(ROOT / 'debug/match_samples/*.png')) + glob.glob(str(ROOT / 'debug/match_samples/*/*.png'))):
        if 'sable' in path or 'killer_ds' in path: continue
        image = cv2.imread(path)
        if image is None or image.shape[:2] != (1080, 1920): continue
        portraits = hud_features(path); gate = sd.SableGate(sd.load_gate_templates(ROOT))
        for role in ('survivor', 'killer'):
            gate.feed(portraits, role); assert not gate.feed(portraits, role), (path, gate.best)
        checked += 1
    assert checked >= 20


# --- J3 (14.11): training-frame collection and labelling ---------------------------------------------
def test_collector_saves_positives_often_negatives_rarely_and_stays_capped(tmp_path, monkeypatch):
    import json
    from src.core import sable_frames as sf
    clock = [0.]; stamps = iter(range(10 ** 6))
    from datetime import datetime, timedelta
    c = sf.FrameCollector(tmp_path, clock=lambda: clock[0], now=lambda: datetime(2026, 11, 14) + timedelta(milliseconds=next(stamps)))
    image = np.full((1080, 1920, 3), 90, np.uint8); people = [((800, 300, 120, 360), .7)]
    saved = []
    for i in range(100):                                  # 10 s at 0.1 s
        clock[0] = i * .1
        saved += [c.offer(image, people, True)] + [c.offer(image, people, False)]
    pos = [p for p in saved if p and p.name.startswith('pos')]; neg = [p for p in saved if p and p.name.startswith('neg')]
    assert 12 <= len(pos) <= 14 and 1 <= len(neg) <= 2
    record = json.loads(pos[0].with_suffix('.json').read_text(encoding='utf-8'))
    assert record['boxes'][0]['box'] == [400, 150, 60, 180] and record['size'] == [960, 540] and record['labels'] is None
    monkeypatch.setattr(sf, 'MAX_FILES', 5); c.prune()
    assert len(list((tmp_path / 'debug/sable_frames').glob('*/*.jpg'))) == 5 and len(list((tmp_path / 'debug/sable_frames').glob('*/*.json'))) == 5


def test_collection_is_off_by_default():
    from src.core.memes import clean_settings, default_settings
    assert default_settings()['sable']['collect'] is False and clean_settings({})['sable']['collect'] is False
    assert clean_settings(dict(sable=dict(collect=True)))['sable']['collect'] is True


def test_labeller_writes_labels_and_drawn_boxes(tmp_path):
    import json
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from src.core.sable_frames import labelled
    from src.gui.sable_labeler import LabelerDialog
    app = QApplication.instance() or QApplication([])
    folder = tmp_path / '2026-11-14'; folder.mkdir()
    for name in ('pos_1', 'neg_2'):
        cv2.imwrite(str(folder / f'{name}.jpg'), np.full((540, 960, 3), 80, np.uint8))
        (folder / f'{name}.json').write_text(json.dumps(dict(sable_in_match=name.startswith('pos'), boxes=[dict(box=[10, 10, 50, 150]), dict(box=[300, 40, 40, 120])], labels=None)), encoding='utf-8')
    d = LabelerDialog(tmp_path)
    d.key(Qt.Key_S); d.key(Qt.Key_N)                              # first box Sable, second not
    from src.gui.sable_labeler import add_box
    add_box(d.record, (500, 100, 40, 130)); d.store()           # a box the detector missed
    d.key(Qt.Key_Right); d.key(Qt.Key_A)                          # the next frame: everything 'not Sable'
    labels = {p.stem: r['labels'] for p, r in labelled(tmp_path)}
    assert sorted(labels) == ['neg_2', 'pos_1'] and sorted(labels.values()) == [['not_sable', 'not_sable'], ['sable', 'not_sable', 'sable']]
