"""Builds and evaluates the Sable Detector's outfit library (assets/sable/library.npz).

The library holds only numeric features (colour histograms and colour layouts) of head, torso
and legs crops; no images. A developer tool: it reads local recordings that are never committed.

    .venv/Scripts/python.exe scripts/build_sable_library.py                     # store video only
    .venv/Scripts/python.exe scripts/build_sable_library.py --positive a.mp4 b.mp4 --negative c.mp4

Sources:
  --store     the in-game store cycling through Sable's outfits (she stands on the right, lit, turning).
  --positive  in-match recordings of the user playing Sable: the player's own character (the large
              figure in the lower middle of the screen) is taken as Sable in every frame.
  --negative  in-match recordings without Sable: every person found counts as 'not Sable'.
  --hud-frame a screenshot with Sable's HUD portrait and --hud-slot (0 = top): saves the gate template.

Evaluation (--no-eval skips it): pieces are built from even blocks of each positive source and
tested on the odd blocks, against all negatives. It prints precision and recall per threshold.
"""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.core import sable_detector as sd  # noqa: E402
from src.core.sable_model import cached  # noqa: E402

BLOCK = 2.          # seconds: build / test alternate in blocks, so a test frame is never next to a build frame
DEDUPE = .985       # a new piece closer than this to a kept one adds nothing


def frames(path, every):
    capture = cv2.VideoCapture(str(path)); fps = capture.get(cv2.CAP_PROP_FPS) or 30.; step = max(1, round(fps * every)); index = 0
    while True:
        ok, frame = capture.read()
        if not ok: break
        if index % step == 0: yield index / fps, frame
        index += 1
    capture.release()


def images(path, every):
    path = Path(path)
    if path.suffix.lower() in ('.png', '.jpg', '.jpeg'):
        image = cv2.imread(str(path))
        if image is not None: yield 0., image
    else: yield from frames(path, every)


def store_box(people, width, height):
    """The store shows one large figure right of the item grid."""
    found = [(box, c) for box, c in people if box[0] + box[2] / 2 > width * .45 and box[3] > height * .4]
    return max(found, key=lambda item: item[1])[0] if found else None


def own_box(people, width, height):
    found = [(box, c) for box, c in people if sd.own_character(box, width, height)]
    return max(found, key=lambda item: item[0][3])[0] if found else None


def augment(crop, rng):
    """In a match she is further away and blurred by motion.

    Darkness and fog are deliberately NOT simulated: measured on 2026-11-12, darkened store pieces
    match almost any character in a dark match (52-62 of 66 other characters above 0.86), so they
    only add false alarms. Real in-match footage has to supply the dark looks."""
    small = cv2.resize(crop, None, fx=.25, fy=.25, interpolation=cv2.INTER_AREA)
    return [crop, cv2.GaussianBlur(crop, (0, 0), 2.), cv2.resize(small, (crop.shape[1], crop.shape[0]), interpolation=cv2.INTER_LINEAR)]


def collect(source, kind, detector, every):
    """[(time, {part: crop})] for the Sable figure of a positive source, or every person of a negative one."""
    out = []
    for t, image in images(source, every):
        height, width = image.shape[:2]; people = detector.detect(image)
        if kind == 'negative':
            boxes = [box for box, _ in people if box[3] >= height * sd.MIN_PERSON_HEIGHT]
        else:
            box = (store_box if kind == 'store' else own_box)(people, width, height); boxes = [box] if box else []
        out += [(t, sd.part_crops(image, box)) for box in boxes]
    return out


def add_pieces(pieces, crops, rng, augmented=True):
    for part, crop in crops.items():
        if crop.size == 0 or min(crop.shape[:2]) < 4: continue
        for variant in (augment(crop, rng) if augmented else [crop]):
            feature = sd.piece_feature(variant)
            if feature is None: continue
            kept = pieces[part]
            if not kept or float((np.asarray(kept) @ feature).max()) < DEDUPE: kept.append(feature)


def features_of(crops):
    return {part: sd.piece_feature(crop) for part, crop in crops.items()}


