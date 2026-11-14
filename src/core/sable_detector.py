"""Sable Detector (block G, experimental): find Sable Ward on screen and describe the alert.

Pipeline, screen capture only:
  1. HUD gate (SableGate): the detector runs only while one of the HUD portraits is Sable's.
  2. People (PersonDetector): YOLOX-tiny through OpenCV DNN. YOLOX is Apache-2.0 (Megvii); no
     AGPL code or weights are used. The model file is downloaded on first use (sable_model.py).
  3. Identity (Library): every person box is split into head / torso / legs and each part is
     compared on its own with all the Sable outfit pieces of that kind, because outfits can be
     mixed. Score = 0.5 head + 0.3 torso + 0.2 legs.
  4. Alert: several consecutive hits start it; about 1 s without her ends it; 3 s cooldown.

Also here: the hand-drawn circle's shape and the placement of the photo. Pure logic with explicit
time; the windows and threads are in src/gui/sable_overlay.py.
"""
import math
import random
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from src.core.match_reader import portrait_similarity

PART_WEIGHTS = dict(head=.5, torso=.3, legs=.2)
PART_SPANS = dict(head=(0., .20), torso=(.20, .55), legs=(.55, 1.))   # shares of the box height
HITS_TO_START = 3            # consecutive detections above the threshold
GONE_SECONDS = .8            # counted from the first detection pass that misses her; with the passes skipped while the circle is lit this hides the alert about 1 s after she leaves
STALL_SECONDS = 2.5          # no detection results at all for this long also ends the alert
COOLDOWN_SECONDS = 3.
BLINK_PERIOD = 1.            # 0.5 s on, 0.5 s off
GATE_SCORE = .65             # HUD portrait vs a Sable HUD template: Sable 0.76-0.98 (Obsession and killer POV included), others at most 0.50 (14.11)
GATE_READS = 2
MIN_PERSON_HEIGHT = .06      # of the client height: smaller boxes carry no usable colour
IDLE, ALERT, COOLDOWN = 'idle', 'alert', 'cooldown'


# --- hand-drawn circle ------------------------------------------------------------------------
def hand_circle(seed, steps=96):
    """Outline polygon of a rough hand-drawn loop in unit coordinates (the ellipse spans about -1..1).

    One stroke: it starts thin at the top, runs round the ellipse with a wobble and slowly drifts
    outwards, so its end overshoots the start as a second, thinner arc above it - the tail. The
    thickness swells in the middle of the stroke. The same seed always gives the same shape."""
    rng = random.Random(seed)
    start = math.radians(rng.uniform(55, 80)); sweep = 2 * math.pi + math.radians(rng.uniform(40, 75)); direction = -1   # screen coords: counter-clockwise on screen
    wobble = [(rng.uniform(.015, .04), rng.uniform(0, 2 * math.pi), k) for k in (1, 2, 3)]
    drift = rng.uniform(.14, .22); squash = rng.uniform(.9, 1.); tilt = math.radians(rng.uniform(-9, 9)); thick = rng.uniform(.09, .125)
    centre = []; widths = []
    for i in range(steps + 1):
        t = i / steps; angle = start + direction * sweep * t
        radius = 1 - drift / 2 + drift * t + sum(a * math.sin(k * angle + p) for a, p, k in wobble)
        x, y = radius * math.cos(angle), -radius * squash * math.sin(angle)
        centre.append((x * math.cos(tilt) - y * math.sin(tilt), x * math.sin(tilt) + y * math.cos(tilt)))
        widths.append(thick * (.12 + .88 * math.sin(math.pi * min(1., t * 1.08)) ** .7) * (1 + .12 * math.sin(7 * t + wobble[0][1])))
    left, right = [], []
    for i, (x, y) in enumerate(centre):
        ax, ay = centre[max(0, i - 1)]; bx, by = centre[min(steps, i + 1)]; dx, dy = bx - ax, by - ay; n = math.hypot(dx, dy) or 1.
        nx, ny = -dy / n, dx / n; half = widths[i] / 2
        left.append((x + nx * half, y + ny * half)); right.append((x - nx * half, y - ny * half))
    return left + right[::-1], centre, widths


