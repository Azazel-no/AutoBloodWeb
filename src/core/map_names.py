"""Map name table and fuzzy matching of the loading-screen OCR lines (block E, v1.1.0).

The table (assets/maps/map_names.json) holds every realm and map in EN, RU, ES, DE, FR; names
come from dbd-map-overlay's i18n files (Apache-2.0, see THIRD_PARTY_NOTICES.md). A realm alone
never picks a map, and an uncertain read returns None: showing no map beats showing a wrong one.
"""
import functools
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

LANGUAGES = ('en', 'ru', 'es', 'de', 'fr')
DEFAULT_KEY = 'F1'


def default_settings():
    return dict(enabled=False, key=DEFAULT_KEY, size=100, opacity=85)


def clean_settings(data):
    data = data if isinstance(data, dict) else {}; out = default_settings()
    out['enabled'] = data.get('enabled') is True
    if isinstance(data.get('key'), str) and data['key'].strip(): out['key'] = data['key'].strip().upper()
    for name, low, high in (('size', 50, 150), ('opacity', 30, 100)):
        if type(data.get(name)) is int and low <= data[name] <= high: out[name] = data[name]
    return out


# OCR mixes Latin and Cyrillic look-alikes in the same word; fold both ways to the same letters.
CYRILLIC_LOOKALIKES = str.maketrans('аеорсухкмтвн', 'aeopcyxkmtbh')


def fold(text):
    """Lower case, no accents or apostrophes, single spaces; 'Léry’s' and 'lerys' are equal."""
    text = unicodedata.normalize('NFD', str(text or ''))
    text = ''.join(c for c in text if not unicodedata.combining(c)).lower().replace('ё', 'е').replace('й', 'и')
    text = re.sub(r"['`´’‘]", '', text)
    return re.sub(r'[^0-9a-zа-я]+', ' ', text).strip()


def levenshtein(a, b):
    if len(a) < len(b): a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


@dataclass(frozen=True)
class Map:
    key: str        # English name, the id used in settings and logs
    realm: str
    names: tuple    # (language, name) pairs
    image: str | None
    md5: str | None


@dataclass(frozen=True)
class Match:
    map: Map
    distance: int   # 0 = exact after folding
    text: str       # the OCR text that matched


@functools.lru_cache(maxsize=2)
def load_maps(root):
    data = json.loads((Path(root) / 'assets/maps/map_names.json').read_text(encoding='utf-8'))
    maps = []
    for realm in data['realms']:
        for m in realm['maps']:
            maps.append(Map(m['key'], realm['key'], tuple(m['names'].items()), m.get('hens333'), m.get('md5')))
    return tuple(maps), data['images_base'], tuple(realm['key'] for realm in data['realms'])


@functools.lru_cache(maxsize=2)
def load_realms(root):
    """{realm key: (its names in every language)}."""
    data = json.loads((Path(root) / 'assets/maps/map_names.json').read_text(encoding='utf-8'))
    return {realm['key']: tuple(dict.fromkeys([realm['key'], *realm.get('names', {}).values()])) for realm in data['realms']}


SEPARATORS = re.compile(r'\s+[-–—:]+\s+|\s*[–—]\s*')
MIN_PREFIX = 5   # folded letters of a cut-off map name after its realm


