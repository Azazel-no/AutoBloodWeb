from collections import Counter
import cv2
import numpy as np

from src.core.icon_library import IconLibrary, extract_boxes, source_group
from src.utils.images import read_image


COUNTS={
    'survivor_items_01.png':15, 'survivor_items_02.png':7, 'survivor_addons_01.png':11,
    'survivor_offerings_01.png':15,'survivor_offerings_02.png':15,
    'survivor_offerings_03.png':15,'survivor_offerings_04.png':14,
    'killer_blight_addons_01.png':15,'- killer_blight_addons_02.png':5,
    'killer_offerings_01.png':15,'killer_offerings_02.png':15,
    'killer_offerings_03.png':15,'killer_offerings_04.png':8,
}


def test_actual_inventory_grids(root):
    for name,expected in COUNTS.items():
        path=root/'references/inventory'/name
        _,category,_,_=source_group(path)
        image=read_image(path)
        boxes=extract_boxes(image,category=='offerings')
        assert len(boxes)==expected,name
        for x,y,w,h in boxes:
            assert x>=0 and y>=0 and x+w<=image.shape[1] and y+h<=image.shape[0]


def test_catalog_and_cache(library):
    legacy=[e for e in library.entries if e.source.startswith('references/inventory/')]
    assert len(legacy)==165
    assert len({e.id for e in library.entries})==len(library.entries)
    assert Counter(e.source.split('/')[-1] for e in legacy)==COUNTS
    before=library.manifest_path.stat().st_mtime_ns
    loaded=IconLibrary(library.root).build()
    assert not loaded.rebuilt
    assert loaded.manifest_path.stat().st_mtime_ns==before
    for e in loaded.entries:
        png=cv2.imdecode(np.fromfile(library.root/e.image,np.uint8),cv2.IMREAD_UNCHANGED)
        assert png.shape==(96,96,4)
        assert np.mean(png[:,:,3]==0)>.1
        if e.source.startswith('references/inventory/'):
            assert np.mean(png[:,:,3]>0)>.5


def test_roles_and_character_extension(library):
    assert len(library.filter('survivor'))==145  # 112 preserved + 33 verified event role entries.
    assert len(library.filter('killer'))==89
    assert len(library.filter('killer','addons','blight'))==21
    assert all(e.character is None for e in library.filter('killer','addons','other_character'))
