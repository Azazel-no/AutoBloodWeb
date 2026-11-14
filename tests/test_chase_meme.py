"""F3: chase meme. Weighted lines, timing, dance chance, music loop, the state machine, detection on the samples."""
import random
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from src.core import chase_meme as cm
from src.core.match_reader import fit_hud, load_layout, with_hud_fit
from src.core.meme_pack import Dance, Pack, Voice
from src.core.memes import default_settings

ROOT = Path(__file__).resolve().parents[1]
CHASE = ROOT / 'debug/match_samples/chase_2026-11-12'
HUD = ROOT / 'debug/match_samples/hud_states_2026-11-11'
VIDEO = CHASE / 'survivorpov_chase_horns_obsession.mp4'
needs_video = pytest.mark.skipif(not VIDEO.is_file(), reason='local samples')
needs_hud = pytest.mark.skipif(not HUD.is_dir(), reason='local samples')
VOICES = [Voice(name, Path(name + '.mp3'), weight, 1.) for name, weight in
          (('chuckles3', 3), ('man3', 3), ('whatcanisay3', 3), ('beautiful2', 2), ('mambaout2', 2), ('teamate2', 2), ('dontpassit1', 1))]
DANCES = [Dance(f'dance-{i}', tuple(Path(f'{n:04d}.webp') for n in range(1, 31)), 30.) for i in (1, 2, 3)]


# --- voice lines, intervals, dances -------------------------------------------------------------
def test_weighted_pick_follows_3_2_1_and_never_repeats():
    rng = random.Random(7); last = None; counts = Counter(); picks = 30000
    for _ in range(picks):
        voice = cm.pick_weighted(VOICES, last, rng); assert voice.name != last; last = voice.name; counts[voice.weight] += 1
    per_line = {w: counts[w] / sum(v.weight == w for v in VOICES) for w in (3, 2, 1)}   # plays per single line of that weight
    assert per_line[3] > per_line[2] > per_line[1]
    assert per_line[3] / per_line[1] == pytest.approx(3., rel=.25) and per_line[2] / per_line[1] == pytest.approx(2., rel=.25)
    assert cm.pick_weighted([VOICES[0]], VOICES[0].name, rng) is VOICES[0]      # A single line may repeat: there is nothing else.
    assert cm.pick_weighted([], None, rng) is None


def run_show(seconds, seed=1, step=.05, **kwargs):
    show = cm.ChaseShow(VOICES, DANCES, random.Random(seed), **kwargs); show.start(0.); events = []; t = 0.
    while t < seconds:
        events += [(round(t, 2), kind, item) for kind, item in show.tick(t)]; t += step
    return show, events


def test_a_line_plays_every_15_to_20_seconds_and_the_first_one_is_not_immediate():
    _, events = run_show(600.)
    times = [t for t, kind, _ in events if kind == 'voice']
    assert 28 <= len(times) <= 40 and 15. <= times[0] <= 20.1
    gaps = np.diff(times); assert gaps.min() >= 14.99 and gaps.max() <= 20.1
    names = [item.name for _, kind, item in events if kind == 'voice']
    assert all(a != b for a, b in zip(names, names[1:]))


def test_a_dance_follows_a_quarter_of_the_lines_and_only_after_the_line_ends():
    lines = dances = 0
    for seed in range(40):
        _, events = run_show(400., seed)
        lines += sum(kind == 'voice' for _, kind, _ in events); dances += sum(kind == 'dance' for _, kind, _ in events)
        last_line = None
        for t, kind, item in events:
            if kind == 'voice': last_line = (t, item)
            else: assert last_line and t >= last_line[0] + last_line[1].duration - .051 and t <= last_line[0] + last_line[1].duration + .11
    assert lines > 700 and dances / lines == pytest.approx(.25, abs=.05)
    _, none = run_show(200., dance_chance=0.); assert not [e for e in none if e[1] == 'dance']
    _, always = run_show(200., dance_chance=1.); assert sum(e[1] == 'dance' for e in always) == sum(e[1] == 'voice' for e in always)


