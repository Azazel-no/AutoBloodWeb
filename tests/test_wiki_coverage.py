"""Hotfix 1.0.1 C: every Wiki item and add-on that can appear in a Bloodweb has an entry, a name and an icon.

The reference list (tests/fixtures/wiki_reference_v101.json) was read from the Wiki
"Add-ons" and "Items" pages on 2026-11-11. Before 1.0.1, 21 survivor add-ons were
missing (among them Gel Dressings, Odd Bulb, Blood Amber, Crimson Stamp, Potent Extract).
"""
import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote
import pytest
from src.core.icon_library import IconLibrary
from src.core.item_info import load_item_info

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = json.loads((ROOT / 'tests/fixtures/wiki_reference_v101.json').read_text(encoding='utf-8'))['entries']


@lru_cache(maxsize=1)
def coverage():
    library = IconLibrary(ROOT).build()
    entries = {e.id: e for e in library.entries}
    by_url = {}
    for record in json.loads((ROOT / 'assets/wiki_manifest.json').read_text(encoding='utf-8'))['records']:
        if record.get('wiki_id') in entries: by_url.setdefault(unquote(record['url']), record['wiki_id'])
    for ident, refs in getattr(library, 'wiki_references', {}).items():
        if ident in entries:
            for ref in refs: by_url.setdefault(unquote(ref['url']), ident)
    for ident, meta in getattr(library, 'event_metadata', {}).items():
        if ident in entries and meta.get('source_url'): by_url.setdefault(unquote(meta['source_url']), ident)
    return library, entries, by_url, load_item_info(ROOT)


def ids(row): return f"{row['role']}-{row['group']}-{row['name']}"


@pytest.mark.parametrize('row', [r for r in REFERENCE if r['bloodweb']], ids=ids)
def test_wiki_entry_has_catalogue_entry_and_icon(row):
    library, entries, by_url, info = coverage()
    ident = by_url.get(unquote(row['url']))
    assert ident, f"missing from the catalogue: {row['name']} ({row['group']}, {row['rarity']})"
    entry = entries[ident]
    assert (entry.role, entry.category) == (row['role'], row['category'])
    assert (ROOT / entry.image).is_file(), 'recognition template missing'
    assert (ROOT / library.display_images.get(ident, entry.image)).is_file(), 'icon missing'
    if ident not in getattr(library, 'event_metadata', {}):
        assert info[ident].name == row['name'], 'name not linked'


def test_reference_is_complete_enough():
    groups = {r['group'] for r in REFERENCE if r['role'] == 'survivor' and r['category'] == 'addons'}
    assert groups == {'Flashlights', 'Keys', 'Maps', 'Med-Kits', 'Toolboxes', 'Fog Vials'}
    assert sum(r['role'] == 'killer' for r in REFERENCE) >= 900


def test_survivor_addons_have_item_compatibility_for_the_randomizer():
    types = json.loads((ROOT / 'assets/randomizer_data.json').read_text(encoding='utf-8'))['addon_types']
    expected = {'Flashlights': 'flashlight', 'Keys': 'key', 'Maps': 'map', 'Med-Kits': 'medkit', 'Toolboxes': 'toolbox', 'Fog Vials': 'fog_vial'}
    for row in REFERENCE:
        if row['role'] == 'survivor' and row['category'] == 'addons':
            assert types.get(row['name']) == expected[row['group']], row['name']


def test_every_survivor_bloodweb_entry_is_selectable_in_priorities(tmp_path):
    """User reports (24.11): items "missing in the priorities list". The catalogue alone is not
    enough: each entry must also appear in the survivor icon grid (its own category and "all",
    whatever killer was last chosen), carry a rarity, and survive a save/reload of priorities."""
    from src.core.preferences import Preferences
    library, entries, by_url, info = coverage()
    rows = [r for r in REFERENCE if r['role'] == 'survivor' and r['bloodweb']]
    wanted = [by_url[unquote(r['url'])] for r in rows]
    for character in ('blight', 'nurse', 'terrifier'):
        everything = {e.id for e in library.filter('survivor', None, character)}
        for row, ident in zip(rows, wanted):
            assert ident in everything, f"{row['name']} hidden from the survivor list (killer {character})"
            assert ident in {e.id for e in library.filter('survivor', row['category'], character)}, row['name']
    for row, ident in zip(rows, wanted):
        rarity = info[ident].rarity if ident in info else library.event_metadata[ident].get('availability')
        assert rarity, f"{row['name']}: no rarity"
    path = tmp_path / 'preferences.json'
    prefs = Preferences(path, library.entries)
    prefs.priorities['survivor'] = list(wanted)
    prefs.save('ru', 'survivor')
    assert Preferences(path, library.entries).priorities['survivor'] == wanted
