"""Chase meme (block F3): chase detection for the player's own survivor and the show that plays during it.

Detection reads two small screen regions and nothing else:
  * the chase horns: small white horns below-left and below-right of the own (top) HUD portrait.
    They animate while the killer chases the player. The Obsession's big horns sit in the same
    place but do not move, so presence alone never starts a chase: the white shape has to change
    between reads.
  * the heart: the red-orange glow on the character's back, near the screen centre.

Start: animated horns on several reads within a second.
Stop:  neither the heart nor animated horns for STOP_SECONDS (the heart flickers behind grass),
       or the match HUD is gone.

Measured on debug/match_samples/chase_2026-11-12 (survivor POV, 1920x1080, HUD 84 % and 100 %):
horns 55-125 white px per side at 100 % with 50-130 % of them changing every 0.1 s; a static
portrait side 0-7 px. Heart 700-4700 px when lit (red near the killer, orange in a chase; up to
24000 px with the camera close), 0-40 px otherwise.

Everything is pure logic with explicit time, so whole chases run in tests without a game.
"""
import functools
import math
import random
from dataclasses import dataclass

import cv2
import numpy as np

from src.core.match_reader import Geometry

IDLE, CHASE = 'idle', 'chase'
OWN_SLOT = 0                 # The player's own portrait is the top one.
# --- horns ------------------------------------------------------------------------------------
# 14.11 (evening): the chase horns are read only where they are drawn, the sectors below-left and
# below-right of the portrait, from white pixels brighter than their own surroundings (lighting
# changes behind the portrait do not count), and coloured pixels are ignored. Measured on the
# samples (share of the two sectors): real chase horns 0.049-0.067, a killer power burst on the
# portrait 0.033, frames without a chase at most 0.029, the Obsession's static horns 0.088 (but
# they do not move).
PORTRAIT_REACH = 2.0          # the read region: the portrait centre +- this many radii
SECTOR_RING = (1.0, 1.9)      # horn sectors: between these distances (in radii) from the centre,
HORN_SECTORS = ((200, 260), (280, 340))   # at these angles (degrees, 0 = right, counter-clockwise)
HORN_STRONG = .04             # sector share of a clearly present pair of horns
HORN_WEAK = .03               # sector share that can count with the heart's confirmation
HORN_MOVING = .15             # share of the horn pixels that changed since the previous read
STRONG_WINDOW = 1.2           # seconds of reads judged for a start without the heart
STRONG_PRESENT = .7           # ...of which this share shows strong horns
STRONG_MOVING = .5            # ...and this share shows them moving
START_WINDOW = 1.0            # weak evidence: animated reads inside this window...
START_READS = 3               # ...at least this many, plus the heart
STOP_SECONDS = 1.5
HEART_CONFIRM = 2.0           # the heart must be seen this close to the animated horns
HEART_READS = 2               # ...on at least 2 of the last 3 heart reads (it flickers behind grass)
HEART_ROI = (.30, .33, .40, .52)      # x, y, w, h as fractions of the client: wherever the camera puts the character's back
HEART_MIN = 400               # px of the heart blob at 1080p (real hearts 700-6000); scales with the height squared
HEART_MAX = 9000              # bigger warm blobs are fire, blood pools or a red light, not the heart (a 15025 px "heart" started a chase on 14.11)
HEART_FILL = .3               # the heart is a compact blob: its share of its bounding box
HEART_REFERENCE = 1080


def portrait_rect(layout, width, height, slot=OWN_SLOT):
    """(x, y, w, h) inside the client: the square of +-PORTRAIT_REACH radii around one HUD portrait."""
    g = Geometry(layout, (height, width)); h = layout['hud']; r = h['portrait_radius'] * PORTRAIT_REACH
    x0, y0 = g.hud(h['portrait_x'] - r, h['portrait_y'][slot] - r); x1, y1 = g.hud(h['portrait_x'] + r, h['portrait_y'][slot] + r)
    x0 = max(0, x0); y0 = max(0, y0)
    return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


horn_rect = portrait_rect     # the old name, kept for callers