def test_music_is_ducked_only_while_a_line_plays_and_a_new_chase_starts_fresh():
    show = cm.ChaseShow(VOICES, DANCES, random.Random(3)); show.start(100.)
    assert not show.ducked(100.) and show.tick(100.) == []
    t = 100.
    while not (events := show.tick(t)): t += .05
    assert events[0][0] == 'voice' and show.ducked(t) and show.ducked(t + .9) and not show.ducked(t + 1.01)
    show.stop(); assert show.tick(t + 30) == [] and not show.ducked(t)
    show.start(500.); assert show.next_line >= 515. and show.line_until is None


# --- music loop ---------------------------------------------------------------------------------
class FakePlayer:
    def __init__(self, length=10.): self.length = length; self.at = None; self.volume = 1.; self.starts = 0
    def play(self): self.at = 0.; self.starts += 1
    def stop(self): self.at = None
    def playing(self): return self.at is not None and self.at < self.length
    def position(self): return min(self.at, self.length) if self.at is not None else 0.
    def duration(self): return self.length
    def set_volume(self, volume): self.volume = volume
    def advance(self, seconds):
        if self.at is not None: self.at += seconds


def test_music_restarts_from_the_beginning_and_loops_with_a_crossfade():
    a, b = FakePlayer(), FakePlayer(); loop = cm.MusicLoop(a, b, crossfade=.5); loop.start(.6)
    assert a.playing() and a.position() == 0. and a.volume == .6 and not b.playing()
    heard = []
    for _ in range(int(25 / .05)):
        a.advance(.05); b.advance(.05); loop.tick(.6)
        heard.append((a.volume if a.playing() else 0.) + (b.volume if b.playing() else 0.))
    assert loop.loops == 2 and a.starts == 2 and b.starts == 1          # 25 s of a 10 s track: A, B, A again.
    assert min(heard) > .3 and max(heard) < .9                           # Never silent at the loop point, never doubled.
    loop.tick(.3); assert max(a.volume, b.volume) == pytest.approx(.3)   # Ducking follows at once.
    loop.stop(); assert not a.playing() and not b.playing()
    loop.start(.6); assert a.playing() and a.position() == 0.            # A new chase: from the start.


def test_crossfade_gains():
    assert cm.crossfade_gains(5., 10., .5) == (1., 0.)
    out, into = cm.crossfade_gains(9.75, 10., .5); assert out == pytest.approx(into) and out ** 2 + into ** 2 == pytest.approx(1.)
    assert cm.crossfade_gains(10., 10., .5)[0] == pytest.approx(0.) and cm.crossfade_gains(3., 0., .5) == (1., 0.)


# --- state machine ------------------------------------------------------------------------------
MOVING = cm.HornSample(60, 60, True, True, .6, .035)   # weak level: needs the heart
STATIC = cm.HornSample(130, 230, True, False, 0.)     # The Obsession's big horns: present, never moving.
NONE = cm.HornSample()


def test_chase_starts_on_animated_horns_and_stops_when_the_heart_is_gone_for_1_5_s():
    d = cm.ChaseDetector(); t = 10.
    assert d.heart(True, t - .6, 900) is None and d.heart(True, t - .4, 900) is None   # the heart (2 reads) confirms weak horns
    assert d.horns(MOVING, t) is None and d.horns(MOVING, t + .1) is None
    event = d.horns(MOVING, t + .2); assert event[0] == 'start' and 'horn animation' in event[1] and d.state == cm.CHASE
    for i in range(50):                                     # Horns fade, the heart keeps the chase alive.
        assert d.horns(NONE, t + .3 + i * .1) is None and d.heart(True, t + .3 + i * .1) is None
    last = t + .3 + 49 * .1
    assert d.heart(False, last + 1.4) is None                # Hidden behind grass for 1.4 s: still a chase.
    assert d.heart(True, last + 1.45) is None
    event = d.heart(False, last + 1.45 + 1.5); assert event[0] == 'stop' and 'heart gone for 1.5 s' in event[1] and d.state == cm.IDLE
    assert d.elapsed(last + 10) == 0.


def test_static_obsession_horns_never_start_a_chase_and_slow_changes_do_not_count():
    d = cm.ChaseDetector()
    for i in range(300): assert d.horns(STATIC, i * .1) is None
    for i in range(20): assert d.horns(MOVING if i % 6 == 0 else STATIC, 100 + i * .1) is None   # One change every 0.6 s is not an animation.
    assert d.state == cm.IDLE


