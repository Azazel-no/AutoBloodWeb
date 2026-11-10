"""Search-only killer names from one data file. Never changes a killer ID."""
from functools import lru_cache
import json
from pathlib import Path

FILE='assets/killer_aliases.json'


@lru_cache(maxsize=4)
def _load(root):
    path=Path(root)/FILE
    if not path.is_file():return {}
    data=json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema')!=1:raise ValueError('invalid_killer_aliases')
    return data['killers']


def aliases_for(root,character):
    entry=_load(str(root)).get(character,{})
    return list(entry.get('en',[]))+list(entry.get('ru',[]))