@functools.lru_cache(maxsize=8)
def _sectors(size):
    yy, xx = np.mgrid[:size, :size]; c = (size - 1) / 2; r = size / (2 * PORTRAIT_REACH)
    dx, dy = (xx - c) / r, (yy - c) / r; d = np.hypot(dx, dy); angle = np.degrees(np.arctan2(-dy, dx)) % 360
    ring = (d > SECTOR_RING[0]) & (d < SECTOR_RING[1])
    return ring & np.logical_or.reduce([(angle > a) & (angle < b) for a, b in HORN_SECTORS])


@dataclass(frozen=True)
class HornSample:
    left: int = 0
    right: int = 0
    present: bool = False     # horn-sized white shapes in the horn sectors (weak level)
    moving: bool = False      # ...and they changed since the previous read
    change: float = 0.
    share: float = 0.         # white share of the horn sectors

    @property
    def strong(self):
        return self.share >= HORN_STRONG


class HornReader:
    """Turns the pixels of portrait_rect into a HornSample; keeps the previous mask to see motion."""
    def __init__(self):
        self.previous = None

    def reset(self):
        self.previous = None

    def read(self, region):
        if region is None or region.size == 0 or min(region.shape[:2]) < 8: self.previous = None; return HornSample()
        size = min(region.shape[:2]); region = cv2.resize(region[..., :3], (size, size)) if region.shape[0] != region.shape[1] else region[..., :3]
        hsv = cv2.cvtColor(np.ascontiguousarray(region), cv2.COLOR_BGR2HSV); value = hsv[..., 2].astype(np.float32)
        r = size / (2 * PORTRAIT_REACH); local = cv2.blur(value, (max(3, int(r * 1.2) | 1),) * 2)
        white = (value - local > 25) & (hsv[..., 1] < 90) & (value > 110)   # brighter than its surroundings, not coloured
        zone = _sectors(size); mask = white & zone
        half = size // 2; left, right = int(mask[:, :half].sum()), int(mask[:, half:].sum())
        share = float(mask.sum()) / max(1, zone.sum())
        present = share >= HORN_WEAK and min(left, right) > 0
        change = 0.
        if present and self.previous is not None and self.previous.shape == mask.shape:
            change = float((mask ^ self.previous).sum()) / max(1, (mask | self.previous).sum())
        self.previous = mask if present else None
        return HornSample(left, right, present, present and change >= HORN_MOVING, round(change, 3), round(share, 4))


# --- heart ------------------------------------------------------------------------------------
def heart_rect(width, height):
    x, y, w, h = HEART_ROI
    return round(width * x), round(height * y), round(width * w), round(height * h)


def heart_blob(region):
    """(area, fill, centre x share, centre y share) of the largest warm glowing blob, or None."""
    if region is None or region.size == 0: return None
    hsv = cv2.cvtColor(np.ascontiguousarray(region[..., :3]), cv2.COLOR_BGR2HSV); hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    core = (val > 225) & (hue >= 2) & (hue <= 22) & (sat > 25) & (sat < 180)
    glow = ((hue <= 18) | (hue >= 172)) & (sat > 150) & (val > 170)
    mask = (core | glow).astype(np.uint8)
    if not mask.any(): return None
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    count, _, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count < 2: return None
    i = 1 + int(stats[1:, cv2.CC_STAT_AREA].argmax()); x, y, w, h, area = stats[i]
    return int(area), float(area) / max(1, w * h), float(centroids[i][0]) / region.shape[1], float(centroids[i][1]) / region.shape[0]


def heart_area(region):
    blob = heart_blob(region)
    return blob[0] if blob else 0


def heart_ok(blob, client_height):
    """The heart on my own character: right size for the window, compact, not at the edge of the search area."""
    if not blob: return False
    area, fill, x, y = blob; scale = (client_height / HEART_REFERENCE) ** 2
    return HEART_MIN * scale <= area <= HEART_MAX * scale and fill >= HEART_FILL and .08 <= x <= .92 and .05 <= y <= .95


def heart_seen(region, client_height):
    return heart_ok(heart_blob(region), client_height)


