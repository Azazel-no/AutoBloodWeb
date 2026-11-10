"""Live 7.11 20:08: Cheryl Mason's red-pink sky behind the header kept the level unknown."""
from pathlib import Path

from src.core.bloodweb_header import read_level, read_level_mask
from src.utils.images import read_image

ROOT = Path(__file__).resolve().parents[1]
FRAME = ROOT / 'tests/fixtures/header_bright/cheryl_level48_header.png'


def test_bright_background_header_is_read_by_the_strict_fallback():
    image = read_image(FRAME)
    assert read_level_mask(image, ROOT, None)[0] is None  # The normal mask fails here (label 0.84).
    level, confidence = read_level(image, ROOT)
    assert level == 48 and confidence >= .95


def test_fallback_never_reads_a_level_where_there_is_no_header():
    image = read_image(FRAME)
    image[30:120, 280:760] = 0
    assert read_level(image, ROOT) == (None, 0.) or read_level(image, ROOT)[0] is None


def test_moving_sky_frames_are_all_read():
    """Live 20:32: the fixed mask failed 15 of 16 frames (label 0.82-0.84) at level 50."""
    for path in sorted(FRAME.parent.glob('cheryl_level50_moving_sky_*.png')):
        image = read_image(path)
        assert read_level_mask(image, ROOT, None)[0] is None
        assert read_level(image, ROOT)[0] == 50, path.name
