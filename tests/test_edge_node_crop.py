"""16.11 live run: Bloodweb stopped with cv2.cvtColor(!_src.empty()) because a node centre sat
closer to the frame edge than its content radius; the negative slice start gave an empty patch."""
import numpy as np
from src.core.bloodweb_detector import square_crop
from src.core.node_category import classify_category


def test_crop_inside_the_frame_is_a_plain_view():
    image = np.arange(100 * 120 * 3, dtype=np.uint8).reshape(100, 120, 3)
    patch = square_crop(image, 60, 50, 10)
    assert patch.shape == (21, 21, 3) and np.shares_memory(patch, image)
    assert (patch == image[40:61, 50:71]).all()


def test_crop_at_every_edge_keeps_full_size():
    image = np.full((100, 120, 3), 200, np.uint8)
    for x, y in ((5, 50), (60, 3), (118, 50), (60, 98), (0, 0), (-4, 50)):
        patch = square_crop(image, x, y, 36)
        assert patch.shape == (73, 73, 3)
        classify_category(patch, 36)   # raised cv2.error on the empty slice before
