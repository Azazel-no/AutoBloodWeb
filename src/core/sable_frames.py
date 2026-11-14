"""Sable Detector training data: frames saved during real matches, for labelling and re-tuning (J3, 14.11).

Off by default. While it is on, frames are saved (downscaled JPEG + the person boxes found on it) to
debug/sable_frames/<date>/: often in matches where the HUD shows Sable, rarely in matches without
her (negatives). The folder is capped: the oldest files go first. Nothing leaves the computer.
"""
import json
import time
from datetime import datetime
from pathlib import Path

import cv2

WIDTH = 960                 # saved frames are scaled to this width
POSITIVE_EVERY = .75        # seconds between frames while Sable is in the match
NEGATIVE_EVERY = 6.         # seconds between frames in matches without her
MAX_FILES = 2000            # frames (each with its .json) across all days
MAX_BYTES = 1 << 30         # 1 GB


class FrameCollector:
    def __init__(self, root, clock=time.monotonic, now=datetime.now):
        self.base = Path(root) / 'debug/sable_frames'; self.clock = clock; self.now = now
        self.last = {True: -1e9, False: -1e9}; self.saved = 0

    def due(self, sable_in_match):
        return self.clock() - self.last[bool(sable_in_match)] >= (POSITIVE_EVERY if sable_in_match else NEGATIVE_EVERY)

    def offer(self, image, people, sable_in_match, hits=(), role=None):
        """Saves the frame when it is due. `people`: [((x, y, w, h), confidence)] in image pixels."""
        if not self.due(sable_in_match): return None
        self.last[bool(sable_in_match)] = self.clock()
        scale = min(1., WIDTH / image.shape[1]); small = cv2.resize(image[..., :3], None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else image[..., :3]
        stamp = self.now(); folder = self.base / stamp.strftime('%Y-%m-%d'); folder.mkdir(parents=True, exist_ok=True)
        name = f"{'pos' if sable_in_match else 'neg'}_{stamp.strftime('%H%M%S_%f')[:-3]}"
        ok, data = cv2.imencode('.jpg', small, [cv2.IMWRITE_JPEG_QUALITY, 88])
        if not ok: return None
        (folder / (name + '.jpg')).write_bytes(data.tobytes())
        scores = {tuple(h.box): round(float(h.score), 3) for h in hits}
        boxes = [dict(box=[round(v * scale) for v in box], confidence=round(float(c), 3), score=scores.get(tuple(box))) for box, c in people]
        (folder / (name + '.json')).write_text(json.dumps(dict(sable_in_match=bool(sable_in_match), role=role, scale=round(scale, 4),
            size=[small.shape[1], small.shape[0]], boxes=boxes, labels=None), indent=1), encoding='utf-8')
        self.saved += 1; self.prune()
        return folder / (name + '.jpg')

    def prune(self):
        """Keeps at most MAX_FILES frames and MAX_BYTES in all; the oldest are deleted first."""
        frames = sorted(self.base.glob('*/*.jpg'), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size + (p.with_suffix('.json').stat().st_size if p.with_suffix('.json').exists() else 0) for p in frames)
        while frames and (len(frames) > MAX_FILES or total > MAX_BYTES):
            old = frames.pop(0); size = old.stat().st_size
            sidecar = old.with_suffix('.json')
            if sidecar.exists(): size += sidecar.stat().st_size; sidecar.unlink()
            old.unlink(); total -= size


def labelled(folder):
    """[(jpg path, record)] of frames whose boxes were labelled in the tool."""
    out = []
    for sidecar in sorted(Path(folder).rglob('*.json')):
        record = json.loads(sidecar.read_text(encoding='utf-8'))
        if record.get('labels') is not None and sidecar.with_suffix('.jpg').is_file(): out.append((sidecar.with_suffix('.jpg'), record))
    return out
