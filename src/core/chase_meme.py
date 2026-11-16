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
# --- K2 (15.11): my horns by shape and place --------------------------------------------------
# A match (2026-11-15 13:49-13:59) had 9 chase starts without the heart, none of them a chase of mine. In every
# one the teammate in slot 2 (the Obsession) had their big bracket horns animating: their top tips lie ~1.5 r
# below MY portrait's centre, inside my old horn sectors, and bright backgrounds (a lit generator, pale sand)
# filled the sectors too. My own chase horns are small white sickles from 0.1 to 0.95 r below my centre, |dx|
# 1.0-1.6 r (measured on the 2026-11-12 video). So: nothing below HORN_CUT is read (the midline to slot 2 is
# 1.24 r), and a start without the heart needs a left AND a right horn matching the template of my horns at
# their place, mirror-symmetric, changing shape in place.
HORN_CUT = 1.15               # radii below my centre: no pixel further down belongs to me
SHAPE_SIZE = 144              # the portrait region is normalised to this size (radius 36 px, as at 1080p 100 %)
SHAPE_BOX = (.85, 1.62, -.15, 1.10)   # |dx| from-to, dy from-to (radii) of one horn; the nickname starts at 1.44 r, low
SHAPE_SEARCH = .25            # radii the template may slide around its place
SHAPE_MIN = .40               # normalised correlation of each horn (real 0.44-0.79, the false starts' best spots < 0.4 or off place)
SHAPE_OFFSET = .15            # the best match must sit this close to the horn's place (radii)
SHAPE_SYMMETRY = .2           # left and right offsets mirror each other within this (radii)
SHAPE_MOVING = .15            # share of the matched horn shapes that changed since the previous read
SIDES = ('left', 'right')
TEMPLATE_FILES = dict(left='assets/ui/hud/chase_horn_left.png', right='assets/ui/hud/chase_horn_right.png')
MATE_BANDS = ((-1.25, -.8), (.85, 1.4))   # a teammate's bracket horns hug their portrait at about +-1 r (radii)
MATE_ROWS = 1.45              # ...from this far above to this far below its centre
MATE_COVER = .35              # share of those rows with a white pixel, on both sides (brackets 0.35-0.99, most backgrounds < 0.35)
MATE_MOVING = .2              # share of the band pixels changed between two reads (animated brackets 0.27-0.6)
MATE_SECONDS = 1.5            # a teammate horn animation this recent makes the heart necessary
DEFAULT_ROOT = __import__('pathlib').Path(__file__).resolve().parents[2]


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
    ring = (d > SECTOR_RING[0]) & (d < SECTOR_RING[1]) & (dy < HORN_CUT)   # K2: never into slot 2's portrait
    return ring & np.logical_or.reduce([(angle > a) & (angle < b) for a, b in HORN_SECTORS])


def normalise_region(region):
    """portrait_rect pixels at SHAPE_SIZE x SHAPE_SIZE (any HUD scale), BGR."""
    return cv2.resize(np.ascontiguousarray(region[..., :3]), (SHAPE_SIZE, SHAPE_SIZE), interpolation=cv2.INTER_AREA)


def horn_whiteness(region):
    """0-1 per pixel: white and brighter than its surroundings (J1: a brightness change of the whole area does not
    count), not coloured. Rows below HORN_CUT are zero. `region` is a normalised portrait region."""
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV); value = hsv[..., 2].astype(np.float32); r = SHAPE_SIZE / (2 * PORTRAIT_REACH)
    local = cv2.blur(value, (int(r * 1.2) | 1,) * 2)
    white = np.clip((value - local - 10) / 40, 0, 1) * (hsv[..., 1] < 90) * (value > 110)
    white[int(round((SHAPE_SIZE - 1) / 2 + HORN_CUT * r)):] = 0
    return white.astype(np.float32)


