"""Random loadouts from the local catalogs. No game input and no network."""
from dataclasses import dataclass, replace
import json
from pathlib import Path
import random

from src.core.item_info import load_item_info
from src.core.perk_catalog import load_catalog

ADDON_SLOTS = ('addon1', 'addon2')
PERK_SLOTS = ('perk1', 'perk2', 'perk3', 'perk4')
ITEM_WORDS = (('flashlight', 'flashlight'), ('med-kit', 'medkit'), ('aid kit', 'medkit'), ('toolbox', 'toolbox'), ('tools', 'toolbox'),
              ('map', 'map'), ('key', 'key'), ('fog vial', 'fog_vial'))


@dataclass(frozen=True)
class Choice:
    id: str
    name: str
    image: str
    rarity: str = ''


@dataclass(frozen=True)
class Options:
    role: str = 'killer'
    perks: bool = False       # Killer: also roll 4 perks.
    offering: bool = False
    character: bool = False   # Survivor: also roll a survivor.
    limited: bool = False     # Include limited and event-only content.
    ptb: bool = False         # Include PTB content.
    excluded: frozenset = frozenset()


def item_type(name):
    low = name.lower()
    return next((kind for word, kind in ITEM_WORDS if word in low), None)


class Pool:
    """Everything that can be rolled; every list is already free of unnamed entries."""
    def __init__(self, root, library, character_name=lambda c: c):
        root = Path(root);info = load_item_info(root);self.root = root
        data_path = root / 'assets/randomizer_data.json'
        data = json.loads(data_path.read_text(encoding='utf-8')) if data_path.is_file() else {}
        ptb_path = root / 'assets/ptb_data.json'
        ptb = json.loads(ptb_path.read_text(encoding='utf-8')) if ptb_path.is_file() else {}
        self.ptb_ids = set()
        self.event_ids = set(getattr(library, 'event_metadata', {}))
        display = getattr(library, 'display_images', {})
        portraits = getattr(library, 'portraits', {})

        def choice(entry):
            return Choice(entry.id, info[entry.id].name, display.get(entry.id, entry.image), info[entry.id].rarity)
        self.addons_by_killer = {}
        for entry in library.entries:
            if entry.role == 'killer' and entry.category == 'addons' and entry.character and entry.id in info:
                self.addons_by_killer.setdefault(entry.character, []).append(choice(entry))
        self.killers = [Choice(c, character_name(c), portraits[c]) for c in sorted(self.addons_by_killer) if portraits.get(c)]
        if ptb.get('killer'):
            self.ptb_ids.add(ptb['killer']['id'])
        self.offerings = {role: [choice(e) for e in library.entries if e.role == role and e.category == 'offerings' and e.id in info] for role in ('killer', 'survivor')}
        self.items = [choice(e) for e in library.entries if e.role == 'survivor' and e.category == 'items' and e.id in info and item_type(info[e.id].name)]
        types = data.get('addon_types', {})
        self.addons_by_type = {}
        for entry in library.entries:
            if entry.role == 'survivor' and entry.category == 'addons' and entry.id in info and info[entry.id].name in types:
                self.addons_by_type.setdefault(types[info[entry.id].name], []).append(choice(entry))
        # The library holds only part of the item add-ons; the rest are identified Wiki downloads.
        known = {c.name for choices in self.addons_by_type.values() for c in choices}
        catalog = json.loads((root / 'assets/item_info.json').read_text(encoding='utf-8')).get('records', {})
        manifest = root / 'assets/wiki_manifest.json'
        for record in json.loads(manifest.read_text(encoding='utf-8')).get('records', []) if manifest.is_file() else []:
            row = catalog.get(record['url'])
            if record['role'] == 'survivor' and record['category'] == 'addons' and row and row['name'] in types and row['name'] not in known:
                known.add(row['name']);self.addons_by_type.setdefault(types[row['name']], []).append(Choice(record['wiki_id'], row['name'], record['normalized'], row['rarity']))
        self.perks = {}
        ptb_perks = {name for side in ('killer', 'survivor') for name in ptb.get(side, {}).get('perks', [])}
        for role in ('killer', 'survivor'):
            records = load_catalog(root, role)
            self.perks[role] = [Choice(p.id, p.name, p.icon) for p in records]
            self.ptb_ids |= {p.id for p in records if p.name in ptb_perks}
        self.survivors = [Choice(s['id'], s['name'], s['image']) for s in data.get('survivors', []) if (root / s['image']).is_file()]
        self.ptb_ids |= {s.id for s in self.survivors if s.name == ptb.get('survivor', {}).get('name')}

    def allowed(self, choices, options):
        return [c for c in choices if c.id not in options.excluded and (options.limited or c.id not in self.event_ids) and (options.ptb or c.id not in self.ptb_ids)]

    def item_candidates(self, options):
        """Only items whose type has at least two usable add-ons can be rolled."""
        return [i for i in self.allowed(self.items, options) if len(self.allowed(self.addons_by_type.get(item_type(i.name), []), options)) >= 2]

    def killer_candidates(self, options):
        return [k for k in self.allowed(self.killers, options) if len(self.allowed(self.addons_by_killer[k.id], options)) >= 2]

    def roll(self, options, rng=random):
        result = {}
        if options.role == 'killer':
            result['killer'] = rng.choice(self.killer_candidates(options))
            result.update(zip(ADDON_SLOTS, rng.sample(self.allowed(self.addons_by_killer[result['killer'].id], options), 2)))
            if options.perks:
                result.update(zip(PERK_SLOTS, rng.sample(self.allowed(self.perks['killer'], options), 4)))
        else:
            result.update(zip(PERK_SLOTS, rng.sample(self.allowed(self.perks['survivor'], options), 4)))
            result['item'] = rng.choice(self.item_candidates(options))
            result.update(zip(ADDON_SLOTS, rng.sample(self.allowed(self.addons_by_type[item_type(result['item'].name)], options), 2)))
            if options.character:
                result['survivor'] = rng.choice(self.allowed(self.survivors, options))
        if options.offering:
            result['offering'] = rng.choice(self.allowed(self.offerings[options.role], options))
        return result

    def owner_addons(self, result, options):
        if 'killer' in result:
            return self.allowed(self.addons_by_killer[result['killer'].id], options)
        return self.allowed(self.addons_by_type[item_type(result['item'].name)], options)

    def reroll(self, result, slot, options, rng=random):
        """A new value for one slot under the same rules; a new killer or item also renews its add-ons."""
        out = dict(result);current = result[slot]

        def other(choices, taken=()):
            rest = [c for c in choices if c != current and c not in taken]
            return rng.choice(rest) if rest else current
        if slot in ('killer', 'item'):
            out[slot] = other(self.killer_candidates(options) if slot == 'killer' else self.item_candidates(options))
            out.update(zip(ADDON_SLOTS, rng.sample(self.owner_addons(out, options), 2)))
        elif slot in ADDON_SLOTS:
            out[slot] = other(self.owner_addons(result, options), [result[s] for s in ADDON_SLOTS])
        elif slot in PERK_SLOTS:
            out[slot] = other(self.allowed(self.perks[options.role], options), [result[s] for s in PERK_SLOTS])
        elif slot == 'offering':
            out[slot] = other(self.allowed(self.offerings[options.role], options))
        elif slot == 'survivor':
            out[slot] = other(self.allowed(self.survivors, options))
        return out

    def spin_images(self, slot, result, options, count, rng=random):
        """Icons shown spinning on one reel."""
        if slot == 'killer':
            source = self.killers
        elif slot == 'item':
            source = self.items
        elif slot == 'survivor':
            source = self.survivors
        elif slot == 'offering':
            source = self.offerings[options.role]
        elif slot in ADDON_SLOTS:
            source = self.owner_addons(result, options)
        else:
            source = self.perks[options.role]
        return [c.image for c in rng.sample(source, min(count, len(source)))]
