"""Block E: map-name matching on loading-screen OCR text (exact, OCR typos, other languages)."""
from pathlib import Path

import pytest

from src.core.map_names import LANGUAGES, MapMatcher, fold, load_maps

ROOT = Path(__file__).resolve().parents[1]
MAPS, BASE, REALMS = load_maps(ROOT)
M = MapMatcher(MAPS)


def key(lines):
    hit = M.match_lines(lines if isinstance(lines, list) else [lines])
    return hit.map.key if hit else None


def test_table_is_complete_in_five_languages_with_images():
    assert len(REALMS) == 21 and len(MAPS) == 45
    for m in MAPS:
        names = dict(m.names)
        assert set(names) == set(LANGUAGES) and all(names.values())
        assert m.image and m.image.startswith('maps/Hens333/') and len(m.md5) == 32


@pytest.mark.parametrize('m', MAPS, ids=lambda m: m.key)
def test_every_name_in_every_language_matches_its_own_map(m):
    for language, name in m.names:
        hit = M.match_lines([name])
        # A few maps share one translated name with no other context (none expected); say which.
        assert hit and hit.map.key == m.key, (language, name)


def test_loading_screen_lines_realm_then_map():
    assert key(['COLDWIND FARM', 'ROTTEN FIELDS']) == 'Rotten Fields'
    assert key(['ФЕРМА КОЛДВИНД', 'ЗАБРОШЕННАЯ СКОТОБОЙНЯ']) == 'Rancid Abattoir'
    assert key(['Haddonfield']) is None                     # a realm alone never picks a map
    assert key(['RACCOON CITY', 'RACCOON CITY POLICE', 'STATION EAST WING']) == 'Raccoon City Police Station East Wing'


@pytest.mark.parametrize('text,expected', [
    ('LERYS MEMORIAL INSTITUTE TREATMENT THEATRE', None),     # realm and map in one line: not a known name, too far
    ('TREATMENT THEATRF', 'Treatment Theatre'),
    ('Azarovs Resting P1ace', 'Azarov’s Resting Place'),
    ('M0THERS DWELLING', "Mother's Dwelling"),
    ('Suffocalion Pit', 'Suffocation Pit'),
    ('Тhe Gаme', 'The Game'),                                 # Latin/Cyrillic look-alikes mixed by OCR
    ('Matadero pestilente', 'Rancid Abattoir'),
    ('Widerliches Schlachthaus', 'Rancid Abattoir'),
    ('Abattoir putride', 'Rancid Abattoir'),
    ('xqzv', None), ('', None), ('BLOODWEB', None),
])
def test_ocr_typos_and_languages(text, expected):
    assert key(text) == expected


def test_conflicting_reads_show_nothing():
    assert key(['Coal Tower', 'Blood Lodge']) is None


def test_fold():
    assert fold('Léry’s Memorial') == fold("lerys memorial") == 'lerys memorial'


# --- H2 (14.11): 'Realm - Map' lines and map names cut off at the end -------------------------------
def realm_matcher():
    from src.core.map_names import load_realms
    maps, _, _ = load_maps(ROOT)
    return MapMatcher(maps, load_realms(ROOT)), maps


def test_live_yamaoka_line_cut_after_family_re():
    """Live 12.11: 'YAMAOKA ESTATE - FAMILY RE:' (and its Cyrillic-lookalike twin) showed no map."""
    matcher, _ = realm_matcher()
    for line in ('YAMAOKA ESTATE - FAMILY RE:', 'УАМАОКА ESTATE - FAMILY RE:'):
        assert matcher.match_lines([line, 'BACK [ESC]']).map.key == 'Family Residence'
    assert matcher.match_lines(['YAMAOKA ESTATE - SANCTUM OF WR']).map.key == 'Sanctum of Wrath'


def test_every_map_with_its_realm_and_a_cut_name_in_every_language():
    import json
    from src.core.map_names import fold
    matcher, maps = realm_matcher(); checked = 0
    realms = {r['key']: r['names'] for r in json.loads((ROOT / 'assets/maps/map_names.json').read_text(encoding='utf-8'))['realms']}
    for m in maps:
        for language, name in m.names:
            realm = realms[m.realm].get(language, m.realm)
            cut = name[:max(8, round(len(name) * .7))]
            others = [o for o in maps if o.realm == m.realm and o.key != m.key and any(fold(n).startswith(fold(cut)) for _, n in o.names)]
            if others: continue   # Two maps of the realm start the same way: nothing must be guessed.
            hit = matcher.match_lines([f'{realm} - {cut}'])
            assert hit and hit.map.key == m.key, (language, realm, cut, hit and hit.map.key)
            checked += 1
    assert checked >= 200


def test_a_realm_alone_or_a_too_short_cut_still_shows_nothing():
    matcher, _ = realm_matcher()
    for line in ('YAMAOKA ESTATE', 'YAMAOKA ESTATE - FAM', 'YAMAOKA T E', 'THE MACMILLAN ESTATE - ', 'BACK [ESC]'):
        assert matcher.match_lines([line]) is None, line
    assert matcher.match_lines(['MACMILLAN ESTATE - COAL TOW']).map.key == 'Coal Tower'
