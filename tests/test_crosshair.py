"""Crosshair: presets, centring, and only inside a match."""
from pathlib import Path
from types import SimpleNamespace as N

import cv2
import numpy as np
import pytest
from PySide6.QtGui import QColor,QImage,QPainter
from PySide6.QtWidgets import QApplication

from src.core.crosshair import PRESETS,centre,draw,extent,preset_config
from src.core.match_reader import load_layout
from src.core.preferences import Preferences
from src.gui.crosshair_overlay import in_match,valid_key

ROOT=Path(__file__).resolve().parents[1];SAMPLES=ROOT/'debug/match_samples';needs=pytest.mark.skipif(not SAMPLES.is_dir(),reason='local samples')


@pytest.mark.parametrize('name',list(PRESETS))
@pytest.mark.parametrize('length',[1,8,40])
def test_every_preset_draws_inside_its_window(name,length):
    QApplication.instance() or QApplication([])
    c=preset_config(name,dict(length=length,color='#ff0000'));half=round(extent(c));img=QImage(half*2,half*2,QImage.Format_ARGB32);img.fill(0)
    p=QPainter(img);draw(p,half,half,c);p.end()
    data=np.frombuffer(img.constBits(),np.uint8).reshape(half*2,half*2,4)
    assert data[:,:,3].any() and not data[0].any() and not data[-1].any() and not data[:,0].any() and not data[:,-1].any()


def test_fifteen_presets():
    assert 10<=len(PRESETS)<=15


@pytest.mark.parametrize('x,y,w,h,r,expected',[(0,0,1920,1080,1,(960,540)),(100,50,1600,900,1,(900,500)),(0,0,3840,2160,2,(960,540)),(300,150,2560,1440,1.25,(1264,696))])
def test_centre_uses_the_client_area_and_dpi(x,y,w,h,r,expected):
    assert centre(N(x=x,y=y,width=w,height=h),r)==pytest.approx(expected)


@needs
@pytest.mark.parametrize('name,expected',[('hud_survivor_1920x1080_01',True),('killer_ds/killer_01_all_healthy',True),('lobby_ready_01',False),('tab_scoreboard_1920x1080_01',False),('ds_over_killer_menu',False),('ds_over_endgame_scoreboard',False)])
def test_only_inside_a_match(name,expected):
    assert in_match(cv2.imread(str(SAMPLES/(name+'.png'))),load_layout(ROOT),ROOT) is expected


def test_off_by_default_and_hotkeys_kept_clear(tmp_path):
    prefs=Preferences(tmp_path/'p.json',[]);assert prefs.settings['crosshair']['enabled'] is False and prefs.settings['crosshair']['key']=='F10'
    assert valid_key('f10')=='F10' and valid_key('F7') is None and valid_key('F8') is None and valid_key('F9') is None


def test_tab_opens_with_real_strings_and_saves_per_preset(tmp_path):
    from src.core.localization import Localization
    from src.gui.crosshair_overlay import CrosshairPage
    QApplication.instance() or QApplication([]);settings=Preferences(tmp_path/'p.json',[]).settings['crosshair'];seen=[]
    page=CrosshairPage(settings,lambda:None,Localization('ru').text);page.changed.connect(seen.append)
    page.sliders['gap'][0].setValue(9)
    assert seen[-1]['gap']==9 and settings['overrides']['classic_gap']['gap']==9 and 'F10' in page.key_label.text()
    page.reset_preset();assert 'classic_gap' not in settings['overrides'] and page.sliders['gap'][0].value()==4
