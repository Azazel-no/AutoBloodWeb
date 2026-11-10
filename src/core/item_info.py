"""Add-on, item and offering descriptions, read from the local catalog only."""
from dataclasses import dataclass
import json
from pathlib import Path

CATALOG='assets/item_info.json'


@dataclass(frozen=True)
class ItemInfo:
    name: str
    rarity: str  # Wiki rarity text, empty when the Wiki shows none.
    description: str
    flavor: str
    page: str


def _read(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def load_item_info(root):
    """Return {library entry id: ItemInfo}.

    An entry is linked only through its own Wiki id or a visually confirmed
    reference. Suggestions still marked for manual review stay unlinked, so a
    tile never shows the description of a different item.
    """
    root=Path(root);records=_read(root/CATALOG).get('records',{})
    urls={r['wiki_id']:r['url'] for r in _read(root/'assets/wiki_manifest.json').get('records',[]) if r.get('wiki_id')}
    for ident,references in _read(root/'assets/wiki_active.json').get('additional_references',{}).items():
        for reference in references:
            if reference.get('url') in records:urls[ident]=reference['url'];break
    return {ident:ItemInfo(**records[url]) for ident,url in urls.items() if url in records}