def test_animated_horns_without_the_heart_never_start_a_chase():
    """J1 (14.11): a brightness pulse on the portrait frame / a bright moving background looked like horns."""
    d = cm.ChaseDetector()
    events = [d.horns(MOVING, i * .1) for i in range(60)]
    assert d.state == cm.IDLE and d.rejected > 0 and {e[0] for e in events if e} == {'rejected'}
    assert d.heart(True, 10., 300) is None or d.state == cm.IDLE                 # a heart long after the horns: still nothing
    d.heart(True, 20.0, 900)
    for i in range(3): d.horns(MOVING, 20 + i * .1)
    event = d.heart(True, 20.5, 900); assert event[0] == 'start' and 'heart seen (900 px)' in event[1]
    assert d.stop(25., 'x')[0] == 'stop' and d.stop(26., 'x') is None


def test_timer_text_and_glitch():
    assert cm.format_timer(0) == '00:00.00' and cm.format_timer(83.456) == '01:23.45' and cm.format_timer(-1) == '00:00.00'
    period = cm.GLITCH_PERIOD; rest = cm.timer_glitch(period * 10 + .01); kick = cm.timer_glitch(period * 10 + period * 4.5 / 13)
    assert abs(rest.dx) < .02 and rest.dy == 0 and rest.smear == 0 and rest.light == 1.
    assert kick.dy < -.3 and kick.dx < -.2 and kick.smear == 1. and kick.fringe == 1.      # Up and left, smeared.
    settle = [cm.timer_glitch(period * 10 + period * f / 13) for f in (6.2, 8, 10, 12.9)]
    assert settle[0].light < settle[-1].light and settle[0].dx < settle[-1].dx and abs(settle[-1].dx) < .06 and abs(settle[-1].dy) < .01
    assert cm.timer_glitch(1.234) == cm.timer_glitch(1.234)                                # Deterministic: no per-frame jitter.
    samples = [cm.timer_glitch(i / 30) for i in range(900)]
    assert all(abs(g.dx) <= .5 and -.7 <= g.dy <= 0 and .45 <= g.light <= 1. for g in samples)
    assert .15 < sum(g.smear == 1. for g in samples) / len(samples) < .3                   # About three frames of thirteen.


# --- detection on the samples -------------------------------------------------------------------
def video_frames():
    capture = cv2.VideoCapture(str(VIDEO)); frames = []
    while True:
        ok, frame = capture.read()
        if not ok: break
        frames.append(frame)
    return frames


@pytest.fixture(scope='module')
def sample():
    frames = video_frames(); base = load_layout(ROOT)
    layouts = [with_hud_fit(base, *fit_hud(frames[i], base)[:2]) for i in (30, 150)]
    return frames, layouts


def detect(frames, layouts, horn_step=3, heart_step=6, tail=75):
    """Runs the video (a montage: frames 0-68 one HUD position, the rest another) and then `tail` frames without the game."""
    d = cm.ChaseDetector(); reader = cm.HornReader(); events = []; blank = np.zeros_like(frames[0])
    for i, frame in enumerate(frames + [blank] * tail):
        now = i / 30; layout = layouts[0 if i < 69 else 1]; event = None
        if i % horn_step == 0:
            x, y, w, h = cm.horn_rect(layout, 1920, 1080); event = d.horns(reader.read(frame[y:y + h, x:x + w]), now)
        if event is None and i % heart_step == 0:
            x, y, w, h = cm.heart_rect(1920, 1080); event = d.heart(cm.heart_seen(frame[y:y + h, x:x + w], 1080), now)
        if event and event[0] != 'rejected': events.append((round(now, 2), *event))
    return events


@needs_video
@pytest.mark.parametrize('horn_step', (2, 3, 4))
def test_sample_video_one_chase_from_the_bright_horns_to_the_heart_going_out(sample, horn_step):
    frames, layouts = sample; events = detect(frames, layouts, horn_step)
    assert [e[1] for e in events] == ['start', 'stop']
    start, stop = events
    assert 3.0 <= start[0] <= 3.7            # The horns turn bright and animate from ~3.1 s; nothing before (first clip: no chase).
    assert 13.9 <= stop[0] <= 14.4           # The heart is last seen at ~12.7 s; 1.5 s later the chase ends.
    assert 'heart gone' in stop[2]


