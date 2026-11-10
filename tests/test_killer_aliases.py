import json

import pytest
from PySide6.QtWidgets import QApplication

from src.core.killer_aliases import FILE, aliases_for
from src.gui.killer_selector import KillerSelector


def test_every_killer_in_the_library_has_aliases_and_no_unknown_ids(root):
    data = json.loads((root / FILE).read_text(encoding='utf-8'))['killers']
    folders = {p.name for p in (root / 'assets/killers/characters').iterdir() if p.is_dir()}
    assert folders == set(data)
    assert all(entry['en'] and entry['ru'] for entry in data.values())


@pytest.mark.parametrize('character,query', [
    ('cannibal', 'Bubba'), ('cannibal', 'БУБА'), ('cannibal', 'leatherface'), ('cannibal', 'кожаное лицо'),
    ('executioner', 'pyramid head'), ('executioner', 'Пирамидоголовый'), ('mastermind', 'WESKER'), ('mastermind', 'вескер'),
    ('nightmare', 'freddy'), ('shape', 'michael myers'), ('ghost_face', 'ghostface'), ('cenobite', 'pinhead'),
    ('good_guy', 'chucky'), ('good_guy', 'чаки'), ('wraith', 'ding dong'), ('slasher', 'jason'), ('dark_lord', 'дракула'),
    ('dark_lord', 'темный лорд'), ('dredge', 'грязь'), ('animatronic', 'springtrap'),
])
def test_search_finds_a_killer_by_nickname_in_either_language_and_any_case(root, character, query):
    app = QApplication.instance() or QApplication([])
    selector = KillerSelector(root)
    selector.addItem('X', character)
    selector.set_search_tokens(0, aliases_for(root, character))
    selector.showPopup()
    app.processEvents()
    selector.search_line.setText(query)
    assert not selector.search_results.item(0).isHidden()
    selector.search_line.setText('no such killer name')
    assert selector.search_results.item(0).isHidden()
    selector.popup.close()


def test_unknown_character_has_no_aliases(root):
    assert aliases_for(root, 'not_a_killer') == []
