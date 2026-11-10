"""Real Dredge level 6 frame: a locked perk's card shows item and path prices."""
import hashlib
import json

from scripts.native_price_v55 import NativePriceLocator, NativeTotalPriceLocator
from src.core.resources import ResourceReader
from src.utils.images import read_image

FRAME = 'tests/fixtures/stage1/locked_perk_two_prices.png'
SHA256 = 'dadbe320a98c2346c57995e23d5fe4d664b7b22be20afdd5e6c02705671a0ae0'
POINT = (746, 328)


def test_locked_perk_card_reads_the_full_path_total_on_the_pinned_frame(root):
    path = root / FRAME
    assert hashlib.sha256(path.read_bytes()).hexdigest() == SHA256
    reader = ResourceReader(root)
    single = NativePriceLocator(root, json.loads((root / 'data/native_price_locator.json').read_text(encoding='utf-8')), reader)
    total = NativeTotalPriceLocator(root, json.loads((root / 'data/native_total_price_locator.json').read_text(encoding='utf-8')), single)
    found = total.locate(read_image(path), POINT)
    assert found['value'] == 5250 and found['badge_score'] >= .96