def shape_box(side, margin=0.):
    """(x0, x1, y0, y1) of one horn's box in the normalised region, grown by `margin` radii."""
    r = SHAPE_SIZE / (2 * PORTRAIT_REACH); c = (SHAPE_SIZE - 1) / 2; a, b, top, bottom = SHAPE_BOX
    dx0, dx1 = (-b, -a) if side == 'left' else (a, b)
    x0, x1 = round(c + (dx0 - margin) * r), round(c + (dx1 + margin) * r)
    y0, y1 = round(c + (top - margin) * r), round(c + (bottom + margin) * r)
    return max(0, x0), min(SHAPE_SIZE, x1), max(0, y0), min(SHAPE_SIZE, y1)


@functools.lru_cache(maxsize=4)
def load_horn_templates(root):
    """{'left', 'right'}: float 0-1 templates of my horns, or None when the files are missing."""
    found = {}
    for side, rel in TEMPLATE_FILES.items():
        path = __import__('pathlib').Path(root) / rel
        image = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_GRAYSCALE) if path.is_file() else None
        if image is None: return None
        found[side] = image.astype(np.float32) / 255
    return found


def match_horns(white, templates):
    """Per side: (score, dx offset, dy offset in radii, patch) of the best template match near the horn's place."""
    r = SHAPE_SIZE / (2 * PORTRAIT_REACH); out = {}
    for side in SIDES:
        template = templates[side]; x0, _, y0, _ = shape_box(side); sx0, sx1, sy0, sy1 = shape_box(side, SHAPE_SEARCH)
        window = white[sy0:sy1, sx0:sx1]
        if window.shape[0] < template.shape[0] or window.shape[1] < template.shape[1] or not window.any():
            out[side] = (0., 0., 0., None); continue
        result = cv2.matchTemplate(window, template, cv2.TM_CCOEFF_NORMED); _, best, _, (bx, by) = cv2.minMaxLoc(result)
        px, py = sx0 + bx, sy0 + by; h, w = template.shape
        out[side] = (float(best) if np.isfinite(best) else 0., (px - x0) / r, (py - y0) / r, white[py:py + h, px:px + w].copy())
    return out


@dataclass(frozen=True)
class HornSample:
    left: int = 0
    right: int = 0
    present: bool = False     # horn-sized white shapes in the horn sectors (weak level)
    moving: bool = False      # ...and they changed since the previous read
    change: float = 0.
    share: float = 0.         # white share of the horn sectors
    left_score: float = 0.    # K2: template correlation of my left / right horn
    right_score: float = 0.
    offset: tuple = (0., 0., 0., 0.)   # where they matched: left dx, dy, right dx, dy (radii from their place)
    pair: bool = False        # both horns by shape, at their place, mirror-symmetric
    pair_moving: bool = False # ...and their shape changed in place since the previous read
    pair_change: float = 0.

    @property
    def strong(self):
        return self.share >= HORN_STRONG

    def shape_text(self):
        return (f'shape L {self.left_score:.2f} R {self.right_score:.2f} at ({self.offset[0]:+.2f},{self.offset[1]:+.2f})/'
                f'({self.offset[2]:+.2f},{self.offset[3]:+.2f}) r, pair {"yes" if self.pair else "no"}, change {self.pair_change:.0%}')


def pair_found(match):
    (ls, ldx, ldy, _), (rs, rdx, rdy, _) = match['left'], match['right']
    return (ls >= SHAPE_MIN and rs >= SHAPE_MIN and max(abs(ldx), abs(ldy), abs(rdx), abs(rdy)) <= SHAPE_OFFSET
            and abs(ldx + rdx) <= SHAPE_SYMMETRY and abs(ldy - rdy) <= SHAPE_SYMMETRY)