@needs_video
def test_sample_video_heart_flicker_and_hud_at_two_scales(sample):
    frames, layouts = sample
    assert layouts[0]['hud']['fit_scale'] < .9 and layouts[1]['hud']['fit_scale'] == 1.     # The montage: HUD at 84 % and at 100 %.
    seen = []
    for frame in frames:
        x, y, w, h = cm.heart_rect(1920, 1080); seen.append(cm.heart_seen(frame[y:y + h, x:x + w], 1080))
    assert not any(seen[:70]) and not any(seen[383:])            # No heart in the first clip (no killer near); it goes out at ~12.7 s.
    lit = seen[100:380]; gaps = [len(run) for run in ''.join('x' if s else '.' for s in lit).split('x') if run]
    assert sum(lit) / len(lit) > .9 and max(gaps, default=0) < 45        # It flickers for less than the 1.5 s debounce.


@needs_hud
def test_static_frames_never_start_a_chase_even_with_obsession_or_chase_horns():
    base = load_layout(ROOT); found = {}
    for path in sorted(HUD.glob('*.png')):
        image = cv2.imread(str(path)); layout = with_hud_fit(base, *fit_hud(image, base)[:2])
        for slot in range(4):
            d = cm.ChaseDetector(); reader = cm.HornReader(); x, y, w, h = cm.horn_rect(layout, 1920, 1080, slot)
            for i in range(40):
                sample = reader.read(image[y:y + h, x:x + w]); assert d.horns(sample, i * .1) is None
            found[(path.name[:2], slot)] = sample.present
    assert found[('01', 0)]        # Meg's Obsession horns and Nicolas's chase horns are seen, but a still image has no motion.


# --- controller ---------------------------------------------------------------------------------
class Backend:
    def __init__(self): self.image = np.zeros((1080, 1920, 3), np.uint8)
    def info(self): return SimpleNamespace(hwnd=1, foreground=True, client=SimpleNamespace(x=0, y=0, width=1920, height=1080))
    def grab(self, rect): return self.image[rect.y:rect.y + rect.height, rect.x:rect.x + rect.width], None


class Window:
    def __init__(self): self.visible = False; self.shown = []
    def hide(self): self.visible = False
    def present(self, client, *args): self.visible = True; self.shown.append(args)


class Clock:
    now = 0.
    def __call__(self): return self.now


class FakeSound(FakePlayer):
    made = []
    def __init__(self, path, parent=None): super().__init__(175.); self.path = path; FakeSound.made.append(self)
    def close(self): self.stop()


class SyncLoader:
    """Decodes on demand: tests have no time for the loader thread to run ahead."""
    def __init__(self, frames):
        from src.gui.chase_overlay import load_frame
        self.frames = frames; self.decode = load_frame
    def frame(self, index): return self.decode(self.frames[index])
    def close(self): pass


class Hub:
    def __init__(self, pack):
        from PySide6.QtCore import QObject, Signal
        class Signals(QObject):
            volume_changed = Signal(); pack_changed = Signal()
        self.signals = Signals(); self.volume_changed = self.signals.volume_changed; self.pack_changed = self.signals.pack_changed
        self.pack = pack; self.settings = default_settings(); self.users = set()
    def use(self, name, active): (self.users.add if active else self.users.discard)(name)
    def reload(self): return self.pack
    def volume(self, duck=1.): return 0. if self.settings['muted'] else self.settings['volume'] / 100 * duck


@pytest.fixture
def chase(tmp_path):
    from PySide6.QtWidgets import QApplication
    from src.gui.chase_overlay import ChaseController
    app = QApplication.instance() or QApplication([])
    frame = tmp_path / '0001.webp'; cv2.imwrite(str(frame), np.full((60, 40, 4), 200, np.uint8))
    pack = Pack(tmp_path, voices=list(VOICES), music=tmp_path / 'chase.mp3', dances=[Dance('dance-1', (frame,) * 30, 30.)])
    FakeSound.made = []; backend = Backend(); clock = Clock(); role = {'value': 'survivor'}
    def make(killer=False):
        c = ChaseController(ROOT, default_settings()['chase'], Hub(pack), lambda: killer, lambda: False, backend=backend, timer_window=Window(), dance_window=Window(),
                            clock=clock, sound=FakeSound, rng=random.Random(5), foreground=lambda: 1, loader=SyncLoader)
        c.watch.role_reader = lambda own, w, h, s: (True, role['value']); return c
    return SimpleNamespace(make=make, backend=backend, clock=clock, role=role)


