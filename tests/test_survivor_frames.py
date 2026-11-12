"""Hotfix 1.0.1 B: detection works on the Bloodwebs of different survivors, not only Cheryl Mason.

Real frames (account block blurred): Lee Yun-Jin and Yui Kimura (live 11.11, level 17),
Aestri Yazar (13.10, level 48) and The Executioner (live 11.11, level 33) as the killer
control. The failure on non-Cheryl survivors was not detection but a saved scene shared by
all survivors (see test_survivor_switch.py); these frames pin that detection itself is
survivor-agnostic and that the add-ons added in 1.0.1 are recognized.
"""
from functools import lru_cache
from pathlib import Path
import pytest
from src.core.bloodweb_detector import BloodwebDetector
from src.core.icon_library import IconLibrary
from src.core.item_info import load_item_info
from src.core.models import NodeState
from src.utils.images import read_image

ROOT = Path(__file__).resolve().parents[1]
FRAMES = {
    # name: (path, role, level, minimum nodes, minimum recognized, {(x, y): expected name})
    'lee_yun_jin': ('tests/fixtures/v101/lee_yun_jin_level17.png', 'survivor', 17, 11, 9,
                    {(443, 501): 'Power Bulb', (927, 621): 'Needle & Thread', (789, 624): 'Instructions'}),
    'yui_kimura': ('tests/fixtures/v101/yui_kimura_level17.png', 'survivor', 17, 11, 9,
                   {(684, 685): 'Needle & Thread', (928, 501): 'Rubber Gloves', (579, 624): 'Camping Aid Kit'}),
    'aestri_yazar': ('references/bloodweb/Снимок экрана 2026-10-13 091003.png', 'survivor', None, 25, 19,
                     {(435, 427): 'Needle & Thread'}),
    'executioner': ('tests/fixtures/v101/executioner_level33.png', 'killer', 33, 16, 9, {}),
}


@lru_cache(maxsize=1)
def shared():
    library = IconLibrary(ROOT).build()
    return library, load_item_info(ROOT)


@pytest.mark.parametrize('name', FRAMES)
def test_bloodweb_detection_on_several_characters(name):
    path, role, level, nodes, recognized, expected = FRAMES[name]
    library, info = shared()
    result = BloodwebDetector(library, role, character='executioner').analyze(read_image(ROOT / path), name)
    assert result.screen_state.value == 'bloodweb'
    if level is not None:
        assert result.level == level and result.level_confidence >= .95
    assert sum(n.state == NodeState.CENTER for n in result.nodes) == 1
    assert len(result.nodes) >= nodes
    assert sum(1 for n in result.nodes if n.icon_id) >= recognized
    for (x, y), wanted in expected.items():
        node = next(n for n in result.nodes if abs(n.x - x) <= 6 and abs(n.y - y) <= 6)
        assert node.icon_id in info and info[node.icon_id].name == wanted, (wanted, node.icon_id, node.candidates[:3])
