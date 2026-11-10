"""Build assets/otz_tierlist.json from the user's saved copy of Otzdarva's add-on tier list.

Reads only debug/otz_tierlist/*.html: the site itself disallows automated access.
Credit: Otzdarva (see THIRD_PARTY_NOTICES.md).
"""
import html
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TIERS = ('strongest', 'good', 'decent', 'almost-no-effect', 'detrimental')  # Page colour groups, best first.
PAGE = 'https://otz-addon-tierlist.pages.dev/'


def norm(text):
    plain = ''.join(c for c in unicodedata.normalize('NFKD', html.unescape(text)) if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]', '', plain.lower().replace('judgement', 'judgment'))


def parse(page):
    """[(aliases, [(name, tier, comment), ...])] in page order."""
    rows = []
    for block in page.split('<div class="killerGroup">')[1:]:
        killer = re.search(r'<div class="killer"[^>]*>\s*<img [^>]*alt="([^"]*)"', block)
        if not killer:
            continue
        addons = []
        for tier, comment, alt in re.findall(
                r'<div class="addon-back ([a-z-]+)">\s*<div class="addon" data-comment="([^"]*)".*?<img [^>]*alt="([^"]*)"',
                block, re.S):
            text = re.sub(r'<[^>]+>', '', html.unescape(comment)).replace('\xa0', ' ')
            addons.append((html.unescape(alt).strip(), tier, re.sub(r'^[^:]{0,60}:\s*', '', text.strip()).strip()))
        rows.append(([a.strip() for a in html.unescape(killer.group(1)).split(',')], addons))
    return rows


def main():
    from src.core.icon_library import IconLibrary
    from src.core.item_info import load_item_info
    from src.core.localization import STRINGS
    source = next((ROOT / 'debug/otz_tierlist').glob('*.html'))
    library = IconLibrary(ROOT).build()
    info = load_item_info(ROOT)
    aliases = json.loads((ROOT / 'assets/killer_aliases.json').read_text(encoding='utf-8'))['killers']
    characters = sorted({e.character for e in library.entries if e.character})
    exact = {}
    for c in characters:
        exact[norm(c)] = c
        exact[norm(STRINGS['en'].get('character_' + c, c))] = c
    loose = {norm(a): c for c, entry in aliases.items() for a in entry.get('en', [])}
    addons = {}
    for e in library.entries:
        if e.character and e.category == 'addons' and e.id in info:
            addons[(e.character, norm(info[e.id].name))] = e.id
    out, missing_killers, missing_addons = {}, [], []
    for names, row in parse(source.read_text(encoding='utf-8', errors='replace')):
        character = next((exact[norm(n)] for n in names if norm(n) in exact), None) or next((loose[norm(n)] for n in names if norm(n) in loose), None)
        if character is None:
            missing_killers.append(names)
            continue
        ordered = sorted(enumerate(row), key=lambda v: (TIERS.index(v[1][1]), v[0]))  # Group order, then left to right.
        found = []
        for _, (name, tier, comment) in ordered:
            ident = addons.get((character, norm(name)))
            if ident is None:
                missing_addons.append(dict(killer=character, addon=name))
            else:
                found.append(dict(id=ident, name=name, tier=tier, comment=comment))
        out[character] = dict(aliases=names, addons=found)
    report = dict(schema=1, source=PAGE, credit="Recommended add-ons: Otzdarva's tier list", tiers=TIERS, killers=out,
                  unmatched_killers=missing_killers, unmatched_addons=missing_addons)
    (ROOT / 'assets/otz_tierlist.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(len(out), 'killers;', sum(len(v['addons']) for v in out.values()), 'add-ons')
    print('unmatched killers', missing_killers)
    print('unmatched add-ons', len(missing_addons), missing_addons[:40])
    print('no tier list for', sorted(set(characters) - set(out)))


if __name__ == '__main__':
    main()
