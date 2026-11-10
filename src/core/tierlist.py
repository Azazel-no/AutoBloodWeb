"""Recommended add-on priorities from Otzdarva's tier list (assets/otz_tierlist.json)."""
from functools import lru_cache
import json
from pathlib import Path

from src.core.priority_targets import PERK_TARGET

FILE = 'assets/otz_tierlist.json'


@lru_cache(maxsize=4)
def _load(root):
    path = Path(root) / FILE
    return json.loads(path.read_text(encoding='utf-8'))['killers'] if path.is_file() else {}


def recommended(root, characters=()):
    """{character: [PERKS, add-ons best first]}; a killer not on the list yet gets PERKS only."""
    listed = {c: [PERK_TARGET] + [a['id'] for a in v['addons']] for c, v in _load(str(root)).items()}
    return {**{c: [PERK_TARGET] for c in characters}, **listed}


def aliases(root, character):
    return list(_load(str(root)).get(character, {}).get('aliases', []))


@lru_cache(maxsize=4)
def _comments(root):
    return {a['id']: a['comment'] for v in _load(root).values() for a in v['addons'] if a.get('comment')}


def comment(root, ident):
    return _comments(str(root)).get(ident, '')