class HornReader:
    """Turns the pixels of portrait_rect into a HornSample; keeps the previous mask to see motion."""
    def __init__(self, root=None):
        self.previous = None; self.previous_pair = None
        self.templates = load_horn_templates(str(root or DEFAULT_ROOT))

    def reset(self):
        self.previous = None; self.previous_pair = None

    def read_shape(self, region):
        """K2: (scores, offsets, pair, pair moving, change) of my horns by template."""
        if self.templates is None: self.previous_pair = None; return dict()
        match = match_horns(horn_whiteness(normalise_region(region)), self.templates)
        pair = pair_found(match); change = 0.
        patches = tuple(match[s][3] for s in SIDES)
        if pair and self.previous_pair is not None and all(a is not None and b is not None and a.shape == b.shape for a, b in zip(patches, self.previous_pair)):
            now = np.concatenate([p.ravel() for p in patches]) > .5; before = np.concatenate([p.ravel() for p in self.previous_pair]) > .5
            change = float((now ^ before).sum()) / max(1, (now | before).sum())
        self.previous_pair = patches if pair else None
        return dict(left_score=round(match['left'][0], 3), right_score=round(match['right'][0], 3),
                    offset=tuple(round(v, 2) for v in (match['left'][1], match['left'][2], match['right'][1], match['right'][2])),
                    pair=pair, pair_moving=pair and change >= SHAPE_MOVING, pair_change=round(change, 3))

    def read(self, region):
        if region is None or region.size == 0 or min(region.shape[:2]) < 8: self.reset(); return HornSample()
        shape = self.read_shape(region)
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
        return HornSample(left, right, present, present and change >= HORN_MOVING, round(change, 3), round(share, 4), **shape)


