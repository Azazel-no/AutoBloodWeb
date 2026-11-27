"""Perk records: icon, owner and effect text, loaded from the local catalog only.

Perks stay out of IconLibrary on purpose: the Bloodweb matcher must not start
recognising them. Owner and description are kept for planned voice perk notes,
which filter by character and rank by the described effect.
"""
from dataclasses import dataclass, field
import json
from pathlib import Path

CATALOG='assets/perk_catalog.json'

# Reviewed Tab tile captured in DBD 10.3.0, 2026-11-26. The glyph differs
# from the rendered Wiki reference at this size. Keep the original crop.
TAB_REFERENCES = {
    'perk_survivor_valkyrie_s_rage': (
        'references/perks/valkyries_rage_tab.png',
        '299a4749af38b484f1731beec51947c0f31865832487ba1a889c17096af983a5',
    ),
}


@dataclass(frozen=True)
class PerkRecord:
    id: str
    name: str
    role: str  # survivor | killer
    owner: str | None  # Character who teaches it; None for general perks.
    description: str  # Effect text only, without flavour quotes.
    flavor: str
    icon: str  # Normalized 96px PNG, relative to the project root.
    original: str
    sha256: str
    url: str
    aliases: tuple[str, ...] = field(default_factory=tuple)  # Translated names when available.


def load_catalog(root,role=None):
    path=Path(root)/CATALOG
    if not path.is_file():return []
    data=json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema')!=1:raise ValueError('invalid_perk_catalog')
    perks=[PerkRecord(r['id'],r['name'],r['role'],r['owner'],r['description'],r['flavor'],r['normalized'],
                      r['original'],r['sha256'],r['url'],tuple(r.get('aliases',()))) for r in data['records']]
    return [p for p in perks if role is None or p.role==role]


def owned_by(perks,owner):
    wanted=owner.casefold()
    return [p for p in perks if p.owner and p.owner.casefold()==wanted]