# --- chase state machine ----------------------------------------------------------------------
class ChaseDetector:
    """IDLE -> CHASE on horn animation; CHASE -> IDLE when all evidence has been gone for STOP_SECONDS.

    Start rules (14.11 evening): strong, sustained horn animation starts on its own; weaker animation
    needs the heart on my own character on most recent reads; the heart alone never starts a chase (it
    shows in the terror radius); my portrait showing dying/carried/hooked/dead never starts one.
    Every transition and every refused start returns its reason so the caller can log it."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = IDLE; self.started_at = None; self.moving = []; self.horns_at = None; self.heart_at = None; self.heart_ever = False
        self.reads = []; self.hearts = []; self.last_heart = None; self.last_heart_area = 0; self.last_sample = None; self.down = False
        if not hasattr(self, 'rejected'): self.rejected = 0
        self.last_rejection = None

    def horns(self, sample, now):
        self.reads = [r for r in self.reads if now - r[0] <= STRONG_WINDOW] + [(now, sample)]
        self.moving = [t for t in self.moving if now - t <= START_WINDOW]
        if sample.moving:
            self.moving.append(now); self.horns_at = now; self.last_sample = sample
        if self.state == IDLE and sample.moving:
            return self.try_start(now)
        return self.check(now)

    def evidence(self, now):
        reads = [s for t, s in self.reads if now - t <= STRONG_WINDOW]
        strong = len(reads) >= 6 and sum(s.strong for s in reads) >= STRONG_PRESENT * len(reads) and sum(s.moving for s in reads) >= STRONG_MOVING * len(reads)
        hearts = [seen for t, seen, _ in self.hearts[-3:] if now - t <= HEART_CONFIRM]
        heart = sum(hearts) >= HEART_READS
        weak = len(self.moving) >= START_READS
        return dict(strong=strong, weak=weak, heart=heart, reads=len(reads), strong_reads=sum(s.strong for s in reads),
                    moving_reads=sum(s.moving for s in reads), share=self.last_sample.share if self.last_sample else 0., heart_area=self.last_heart_area)

    def try_start(self, now):
        e = self.evidence(now)
        if self.down: return self.reject(now, 'own portrait shows a down state', e)
        if not e['weak'] and not e['strong']: return None        # too little to judge yet: not a refusal
        if not (e['strong'] or e['heart']):
            return self.reject(now, 'weak horn animation without the heart', e)
        sample = self.last_sample or HornSample()
        self.state = CHASE; self.started_at = (self.moving[0] if self.moving else now); self.heart_at = self.last_heart; self.heart_ever = e['heart']
        how = 'strong horn animation' if e['strong'] else 'horn animation confirmed by the heart'
        return 'start', (f"{how}: {e['moving_reads']}/{e['reads']} reads moving, {e['strong_reads']} strong (share {sample.share:.3f}, "
                         f"change {sample.change:.0%}); heart {'seen' if e['heart'] else 'not seen'} ({self.last_heart_area} px)")

    def reject(self, now, why, evidence):
        self.rejected += 1
        if self.last_rejection is None or now - self.last_rejection[0] >= 3. or self.last_rejection[1] != why:
            self.last_rejection = (now, why)
            return 'rejected', f"{why}: {evidence['moving_reads']}/{evidence['reads']} reads moving, {evidence['strong_reads']} strong, share {evidence['share']:.3f}, heart {'seen' if evidence['heart'] else 'not seen'} ({evidence['heart_area']} px)"
        return None

    def heart(self, seen, now, area=0):
        self.hearts = (self.hearts + [(now, bool(seen), area)])[-6:]
        if seen:
            self.last_heart = now; self.last_heart_area = area
            if self.state == CHASE: self.heart_at = now; self.heart_ever = True
        if self.state == IDLE and len(self.moving) >= START_READS and seen and now - self.moving[-1] <= HEART_CONFIRM:
            return self.try_start(now)
        return self.check(now)

    def check(self, now):
        if self.state != CHASE: return None
        last = max(t for t in (self.heart_at if self.heart_ever else None, self.horns_at, self.started_at) if t is not None)
        if now - last < STOP_SECONDS: return None
        heart_gone = self.heart_ever and self.heart_at is not None
        reason = (f'heart gone for {now - self.heart_at:.1f} s, horns not animated for {now - self.horns_at:.1f} s' if heart_gone
                  else f'horns not animated for {now - self.horns_at:.1f} s and the heart was never seen')
        return self.stop(now, reason)

    def stop(self, now, reason):
        if self.state != CHASE: return None
        seconds = now - self.started_at; self.reset()
        return 'stop', f'{reason}; chase lasted {seconds:.1f} s'

    def elapsed(self, now):
        return max(0., now - self.started_at) if self.state == CHASE else 0.


def format_timer(seconds):
    """mm:ss.cc from 00:00.00"""
    total = int(max(0., seconds) * 100); minutes, rest = divmod(total, 6000)
    return f'{min(minutes, 99):02d}:{rest // 100:02d}.{rest % 100:02d}'


# --- the show: voice lines, dances, ducking ---------------------------------------------------
LINE_INTERVAL = (15., 20.)
DANCE_CHANCE = .25
DUCK = .5                    # Music level while a voice line plays.
FALLBACK_LINE_SECONDS = 2.   # A line of unknown length ducks the music this long.


def pick_weighted(voices, last, rng):
    """One voice by weight (3 common, 2 medium, 1 rare), never the one that just played."""
    pool = [v for v in voices if v.name != last] or list(voices)
    if not pool: return None
    return rng.choices(pool, weights=[max(1, v.weight) for v in pool], k=1)[0]


class ChaseShow:
    """What plays during one chase. tick() returns the events that are due: ('voice', v), ('dance', d)."""
    def __init__(self, voices=(), dances=(), rng=None, interval=LINE_INTERVAL, dance_chance=DANCE_CHANCE):
        self.voices = list(voices); self.dances = list(dances); self.rng = rng or random.Random()
        self.interval = interval; self.dance_chance = dance_chance; self.last = None; self.active = False
        self.next_line = None; self.line_until = None; self.dance_pending = False

    def start(self, now):
        """A new chase always starts fresh: the first line comes 15-20 s in."""
        self.active = True; self.line_until = None; self.dance_pending = False
        self.next_line = now + self.rng.uniform(*self.interval)

    def stop(self):
        self.active = False; self.next_line = None; self.line_until = None; self.dance_pending = False

    def ducked(self, now):
        return self.active and self.line_until is not None and now < self.line_until

    def tick(self, now):
        if not self.active: return []
        events = []
        if self.line_until is not None and now >= self.line_until:      # The line just ended: maybe a dance.
            self.line_until = None
            if self.dance_pending:
                self.dance_pending = False
                if self.dances and self.rng.random() < self.dance_chance: events.append(('dance', self.rng.choice(self.dances)))
        if self.next_line is not None and now >= self.next_line:
            self.next_line = now + self.rng.uniform(*self.interval)
            voice = pick_weighted(self.voices, self.last, self.rng)
            if voice is not None:
                self.last = voice.name; self.line_until = now + (voice.duration or FALLBACK_LINE_SECONDS); self.dance_pending = True
                events.append(('voice', voice))
        return events


# --- fade-out -------------------------------------------------------------------------------------
FADE_SECONDS = 4.            # default; the tab offers 3-5 s


def fade_gain(elapsed, seconds=FADE_SECONDS):
    """1 -> 0 over `seconds`, smooth at both ends (a raised cosine): no audible step when it starts or stops."""
    if seconds <= 0 or elapsed >= seconds: return 0.
    if elapsed <= 0: return 1.
    return .5 * (1 + math.cos(math.pi * elapsed / seconds))


# --- own portrait: dying, carried, hooked ------------------------------------------------------
DOWN_READS = 2               # two reads in a row, like the 1v1 timer


class DownWatch:
    """The own (top) HUD portrait shows dying, carried, hooked or dead on two reads in a row."""
    def __init__(self):
        self.reads = 0; self.state = None

    def reset(self):
        self.reads = 0; self.state = None

    def feed(self, status):
        from src.core.onevone import DOWN_STATES
        if status in DOWN_STATES:
            self.reads = self.reads + 1 if status == self.state or self.state is None else 1; self.state = status
        else: self.reads = 0; self.state = None
        return self.state if self.reads >= DOWN_READS else None


# --- music loop -------------------------------------------------------------------------------
def crossfade_gains(position, duration, crossfade):
    """(gain of the ending pass, gain of the starting pass) at a position near the end of the track."""
    if duration <= 0 or crossfade <= 0 or position < duration - crossfade: return 1., 0.
    progress = min(1., max(0., (position - (duration - crossfade)) / crossfade))
    return math.cos(progress * math.pi / 2), math.sin(progress * math.pi / 2)   # Equal power: no dip in the middle.


class MusicLoop:
    """Loops one track on two players: the next pass starts under the end of the current one.

    A player needs play(), stop(), position(), duration(), playing() and set_volume(0..1)."""
    def __init__(self, first, second, crossfade=.35):
        self.players = [first, second]; self.crossfade = crossfade; self.current = 0; self.fading = False; self.running = False; self.loops = 0

    def start(self, volume):
        """Always from the beginning."""
        for player in self.players: player.stop()
        self.current = 0; self.fading = False; self.running = True; self.loops = 0
        self.players[0].set_volume(volume); self.players[0].play()

    def stop(self):
        self.running = False; self.fading = False
        for player in self.players: player.stop()

    def tick(self, volume):
        if not self.running: return
        now, nxt = self.players[self.current], self.players[1 - self.current]
        duration = now.duration(); position = now.position(); crossfade = min(self.crossfade, duration / 4) if duration > 0 else 0.
        ended = not now.playing() or (duration > 0 and position >= duration - .02)
        if not self.fading and duration > 0 and (position >= duration - crossfade or ended):
            self.fading = True; self.loops += 1; nxt.set_volume(0.); nxt.play()
        if self.fading:
            out, into = crossfade_gains(position, duration, crossfade) if not ended else (0., 1.)
            now.set_volume(volume * out); nxt.set_volume(volume * into)
            if ended or out <= 0.01:
                now.stop(); self.current = 1 - self.current; self.fading = False; nxt.set_volume(volume)
        else:
            now.set_volume(volume)


# --- timer look -------------------------------------------------------------------------------
GLITCH_PERIOD = 13 / 30      # The reference kicks once every 13 frames of 30 fps video.


@dataclass(frozen=True)
class Glitch:
    dx: float = 0.        # offsets in digit heights; negative = left / up
    dy: float = 0.
    smear: float = 0.     # 0..1 vertical motion blur
    fringe: float = 0.    # 0..1 colour fringing
    light: float = 1.     # digit brightness 0..1
    zoom: float = 1.


def timer_glitch(seconds):
    """The edit-style glitch of the reference clip, as a pure function of the chase time.

    Each 0.43 s cycle, as measured on music+timer-1.mp4: three sharp frames at rest; a kick up and
    to the left with heavy vertical smear (digits half out of the strip) for three frames; then the
    digits land dim, slightly enlarged and left of their place and drift back to the right, with a
    small overshoot, while they brighten. Every cycle varies a little."""
    cycle, phase = divmod(max(0., seconds) / GLITCH_PERIOD, 1.); wobble = random.Random(int(cycle)); k = .8 + .4 * wobble.random()
    if phase < 3 / 13:
        return Glitch(dx=.02 * (wobble.random() - .5))
    if phase < 6 / 13:
        p = (phase - 3 / 13) / (3 / 13)
        return Glitch(dx=-.28 * k, dy=-(.42 + .1 * math.sin(p * math.pi)) * k, smear=1., fringe=1., light=.85)
    p = (phase - 6 / 13) / (7 / 13); ease = 1 - (1 - p) ** 2
    dx = -.32 * k + (.32 * k + .18 * k * math.sin(p * math.pi) * (1 - p)) * ease
    return Glitch(dx=dx, dy=-.12 * k * (1 - ease), smear=max(0., .35 * (1 - 2.5 * p)), light=.5 + .5 * ease, zoom=1. + .06 * (1 - ease))