class MapMatcher:
    def __init__(self, maps, realms=None):
        self.maps = maps; self.names = []   # (folded name, map)
        self.realms = {}                     # folded realm name -> realm key
        for key, names in (realms or {}).items():
            for name in names:
                folded = fold(name)
                for variant in {folded, folded[4:] if folded.startswith('the ') else folded}:
                    self.realms[variant] = key; self.realms[variant.translate(CYRILLIC_LOOKALIKES)] = key
        for m in maps:
            for folded in {fold(name) for _, name in m.names} | {fold(m.key)}:
                self.names.append((folded, m)); self.names.append((folded.translate(CYRILLIC_LOOKALIKES), m))
                if folded.startswith('the '): self.names.append((folded[4:], m))

    def by_key(self, key):
        return next((m for m in self.maps if m.key == key), None)

    def match_text(self, text):
        """Best map for one string, or None when it is too far or two maps are equally close."""
        raw = fold(text)
        if len(raw) < 4: return None
        candidates = {raw, raw.translate(CYRILLIC_LOOKALIKES)}
        distance = {}   # map key -> (closest distance, map)
        for name, m in self.names:
            for c in candidates:
                if abs(len(c) - len(name)) > max(2, len(name) // 5): continue
                d = levenshtein(c, name)
                if m.key not in distance or d < distance[m.key][0]: distance[m.key] = (d, m)
        ranked = sorted(distance.values(), key=lambda pair: pair[0])
        if not ranked or ranked[0][0] > max(1, int(len(raw) * .18)): return None
        if len(ranked) > 1 and ranked[1][0] <= ranked[0][0]: return None   # Two maps equally close.
        return Match(ranked[0][1], ranked[0][0], text)

    def realm_of(self, text):
        """Realm key for a realm name read with at most one OCR slip per eight letters, else None."""
        raw = fold(text)
        if len(raw) < 4 or not self.realms: return None
        best = sorted((levenshtein(c, name), key) for name, key in self.realms.items() for c in {raw, raw.translate(CYRILLIC_LOOKALIKES)}
                      if abs(len(c) - len(name)) <= max(1, len(name) // 8))
        if not best or best[0][0] > max(1, len(raw) // 8): return None
        if len({key for d, key in best if d == best[0][0]}) > 1: return None
        return best[0][1]

    def match_realm_prefix(self, text):
        """'YAMAOKA ESTATE - FAMILY RE': a confidently read realm, then the start of exactly one of its maps.

        Live 12.11: the long Yamaoka line came out cut at 'FAMILY RE' and no map was shown."""
        parts = [part for part in SEPARATORS.split(str(text)) if part.strip()]
        if len(parts) < 2: return None
        realm = self.realm_of(parts[0])
        if realm is None: return None
        rest = fold(' '.join(parts[1:]))
        if len(rest.replace(' ', '')) < MIN_PREFIX: return None
        hits = {}
        for name, m in self.names:
            if m.realm != realm: continue
            for c in {rest, rest.translate(CYRILLIC_LOOKALIKES)}:
                if len(name) < len(c): continue
                d = levenshtein(c, name[:len(c)])
                if d <= len(c) // 8 and (m.key not in hits or d < hits[m.key][0]): hits[m.key] = (d, m)
        ranked = sorted(hits.values(), key=lambda pair: pair[0])
        if not ranked or (len(ranked) > 1 and ranked[1][0] - ranked[0][0] < 2): return None   # 'Ala este' vs 'Ala oeste': two edits apart is enough.
        d, m = ranked[0]
        return Match(m, d, text)

    def match_lines(self, lines):
        """Single lines first, then two or three joined lines (long names wrap on the loading screen)."""
        lines = [l for l in (str(x).strip() for x in lines) if l]
        found = []
        for line in lines:   # 'Realm - Map' on one line: each part on its own, then a cut-off map name.
            parts = [part for part in SEPARATORS.split(line) if part.strip()]
            if len(parts) > 1:
                found += [hit for part in parts if (hit := self.match_text(part))]
                if not found and (hit := self.match_realm_prefix(line)): found.append(hit)
        if found:
            found.sort(key=lambda h: h.distance)
            if len({h.map.key for h in found if h.distance == found[0].distance}) > 1: return None
            return found[0]
        for size in (1, 2, 3):
            for i in range(len(lines) - size + 1):
                hit = self.match_text(' '.join(lines[i:i + size]))
                if hit: found.append(hit)
            if found: break
        if not found: return None
        found.sort(key=lambda h: h.distance)
        if len({h.map.key for h in found if h.distance == found[0].distance}) > 1: return None   # Two different maps read: uncertain.
        return found[0]


def ocr_lines(result):
    """Windows OCR words ({text,x,y,w,h}) grouped back into lines, top to bottom."""
    lines = []
    for word in sorted(result.get('words', []), key=lambda w: (w['y'], w['x'])):
        middle = word['y'] + word['h'] / 2
        line = next((l for l in lines if abs(l['middle'] - middle) <= max(l['h'], word['h']) / 2), None)
        if line is None: lines.append(dict(middle=middle, h=word['h'], words=[word]))
        else: line['words'].append(word)
    return [' '.join(w['text'] for w in sorted(l['words'], key=lambda w: w['x'])) for l in lines]