def blink_on(elapsed):
    return (elapsed % BLINK_PERIOD) < BLINK_PERIOD / 2


def circle_box(box, pad=.18):
    """The ellipse around a person box (x, y, w, h): a little larger, never thinner than 60 % of its height."""
    x, y, w, h = box; cx, cy = x + w / 2, y + h / 2; rw = max(w, h * .6) * (1 + pad) / 2 * 1.15; rh = h * (1 + pad) / 2
    return cx, cy, rw, rh


def photo_box(client_width, client_height, box, size, aspect):
    """(x, y, w, h) of the photo beside the person box: on the side with more room, clear of the
    screen centre, of the HUD column on the left and of the perks at the bottom right."""
    h = round(client_height * size / 100); w = round(h * aspect); x, y, bw, bh = box
    margin = round(client_height * .03); left_limit = round(client_width * .19); bottom = round(client_height * .70)
    centre = (client_width * .40, client_width * .60)
    def fits(px):
        return px >= left_limit and px + w <= client_width - margin and (px + w <= centre[0] or px >= centre[1])
    right, left = round(x + bw * 1.2 + margin), round(x - bw * .2 - margin - w)
    options = [right, left] if client_width - (x + bw) >= x - left_limit else [left, right]
    px = next((p for p in options if fits(p)), None)
    if px is None:   # No free side next to her: the nearer free band beside the centre.
        px = round(centre[1]) if x + bw / 2 < client_width / 2 else max(left_limit, round(centre[0] - w))
        if not fits(px): px = min(max(left_limit, round(centre[1])), client_width - margin - w)
    py = min(max(margin, round(y + bh / 2 - h / 2)), max(margin, bottom - h))
    return px, py, w, h


class SmoothBox:
    """Follows the detections without jitter: an exponential average of centre and size."""
    def __init__(self, weight=.45):
        self.weight = weight; self.box = None

    def reset(self):
        self.box = None

    def update(self, box):
        if self.box is None: self.box = tuple(float(v) for v in box)
        else: self.box = tuple(a + self.weight * (b - a) for a, b in zip(self.box, box))
        return self.box


# --- alert state machine ----------------------------------------------------------------------
class Alert:
    """feed(box or None, now) -> 'show', 'hide' or None."""
    def __init__(self, hits=HITS_TO_START, gone=GONE_SECONDS, cooldown=COOLDOWN_SECONDS):
        self.hits_needed = hits; self.gone = gone; self.cooldown = cooldown; self.reset()

    def reset(self):
        self.state = IDLE; self.hits = 0; self.seen_at = None; self.missing_since = None; self.shown_at = None; self.until = None; self.box = None

    def feed(self, box, now):
        if self.state == COOLDOWN:
            if now < self.until: return None
            self.state = IDLE; self.hits = 0
        if self.state == IDLE:
            self.hits = self.hits + 1 if box is not None else 0
            if self.hits >= self.hits_needed:
                self.state = ALERT; self.seen_at = self.shown_at = now; self.missing_since = None; self.box = box; return 'show'
            return None
        if box is not None: self.seen_at = now; self.missing_since = None; self.box = box; return None
        if self.missing_since is None: self.missing_since = now
        return self.check(now)

    def check(self, now):
        """Call on every tick: the alert ends `gone` seconds after the first miss, or when results stop coming."""
        if self.state == ALERT and ((self.missing_since is not None and now - self.missing_since >= self.gone) or now - self.seen_at >= self.gone + STALL_SECONDS):
            self.state = COOLDOWN; self.until = now + self.cooldown; self.hits = 0; self.box = None; return 'hide'
        return None

    def stop(self):
        was = self.state == ALERT; self.reset(); return 'hide' if was else None