class MateHorns:
    """K2: whether a teammate's horns animate (they are being chased). Read from the HUD column frame the chase
    meme already grabs for the down states, so no capture is added. A teammate's static Obsession brackets are
    present but not moving; animated ones make the heart necessary for a start (a teammate chase explains them)."""
    def __init__(self):
        self.previous = {}; self.seen_at = {}

    def reset(self):
        self.previous = {}; self.seen_at = {}

    @staticmethod
    def mask(image, cx, cy, r):
        """Both side bands of one teammate portrait: (white mask, coverage left, coverage right) or None."""
        y0, y1 = int(cy - MATE_ROWS * r), int(cy + MATE_ROWS * r)
        x0, x1 = int(cx + MATE_BANDS[0][0] * r), int(cx + MATE_BANDS[1][1] * r)
        if y0 < 0 or x0 < 0 or y1 > image.shape[0] or x1 > image.shape[1]: return None
        area = np.ascontiguousarray(image[y0:y1, x0:x1, :3]); hsv = cv2.cvtColor(area, cv2.COLOR_BGR2HSV); value = hsv[..., 2].astype(np.float32)
        local = cv2.blur(value, (int(r * 1.2) | 1,) * 2); white = (value - local > 20) & (hsv[..., 1] < 90) & (value > 110)
        bands = [white[:, int((a - MATE_BANDS[0][0]) * r):int((b - MATE_BANDS[0][0]) * r)] for a, b in MATE_BANDS]
        return np.concatenate(bands, axis=1), float(bands[0].any(axis=1).mean()), float(bands[1].any(axis=1).mean())

    def read(self, image, centres, r, now):
        """centres: {slot: (x, y)} of the teammate portraits inside `image`. Returns {slot: True} for animated horns."""
        flags = {}
        for slot, (cx, cy) in centres.items():
            found = self.mask(image, cx, cy, r)
            if found is None: self.previous.pop(slot, None); continue
            mask, left, right = found; present = min(left, right) >= MATE_COVER; before = self.previous.get(slot)
            if present and before is not None and before.shape == mask.shape:
                change = float((mask ^ before).sum()) / max(1, (mask | before).sum())
                if change >= MATE_MOVING: self.seen_at[slot] = now
            self.previous[slot] = mask if present else None
            flags[slot] = slot in self.seen_at and now - self.seen_at[slot] <= MATE_SECONDS
        return flags

    def active(self, now):
        return sorted(slot for slot, t in self.seen_at.items() if now - t <= MATE_SECONDS)


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
        if not hasattr(self, 'mates'): self.mates = []
        self.last_rejection = None; self.last_evidence = None

    def teammates(self, slots):
        """K2: HUD slots (1-3) whose horns animate right now (MateHorns.active)."""
        self.mates = list(slots)

    def horns(self, sample, now):
        self.reads = [r for r in self.reads if now - r[0] <= STRONG_WINDOW] + [(now, sample)]
        self.moving = [t for t in self.moving if now - t <= START_WINDOW]
        if sample.moving or sample.pair_moving: self.last_sample = sample
        if sample.moving:
            self.moving.append(now); self.horns_at = now
        if sample.pair_moving: self.horns_at = now
        if self.state == IDLE and (sample.moving or sample.pair_moving):
            return self.try_start(now)
        return self.check(now)

    def evidence(self, now):
        reads = [s for t, s in self.reads if now - t <= STRONG_WINDOW]
        # K2: without the heart only my horns by shape count: both present on most reads, changing in place.
        pairs = sum(s.pair for s in reads); pair_moving = sum(s.pair_moving for s in reads)
        strong = len(reads) >= 6 and pairs >= STRONG_PRESENT * len(reads) and pair_moving >= STRONG_MOVING * len(reads)
        hearts = [seen for t, seen, _ in self.hearts[-3:] if now - t <= HEART_CONFIRM]
        heart = sum(hearts) >= HEART_READS
        weak = len(self.moving) >= START_READS
        sample = self.last_sample or (reads[-1] if reads else HornSample())
        return dict(strong=strong, weak=weak, heart=heart, reads=len(reads), strong_reads=sum(s.strong for s in reads),
                    moving_reads=sum(s.moving for s in reads), share=sample.share, heart_area=self.last_heart_area,
                    pair_reads=pairs, pair_moving_reads=pair_moving, left_score=sample.left_score, right_score=sample.right_score,
                    offset=list(sample.offset), pair_change=sample.pair_change, teammate_horns=[s + 1 for s in self.mates])

    def try_start(self, now):
        e = self.evidence(now); self.last_evidence = e
        if self.down: return self.reject(now, 'own portrait shows a down state', e)
        if not e['weak'] and not e['strong']: return None        # too little to judge yet: not a refusal
        if e['strong'] and e['teammate_horns'] and not e['heart']:
            return self.reject(now, f"my horns by shape, but teammate horns animate too (slot {', '.join(map(str, e['teammate_horns']))}) and no heart", e)
        if not (e['strong'] or e['heart']):
            return self.reject(now, 'horn animation without my horn shape and without the heart', e)
        sample = self.last_sample or HornSample()
        self.state = CHASE; self.started_at = (self.moving[0] if self.moving else now); self.heart_at = self.last_heart; self.heart_ever = e['heart']
        how = 'my horns by shape' if e['strong'] and not e['heart'] else 'horn animation confirmed by the heart'
        return 'start', (f"{how}: {e['moving_reads']}/{e['reads']} reads moving, {e['strong_reads']} strong (share {sample.share:.3f}, "
                         f"change {sample.change:.0%}); {self.shape_text(e)}; heart {'seen' if e['heart'] else 'not seen'} ({self.last_heart_area} px)")

    @staticmethod
    def shape_text(e):
        o = e['offset']
        return (f"shape L {e['left_score']:.2f} R {e['right_score']:.2f} at ({o[0]:+.2f},{o[1]:+.2f})/({o[2]:+.2f},{o[3]:+.2f}) r, "
                f"pair {e['pair_reads']}/{e['reads']}, pair moving {e['pair_moving_reads']}/{e['reads']}, "
                f"teammate horns {', '.join(f'slot {s}' for s in e['teammate_horns']) or 'none'}")

    def reject(self, now, why, evidence):
        self.rejected += 1
        if self.last_rejection is None or now - self.last_rejection[0] >= 3. or self.last_rejection[1] != why:
            self.last_rejection = (now, why)
            return 'rejected', (f"{why}: {evidence['moving_reads']}/{evidence['reads']} reads moving, {evidence['strong_reads']} strong, "
                                f"share {evidence['share']:.3f}, {self.shape_text(evidence)}, heart {'seen' if evidence['heart'] else 'not seen'} ({evidence['heart_area']} px)")
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
