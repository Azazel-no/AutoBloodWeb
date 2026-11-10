"""Live 7.11 20:36: 'IRIDESCENT SOUL GUARD CHARM UNLOCKED' did not match the 'REWARDS UNLOCKED' title."""
from pathlib import Path

import numpy as np

from src.core.video_ui import certificate
from src.utils.images import read_image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'references/bloodweb/v54_onryo/reward_1530.png'


def other_item_name(shift):
    """The same dialog with REWARDS removed and UNLOCKED moved, as a longer item name places it."""
    image = read_image(SOURCE).copy()
    x, y, w, h = 755, 253, 410, 39
    background = image[y - 40:y - 40 + h, x:x + w].copy()
    word = image[y:y + h, x + 203:x + w].copy()
    image[y:y + h, x:x + w] = background
    image[y:y + h, x + 203 + shift:x + w + shift] = word
    return image


def test_reward_with_another_item_name_is_found_by_unlocked_and_ok():
    proof = certificate(other_item_name(180), ROOT, 'reward')
    assert proof and proof.get('variant') == 'unlocked_word'
    assert abs(proof['regions']['button'][0] - 1666) <= 8 and abs(proof['regions']['button'][1] - 906) <= 8


def test_unlocked_without_ok_is_not_a_reward():
    image = other_item_name(180)
    image[880:960, 1620:1760] = image[700:780, 1620:1760]
    assert certificate(image, ROOT, 'reward') is None