# --- HUD gate ---------------------------------------------------------------------------------
def load_gate_templates(root):
    """Sable's HUD portrait crops (assets/ui/hud_portraits/sable_ward*.png), as portrait features."""
    out = []
    for path in sorted((Path(root) / 'assets/ui/hud_portraits').glob('sable_ward*.png')):
        image = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
        if image is None: continue
        image = cv2.resize(image, (96, 96), interpolation=cv2.INTER_AREA); yy, xx = np.ogrid[:96, :96]
        out.append((cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32), ((xx - 48) ** 2 + (yy - 48) ** 2) <= (48 * .72) ** 2))
    return out


class SableGate:
    """Latches for the match once a HUD portrait matched Sable on GATE_READS reads; cleared when the match ends.

    A portrait is hidden by status icons for long stretches, so 'not seen now' never closes the gate."""
    def __init__(self, templates, score=GATE_SCORE, reads=GATE_READS):
        self.templates = list(templates); self.score = score; self.reads = reads; self.reset()

    def reset(self):
        self.open = False; self.slot = None; self.best = 0.; self.streak = 0

    def feed(self, portraits, role):
        """portraits: hud_portraits() features, top slot first. As a survivor the top slot is the player and does not count."""
        if self.open or not self.templates: return self.open
        slots = range(1, len(portraits)) if role == 'survivor' else range(len(portraits))
        best, where = 0., None
        for slot in slots:
            value = max((portrait_similarity(portraits[slot], t) for t in self.templates), default=0.)
            if value > best: best, where = value, slot
        self.best = best
        self.streak = self.streak + 1 if best >= self.score else 0
        if self.streak >= self.reads: self.open = True; self.slot = where
        return self.open


# --- identity: outfit pieces ------------------------------------------------------------------
HIST = 12 * 4 + 4            # hue x saturation bins for coloured pixels, 4 grey levels for the rest
GRID = 4                     # colour layout: a 4x4 grid of mean Lab values
W_HIST, W_GRID = .6, .4