def play_video(c, env, frames, tail=75, on_frame=None):
    blank = np.zeros_like(frames[0])
    for i, frame in enumerate(list(frames) + [blank] * tail):
        env.backend.image = frame; env.clock.now = 1000 + i / 30; c.step()
        for sound in FakeSound.made: sound.advance(1 / 30)
        if on_frame: on_frame(i)


@needs_video
def test_controller_runs_the_show_for_the_sample_chase_and_hides_everything_after(chase, sample, caplog):
    frames, _ = sample; c = chase.make(); c.enabled = True; seen = {}
    def note(i):
        if i in (60, 150, 300, 380, 500): seen[i] = (c.active, c.timer_window.visible, bool(c.music and c.music.players[0].playing()))
        if c.fading is not None and c.music: volumes.append(c.music.players[c.music.current].volume)
    volumes = []
    with caplog.at_level('INFO'):
        play_video(c, chase, frames[72:], tail=200, on_frame=note)   # The second clip only (the cut is at frame 69-70): HUD at 100 %, horns turn bright ~1 s in.
    assert seen[60] == (True, True, True) and seen[150] == (True, True, True) and seen[300] == (True, True, True)
    assert seen[380] == (False, True, True)                    # 1.5 s after the heart went out: the chase is over, fading out.
    assert seen[500] == (False, False, False)                  # 4 s later everything is silent and hidden.
    assert volumes and volumes[0] > volumes[len(volumes) // 2] > volumes[-1] and all(a >= b for a, b in zip(volumes, volumes[1:]))
    starts = [r.message for r in caplog.records if 'Chase meme START' in r.message]; stops = [r.message for r in caplog.records if 'Chase meme STOP' in r.message]
    assert len(starts) == 1 and 'horn animation' in starts[0] and len(stops) == 1 and 'heart gone' in stops[0]
    first, last = c.timer_window.shown[0], c.timer_window.shown[-1]
    assert first[0] < .2 and 8. < last[0] < 11.                # The timer counts the chase from 00:00.00.
    assert not c.dance_window.visible and c.detector.state == cm.IDLE


@needs_video
def test_controller_never_plays_as_killer_or_outside_a_match(chase, sample):
    frames, _ = sample; chase.role['value'] = 'killer'; c = chase.make(killer=True); c.enabled = True
    play_video(c, chase, frames[72:200], tail=0)
    assert not c.active and not c.timer_window.shown and not any(s.starts for s in FakeSound.made)
    chase.role['value'] = 'survivor'; c = chase.make(); c.enabled = True; c.automation_active = lambda: True
    play_video(c, chase, frames[72:200], tail=0); assert not c.active                      # Bloodweb automation: overlays stay away.
    c = chase.make(); c.enabled = True; c.foreground = lambda: 99
    play_video(c, chase, frames[72:200], tail=0); assert not c.active                      # DBD not in front.


@needs_video
def test_voice_dance_and_ducking_during_a_long_chase(chase, sample):
    frames, _ = sample; c = chase.make(); c.enabled = True; c.show.dance_chance = 1.
    play_video(c, chase, frames[72:300], tail=0); assert c.active
    hold = frames[300]; chase.backend.image = hold; start = chase.clock.now; ducked = danced = False; voices = 0
    for i in range(30 * 30):                                   # 30 more seconds of the same chase frame; the heart is on it.
        chase.clock.now = start + i / 30; c.step()
        for sound in FakeSound.made: sound.advance(1 / 30)
        ducked |= c.show.ducked(chase.clock.now) and c.music.players[c.music.current].volume == pytest.approx(.3)
        danced |= c.dance_window.visible
    voices = sum(s.starts for s in c.voices.values())
    assert c.active and voices in (1, 2) and ducked and danced
    x, y, w, h = c.dance_window.shown[-1][1]
    assert x >= 1920 * .62 and x + w <= 1920 and y >= 0 and y + h <= 1080                  # Right side, clear of the centre.
    c.end('test', fade=False); assert not c.dance_window.visible and not c.timer_window.visible and not c.music.players[0].playing()


def test_preview_plays_and_stops_without_a_game(chase):
    c = chase.make(); c.preview(); assert c.active and c.preview_until is not None
    for i in range(30 * 5):
        chase.clock.now = i / 30; c.step()
    assert c.timer_window.visible and sum(s.starts for s in c.voices.values()) == 1
    chase.clock.now = 20.; c.step(); assert not c.active and c.fading is not None and c.show.dance_chance == cm.DANCE_CHANCE
    chase.clock.now = 25.; c.step(); assert c.fading is None and not c.timer_window.visible


def test_timer_sits_top_right_and_the_dancer_never_covers_the_centre():
    from src.gui.chase_overlay import dance_box, timer_box
    for width, height in ((1920, 1080), (2560, 1440), (1280, 720), (3440, 1440)):
        x, y, w, h = timer_box(width, height); assert x + w <= width and x > width * .7 and y < height * .05 and h < height * .1
        bx, by, bw, bh = timer_box(width, height, 2.); assert bw == pytest.approx(w * 2, abs=2) and bx + bw <= width
        for dance_x in (50, 86, 97):
            for size in (15, 34, 60):
                for aspect in (.7, 1.25, 2.2):
                    dx, dy, dw, dh = dance_box(width, height, dict(dance_x=dance_x, dance_y=52, dance_size=size), aspect)
                    assert dx >= width * .62 - 1 and dx + dw <= width and dy >= 0 and dy + dh <= height and dw > 0 and dh > 0


def test_frame_loader_decodes_ahead_and_returns_the_newest_ready_frame():
    import time
    from src.gui.chase_overlay import FrameLoader
    loader = FrameLoader(list(range(100)), decode=lambda value: value)
    deadline = time.monotonic() + 2
    while loader.frame(0) is None and time.monotonic() < deadline: time.sleep(.005)
    assert loader.frame(0) == 0
    while loader.frame(5) != 5 and time.monotonic() < deadline: time.sleep(.005)
    assert loader.frame(5) == 5 and max(loader.ready) <= 5 + FrameLoader.AHEAD + 1       # Bounded: never the whole clip in memory.
    loader.frame(60); time.sleep(.2); assert loader.frame(60) == 60 and min(loader.ready) >= 58
    loader.close(); loader.thread.join(1); assert not loader.thread.is_alive()


# --- H5: fade-out and the own portrait ------------------------------------------------------------
def test_fade_curve_is_smooth_and_ends_at_the_configured_time():
    assert cm.fade_gain(0, 4.) == 1. and cm.fade_gain(4., 4.) == 0. and cm.fade_gain(9., 4.) == 0.
    assert cm.fade_gain(2., 4.) == pytest.approx(.5) and cm.fade_gain(1., 4.) > .8 and cm.fade_gain(3., 4.) < .2
    samples = [cm.fade_gain(t / 10, 3.) for t in range(31)]
    assert all(a >= b for a, b in zip(samples, samples[1:])) and max(a - b for a, b in zip(samples, samples[1:])) < .06   # No step anywhere.
    from src.core.memes import clean_settings, default_settings
    assert default_settings()['chase']['fade'] == 40 and clean_settings(dict(chase=dict(fade=99)))['chase']['fade'] == 40
    assert clean_settings(dict(chase=dict(fade=30)))['chase']['fade'] == 30


def start_chase(c, chase):
    c.enabled = True; c.watch.client = SimpleNamespace(x=0, y=0, width=1920, height=1080); c.watch.game_hwnd = 1
    chase.clock.now = 50.; c.begin(50., 'test'); return c


def test_the_end_fades_every_sound_and_both_windows_then_stops(chase):
    c = start_chase(chase.make(), chase); c.settings['fade'] = 30
    voice = c.voices['man3']; voice.play(); c.start_dance(c.hub.pack.dances[0], 50.)
    chase.clock.now = 60.; c.play(60.); c.end('heart gone')
    assert c.fading is not None and not c.active and c.music.players[0].playing()
    levels = []
    for i in range(1, 40):
        chase.clock.now = 60. + i / 10; c.fade_step(chase.clock.now)
        if c.fading is None: break
        levels.append((round(c.music.players[c.music.current].volume, 3), round(voice.volume, 3), c.timer_window.shown[-1][2]))
    assert c.fading is None and chase.clock.now == pytest.approx(63., abs=.11)        # 3 s, as configured.
    assert levels[0][0] > levels[-1][0] and levels[0][1] > levels[-1][1] and levels[0][2] > levels[-1][2]
    assert all(m == v for m, v, _ in levels)                                           # Music and voice fall together.
    assert not c.timer_window.visible and not c.dance_window.visible and not voice.playing() and not c.music.players[0].playing()


def test_a_new_chase_during_the_fade_starts_from_the_beginning(chase):
    c = start_chase(chase.make(), chase)
    chase.clock.now = 70.; c.play(70.); c.end('heart gone'); chase.clock.now = 71.; c.fade_step(71.)
    assert c.fading is not None
    c.begin(71.5, 'animated horns again')
    assert c.fading is None and c.active and c.chase_at == 71.5
    assert c.music.players[0].playing() and c.music.players[0].position() == 0. and c.music.loops == 0
    chase.clock.now = 71.6; c.play(71.6); assert c.timer_window.shown[-1][0] == pytest.approx(.1)   # 00:00.10, not 00:21.


def test_switching_off_stops_at_once(chase):
    c = start_chase(chase.make(), chase); c.end('switched off', fade=False)
    assert c.fading is None and not c.timer_window.visible and not c.music.players[0].playing()


def test_down_watch_needs_two_reads_and_names_the_state():
    w = cm.DownWatch()
    assert w.feed('other') is None and w.feed('dying') is None and w.feed('dying') == 'dying'
    w.reset(); assert w.feed('carried') is None and w.feed('other') is None and w.feed('carried') is None and w.feed('carried') == 'carried'
    w.reset(); assert w.feed('dying') is None and w.feed('carried') is None and w.feed('carried') == 'carried'


@needs_hud
@pytest.mark.parametrize('name, state', [('03_killerpov_kate_dying', 'dying'), ('04_killerpov_kate_carried', 'carried')])
def test_my_portrait_going_down_ends_the_chase(chase, name, state, caplog):
    """Killer-POV samples: Kate is in slot 4; her row is copied into the top slot so it is 'my' portrait."""
    def mine(image):
        image = image.copy(); image[454 - 46:454 + 46, :600] = image[719 - 46:719 + 46, :600]; return image
    healthy = mine(cv2.imread(str(next(HUD.glob('02_killerpov_kate_injured_chase*.png')))))
    down = mine(cv2.imread(str(next(HUD.glob(name + '*.png')))))
    c = start_chase(chase.make(), chase); c.watch.in_match = True; c.watch.hud_visible = True; c.watch.hud_seen_at = 50.; c.watch.role = 'survivor'
    c.watch.poll = lambda: setattr(c.watch, 'hud_seen_at', chase.clock.now)
    c.detector.state = cm.CHASE; c.detector.started_at = 50.; c.detector.heart_at = 1e9; c.detector.heart_ever = True
    c.detector.horns = lambda sample, now: None; c.detector.heart = lambda seen, now, area=0: None   # Only the portrait decides here.
    chase.backend.image = healthy
    for i in range(10):
        chase.clock.now = 51. + i * .35; c.step()
    assert c.active
    chase.backend.image = down
    with caplog.at_level('INFO'):
        for i in range(10):
            chase.clock.now = 55. + i * .35; c.step()
            if not c.active: break
    assert not c.active and c.fading is not None
    assert any(f'own portrait shows {state}' in r.message for r in caplog.records if 'Chase meme STOP' in r.message)


# --- J1 (14.11): false starts from a brightness pulse --------------------------------------------------
FALSE_STARTS = ROOT / 'debug/match_samples/chase_2026-11-14'


@pytest.mark.skipif(not FALSE_STARTS.is_dir(), reason='local samples')
@pytest.mark.parametrize('name', ['false_chase_start_timer_running_01', 'false_chase_start_02'])
def test_brightness_pulses_on_the_false_start_frames_never_start_a_chase(chase, name):
    frame = cv2.imread(str(FALSE_STARTS / f'{name}.png'))
    hud = (slice(380, 820), slice(0, 400))
    c = chase.make(); c.enabled = True; c.watch.role_reader = lambda own, w, h, s: (True, 'survivor')
    for i in range(30 * 8):                                      # 8 s of the portrait frame pulsing brighter and darker
        pulse = frame.copy(); gain = 1. + .45 * np.sin(i / 3.)
        pulse[hud] = np.clip(pulse[hud].astype(np.float32) * gain, 0, 255).astype(np.uint8)
        chase.backend.image = pulse; chase.clock.now = 2000 + i / 30; c.step()
    assert not c.active and not c.timer_window.shown


# --- 14.11 evening: rebalanced start rules -------------------------------------------------------------
def run_rules(frames, layouts, heart=True):
    d = cm.ChaseDetector(); reader = cm.HornReader(); events = []
    for i, frame in enumerate(frames):
        now = i / 30; layout = layouts[0 if i < 69 else 1]; event = None
        if i % 3 == 0:
            x, y, w, h = cm.portrait_rect(layout, 1920, 1080); event = d.horns(reader.read(frame[y:y + h, x:x + w]), now)
        if event is None and heart and i % 6 == 0:
            x, y, w, h = cm.heart_rect(1920, 1080); blob = cm.heart_blob(frame[y:y + h, x:x + w])
            event = d.heart(cm.heart_ok(blob, 1080), now, blob[0] if blob else 0)
        if event: events.append((now, *event))
    return events


@needs_video
def test_strong_horn_animation_starts_without_the_heart_within_a_second_or_so(sample):
    """The heart is often hidden by the camera, grass or a wall: sustained horn animation must be enough."""
    frames, layouts = sample
    starts = [e for e in run_rules(frames, layouts, heart=False) if e[1] == 'start']
    assert starts and 3.0 <= starts[0][0] <= 4.5 and 'strong horn animation' in starts[0][2]   # the horns brighten at ~3.0 s


@needs_video
def test_with_the_heart_the_same_chase_starts_earlier(sample):
    frames, layouts = sample
    starts = [e for e in run_rules(frames, layouts) if e[1] == 'start']
    assert starts and 3.0 <= starts[0][0] <= 3.7


@pytest.mark.skipif(not (FALSE_STARTS / 'false_chase_killer_power_on_portrait_01.png').is_file(), reason='local samples')
def test_a_killer_power_burst_on_my_portrait_with_the_heart_lit_is_not_a_chase():
    """14.11: a killer power put a bright spiky animated burst over my portrait while the terror-radius heart was lit."""
    from src.core.match_reader import load_layout
    layout = load_layout(ROOT); a, b = (cv2.imread(str(FALSE_STARTS / f'false_chase_killer_power_on_portrait_0{n}.png')) for n in (1, 2))
    d = cm.ChaseDetector(); reader = cm.HornReader(); events = []
    for i in range(300):                                          # 30 s of the burst flickering between its two looks
        image = a if (i // 2) % 2 == 0 else b; now = i / 10
        x, y, w, h = cm.portrait_rect(layout, 1920, 1080); event = d.horns(reader.read(image[y:y + h, x:x + w]), now)
        if event is None and i % 2 == 0:
            x, y, w, h = cm.heart_rect(1920, 1080); blob = cm.heart_blob(image[y:y + h, x:x + w])
            assert cm.heart_ok(blob, 1080)                        # the heart really is lit on these frames
            event = d.heart(True, now, blob[0])
        if event: events.append(event[0])
    assert 'start' not in events


def test_the_heart_check_rejects_huge_or_ragged_red_blobs():
    assert cm.heart_ok((1800, .6, .5, .6), 1080) and not cm.heart_ok((15025, .6, .5, .6), 1080)   # 14.11: a 15025 px "heart"
    assert not cm.heart_ok((1800, .15, .5, .6), 1080) and not cm.heart_ok((1800, .6, .02, .6), 1080) and not cm.heart_ok((200, .6, .5, .6), 1080)


def test_a_down_portrait_never_starts_a_chase():
    d = cm.ChaseDetector(); d.down = True; strong = cm.HornSample(80, 80, True, True, .5, .06)
    events = [d.horns(strong, i * .1) for i in range(30)]
    assert d.state == cm.IDLE and any(e and e[0] == 'rejected' and 'down state' in e[1] for e in events)