def evaluate(positives, negatives, rng):
    """Build from even blocks, test on odd blocks. Returns (positive scores, negative scores)."""
    pieces = {part: [] for part in sd.PART_WEIGHTS}; held = []
    for t, crops in positives:
        if int(t // BLOCK) % 2 == 0: add_pieces(pieces, crops, rng)
        else: held.append(crops)
    library = sd.Library({part: np.array(v, np.float32) for part, v in pieces.items()})
    if not library.ready: return [], []
    return [library.score(features_of(c))[0] for c in held], [library.score(features_of(c))[0] for _, c in negatives]


def report(pos, neg):
    print(f'  held-out positives {len(pos)}, negatives {len(neg)}')
    if not pos: return None
    print('  threshold  recall  false positives  precision')
    best = None
    for threshold in np.arange(.70, .99, .03):
        tp = sum(s >= threshold for s in pos); fp = sum(s >= threshold for s in neg)
        recall = tp / len(pos); precision = tp / max(1, tp + fp)
        print(f'    {threshold:.2f}     {recall:5.0%}   {fp:4d} / {len(neg):<4d}     {precision:5.0%}')
        if fp == 0 and best is None: best = float(threshold)
    return best


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--store', type=Path, default=ROOT / 'debug/match_samples/sable_store_outfits.mp4')
    parser.add_argument('--positive', type=Path, nargs='*', default=[])
    parser.add_argument('--negative', type=Path, nargs='*', default=[])
    parser.add_argument('--model', type=Path, default=None, help='yolox_tiny.onnx; default: data/models or build/models')
    parser.add_argument('--out', type=Path, default=ROOT / 'assets/sable/library.npz')
    parser.add_argument('--every', type=float, default=.25, help='seconds between sampled frames')
    parser.add_argument('--no-eval', action='store_true')
    parser.add_argument('--hud-frame', type=Path); parser.add_argument('--hud-slot', type=int, default=0)
    parser.add_argument('--labels', type=Path, help='debug/sable_frames: frames labelled in the tool (Sable / not Sable boxes)')
    args = parser.parse_args(); rng = np.random.default_rng(31)
    if args.hud_frame:
        from src.core.match_reader import Geometry, fit_hud, load_layout, with_hud_fit
        image = cv2.imread(str(args.hud_frame)); base = load_layout(ROOT); layout = with_hud_fit(base, *fit_hud(image, base)[:2])
        g = Geometry(layout, image.shape); h = layout['hud']; cx, cy = g.hud(h['portrait_x'], h['portrait_y'][args.hud_slot]); r = g.size(h['portrait_radius'])
        folder = ROOT / 'assets/ui/hud_portraits'; folder.mkdir(parents=True, exist_ok=True); count = len(list(folder.glob('sable_ward*.png')))
        target = folder / f'sable_ward_{count + 1}.png'; cv2.imwrite(str(target), cv2.resize(image[cy - r:cy + r, cx - r:cx + r], (96, 96), interpolation=cv2.INTER_AREA))
        print(f'HUD gate template saved: {target}')
    model = args.model or cached(ROOT) or ROOT / 'build/models/yolox_tiny.onnx'
    if not Path(model).is_file(): sys.exit('Person model not found: enable the Sable Detector once (it downloads the model) or pass --model.')
    detector = sd.PersonDetector(model)
    sources = ([('store', args.store)] if args.store and args.store.is_file() else []) + [('positive', p) for p in args.positive]
    positives = {}
    for kind, path in sources:
        positives[path] = collect(path, kind, detector, args.every); print(f'{kind:<8} {path.name}: {len(positives[path])} Sable figures')
    negatives = []
    if args.labels:
        from src.core.sable_frames import labelled
        frames = labelled(args.labels); found = 0
        for index, (jpg, record) in enumerate(frames):
            image = cv2.imread(str(jpg))
            for entry, label in zip(record['boxes'], record['labels']):
                if label not in ('sable', 'not_sable'): continue
                item = (float(index * BLOCK), sd.part_crops(image, entry["box"]))   # even frames build, odd frames test
                (positives.setdefault(Path(f'labelled_{jpg.parent.name}'), []) if label == 'sable' else negatives).append(item); found += 1
        print(f'labelled {args.labels}: {len(frames)} frames, {found} boxes ({sum(len(v) for k, v in positives.items() if k.name.startswith("labelled"))} Sable)')
    for path in args.negative:
        found = collect(path, 'negative', detector, args.every); negatives += found; print(f'negative {path.name}: {len(found)} people')
    threshold = None
    if not args.no_eval:
        print('Evaluation (built from even 2 s blocks, tested on odd blocks):')
        pos, neg = evaluate([item for items in positives.values() for item in items], negatives, rng); threshold = report(pos, neg)
        for path in positives:
            if len(positives) > 1:
                rest = [item for other, items in positives.items() if other != path for item in items]
                pieces = {part: [] for part in sd.PART_WEIGHTS}
                for _, crops in rest: add_pieces(pieces, crops, rng)
                library = sd.Library({part: np.array(v, np.float32) for part, v in pieces.items()})
                if library.ready:
                    scores = [library.score(features_of(c))[0] for _, c in positives[path]]
                    print(f'  leave-one-source-out {path.name}: median score {np.median(scores):.2f}, share above 0.70: {np.mean(np.array(scores) >= .70):.0%}')
    pieces = {part: [] for part in sd.PART_WEIGHTS}
    for items in positives.values():
        for _, crops in items: add_pieces(pieces, crops, rng)
    library = sd.Library({part: np.array(v, np.float32) for part, v in pieces.items()})
    library.save(args.out, sources=np.array([p.name for p in positives]), suggested_threshold=np.array(threshold if threshold is not None else -1.))
    print(f'Library written: {args.out} ({", ".join(f"{part} {len(library.pieces[part])}" for part in sd.PART_WEIGHTS)}), {args.out.stat().st_size / 1e3:.0f} kB')


if __name__ == '__main__':
    main()