def piece_feature(crop):
    """A part crop -> unit vector whose dot product with another one is 0.6 * histogram overlap + 0.4 * layout cosine."""
    if crop is None or crop.size == 0 or min(crop.shape[:2]) < 4: return None
    crop = cv2.resize(crop[..., :3], (32, 48), interpolation=cv2.INTER_AREA); hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0].ravel().astype(int), hsv[..., 1].ravel().astype(int), hsv[..., 2].ravel().astype(int)
    grey = (sat < 40) | (val < 40); hist = np.zeros(HIST, np.float32)
    np.add.at(hist, (hue[~grey] * 12 // 180) * 4 + np.minimum(3, (sat[~grey] - 40) * 4 // 216), 1.)
    np.add.at(hist, 48 + np.minimum(3, val[grey] * 4 // 256), 1.)
    hist = np.sqrt(hist / max(1., hist.sum()))                         # Bhattacharyya: dot product of the roots
    lab = cv2.cvtColor(cv2.resize(crop, (GRID, GRID), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2LAB).astype(np.float32).ravel()
    lab = lab - lab.mean(); lab = lab / (np.linalg.norm(lab) + 1e-6)
    return np.concatenate([hist * math.sqrt(W_HIST), lab * math.sqrt(W_GRID)])


def part_crops(image, box):
    """{part: crop} for a person box (x, y, w, h); the middle 70 % of the width, where the body is."""
    x, y, w, h = [int(v) for v in box]; x0, x1 = max(0, x + round(w * .15)), min(image.shape[1], x + round(w * .85)); out = {}
    for part, (top, bottom) in PART_SPANS.items():
        out[part] = image[max(0, y + round(h * top)):min(image.shape[0], y + round(h * bottom)), x0:x1]
    return out


def person_features(image, box):
    return {part: piece_feature(crop) for part, crop in part_crops(image, box).items()}


class Library:
    """Sable outfit pieces per part; scores a person by the best matching piece of each part."""
    def __init__(self, pieces):
        self.pieces = {part: np.asarray(pieces.get(part, np.zeros((0, HIST + GRID * GRID * 3))), np.float32) for part in PART_WEIGHTS}

    @classmethod
    def load(cls, path):
        with np.load(str(path)) as data: return cls({part: data[part] for part in PART_WEIGHTS if part in data})

    def save(self, path, **extra):
        Path(path).parent.mkdir(parents=True, exist_ok=True); np.savez_compressed(str(path), **self.pieces, **extra)

    @property
    def ready(self):
        return all(len(self.pieces[part]) for part in PART_WEIGHTS)

    def part_scores(self, features):
        return {part: (float((self.pieces[part] @ features[part]).max()) if features.get(part) is not None and len(self.pieces[part]) else 0.) for part in PART_WEIGHTS}

    def score(self, features):
        """(total 0..1, per-part scores)."""
        parts = self.part_scores(features)
        return sum(PART_WEIGHTS[p] * parts[p] for p in PART_WEIGHTS), parts


# --- people -----------------------------------------------------------------------------------
class PersonDetector:
    """YOLOX person boxes through OpenCV DNN. detect() returns [((x, y, w, h), confidence)] in image pixels."""
    def __init__(self, model_path, size=416, confidence=.35):
        self.net = cv2.dnn.readNetFromONNX(str(model_path)); self.size = size; self.confidence = confidence
        grids, strides = [], []
        for stride in (8, 16, 32):
            n = size // stride; yy, xx = np.mgrid[:n, :n]
            grids.append(np.stack([xx, yy], -1).reshape(-1, 2)); strides.append(np.full((n * n, 1), stride))
        self.grid = np.concatenate(grids).astype(np.float32); self.stride = np.concatenate(strides).astype(np.float32)

    def detect(self, image):
        h, w = image.shape[:2]; ratio = min(self.size / h, self.size / w)
        canvas = np.full((self.size, self.size, 3), 114, np.uint8); small = cv2.resize(image[..., :3], (max(1, round(w * ratio)), max(1, round(h * ratio))))
        canvas[:small.shape[0], :small.shape[1]] = small
        self.net.setInput(canvas.transpose(2, 0, 1)[None].astype(np.float32)); out = self.net.forward()[0]
        score = out[:, 4] * out[:, 5]; keep = score > self.confidence          # class 0 of COCO: person
        if not keep.any(): return []
        centre = (out[keep, :2] + self.grid[keep]) * self.stride[keep]; size = np.exp(out[keep, 2:4]) * self.stride[keep]
        boxes = np.concatenate([centre - size / 2, size], 1) / ratio; scores = score[keep]
        picked = cv2.dnn.NMSBoxes(boxes.tolist(), scores.tolist(), self.confidence, .45)
        return [(tuple(int(round(v)) for v in boxes[i]), float(scores[i])) for i in np.array(picked).flatten()]


def overlap(a, b):
    """Intersection over union of two (x, y, w, h) boxes."""
    x0, y0 = max(a[0], b[0]), max(a[1], b[1]); x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    return inter / max(1e-6, a[2] * a[3] + b[2] * b[3] - inter)


def own_character(box, width, height):
    """As a survivor the player's own character fills the lower middle of the screen; it is never the Sable we look for."""
    x, y, w, h = box
    return h >= height * .3 and x <= width * .5 <= x + w and y <= height * .72 <= y + h


@dataclass(frozen=True)
class Hit:
    box: tuple
    score: float
    parts: dict
    confidence: float


def find_sable(image, people, library, threshold, role='killer', ignore=()):
    """The best-scoring person above the threshold, or None; also every candidate's Hit for the log.

    `ignore`: boxes of our own overlay (the photo), which the screen capture sees too. Only a box that
    nearly coincides with one is dropped, so she is still followed when she walks behind the photo."""
    height, width = image.shape[:2]; hits = []
    for box, confidence in people:
        if box[3] < height * MIN_PERSON_HEIGHT or any(overlap(box, other) > .6 for other in ignore): continue
        if role == 'survivor' and own_character(box, width, height): continue
        total, parts = library.score(person_features(image, box)); hits.append(Hit(box, total, parts, confidence))
    best = max(hits, key=lambda hit: hit.score, default=None)
    return (best if best is not None and best.score >= threshold else None), hits
