"""Offline replay of the chase meme detector over saved frames (TASKS 2026-11-25, B1/B3/B7).

Usage:  python scripts/chase_replay.py FOLDER [--json OUT]

FOLDER holds full-client frames (.jpg/.png, e.g. debug/chase_events/<day>/ or debug/chase_false/).
For every frame it prints the chase signal of each of the 4 HUD rows, measured the same way for
all rows (src/core/chase_meme.row_scores), which row wins, the margin, and whether the per-frame
own-row rule (src/core/chase_meme.own_row_wins) would allow a start. Frames are read one by
one, so motion (the horns animating) is not judged here; the live detector adds that.
No screen capture, no input.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.core import chase_meme as cm
from src.utils.images import read_image


def replay(folder, layout=None):
    layout = layout or json.loads((ROOT / 'assets/ui/match_layout.json').read_text(encoding='utf-8'))
    rows = []
    for path in sorted(Path(folder).glob('*')):
        if path.suffix.lower() not in ('.jpg', '.png'): continue
        image = read_image(path)
        if image is None: continue
        scores = cm.row_scores(image, layout)
        allowed, why = cm.own_row_wins(scores)
        meta = path.with_suffix('.json')
        kind = json.loads(meta.read_text(encoding='utf-8')).get('kind') if meta.is_file() else None
        rows.append(dict(frame=path.name, saved_as=kind, scores=[round(s, 3) for s in scores],
                         winner=max(range(4), key=lambda i: scores[i]) + 1, allowed=allowed, why=why))
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('folder'); parser.add_argument('--json')
    args = parser.parse_args()
    found = replay(args.folder)
    for r in found:
        print(f"{r['frame']:<28} {str(r['saved_as']):<9} rows {' '.join(f'{s:.2f}' for s in r['scores'])}  "
              f"winner {r['winner']}  {'START ok' if r['allowed'] else 'BLOCK'}  {r['why']}")
    if args.json: Path(args.json).write_text(json.dumps(found, indent=1), encoding='utf-8')
