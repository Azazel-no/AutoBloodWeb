"""Overlay placement: left of the portrait or right of the nickname."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.match_reader import load_layout,right_centre_x

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('height,width',[(1080,1920),(1440,2560),(2160,3840),(900,1600)])
@pytest.mark.parametrize('size',['normal','large'])
def test_right_cluster_starts_after_the_nicknames_and_clears_the_portrait(app,height,width,size):
    from src.gui.match_overlay import PerkOverlay
    layout=load_layout(ROOT);hud=layout['hud'];o=PerkOverlay(ROOT);o.layout_data=layout;o.size_name=size
    o.client=SimpleNamespace(x=0,y=0,width=width,height=height);o.position='right';scale=height/1080
    for slot,y in enumerate(hud['portrait_y']):
        (cx,cy),d,centres=o.cluster_geometry(slot)
        left=min(x for x,_ in centres.values())-d/2
        assert left>=(hud['right_x']-1)*scale and left>(hud['portrait_x']+hud['portrait_radius'])*scale
        assert cy==pytest.approx(y*scale,abs=1)
    o.position='left';(cx,_),_,_=o.cluster_geometry(0)
    assert cx<hud['portrait_x']*scale


def test_right_timer_uses_the_same_column_rule():
    layout=load_layout(ROOT)
    assert right_centre_x(layout,30)-30>layout['hud']['right_x']


def test_no_overlay_window_is_the_size_of_the_whole_game():
    """J4 (14.11): NVIDIA recording turned itself off while a monitor-sized topmost overlay was up."""
    from types import SimpleNamespace
    from src.gui.match_overlay import hud_overlay_rect
    for width, height, ratio in ((1920, 1080, 1.), (2560, 1440, 1.25), (3840, 2160, 1.5)):
        client = SimpleNamespace(x=0, y=0, width=width, height=height)
        x, y, w, h = hud_overlay_rect(client, ratio)
        assert w * ratio <= width * .5 + 1 and h * ratio == pytest.approx(height, abs=1)
    import inspect
    from src.gui import sable_overlay
    assert 'client.height - 2' in inspect.getsource(sable_overlay.SableWindow.present)
