"""Scoreboard and HUD found at any DBD UI scale, and why a frame was rejected."""
import glob
from pathlib import Path

import cv2
import pytest

from src.core.hotkey_config import extra_keys_conflict
from src.core.match_reader import Geometry,find_scoreboard,fit_hud,is_scoreboard,load_layout,scoreboard_evidence
from src.core.preferences import Preferences

ROOT=Path(__file__).resolve().parents[1];S=ROOT/'debug/match_samples';L=load_layout(ROOT)
needs=pytest.mark.skipif(not (S/'uiscale').is_dir(),reason='local samples')
img=lambda name:cv2.imread(str(S/(name+'.png')))


@needs
@pytest.mark.parametrize('name',['tab_uiscale_small_01','tab_uiscale_small_02'])
def test_small_scale_scoreboard_rows_and_portrait(name):
    image=img('uiscale/'+name);assert not is_scoreboard(image,L)
    t=find_scoreboard(image,L)['tab']
    assert all(abs(a-b)<=4 for a,b in zip(t['rows_y'],(270,378,486,595)))  # Centres of the perk diamonds.
    assert abs(t['perk_x'][0]-755)<=4


@needs
@pytest.mark.parametrize('name',[p.stem for p in S.glob('*tab_*.png')])
def test_default_scoreboards_still_match_the_plain_layout(name):
    assert find_scoreboard(img(name),L) is L


@needs
def test_nothing_else_is_taken_for_a_scoreboard():
    others=[p for p in [*S.glob('*.png'),*(S/'killer_ds').glob('*.png'),*(S/'uiscale').glob('hud*.png')] if '_tab_' not in p.name and 'tab_score' not in p.name]
    assert len(others)>20 and not [p.name for p in others if find_scoreboard(cv2.imread(str(p)),L)]


@needs
def test_our_window_on_the_right_does_not_hide_the_scoreboard():
    image=img('pair02_tab_1920x1080');image[:,1280:]=0;assert is_scoreboard(image,L)


@needs
@pytest.mark.parametrize('name,scale',[('uiscale/hud_uiscale_small_01',.84),('uiscale/hud_uiscale_smaller_02',.69),('uiscale/hud_uiscale_default_app_window_open',1.),('hud_survivor_1920x1080_01',1.)])
def test_hud_scale(name,scale):
    s,t,confident=fit_hud(img(name),L);assert confident and abs(s-scale)<=.04


@needs
def test_rejection_reason_is_logged():
    report=scoreboard_evidence(img('hud_survivor_1920x1080_01'),L)
    assert set(report)=={'perks','empty','unknown','bands','end_scoreboard'}


def test_keys_cannot_collide():
    b=dict(toggle='F8',stop='F9')
    assert extra_keys_conflict(b,'F8','TAB') and extra_keys_conflict(b,'F10','F10') and not extra_keys_conflict(b,'F10','TAB')


def test_overlay_scale_is_validated(tmp_path):
    (tmp_path/'p.json').write_text('{"settings":{"overlay_scale":999}}',encoding='utf-8')
    assert Preferences(tmp_path/'p.json',[]).settings['overlay_scale']==100


def test_first_launch_is_english(library,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QApplication
    from src.gui import main_window
    QApplication.instance() or QApplication([])
    class Keys:
        ready=True
        def __init__(self,*a,**k):pass
        def close(self):pass
    monkeypatch.setattr(main_window,'GlobalHotkeys',Keys)
    w=main_window.MainWindow(library,tmp_path/'new.json');assert w.locale.language=='en' and w.language.currentData()=='en'
    from types import SimpleNamespace as N
    from src.gui.match_overlay import PerkOverlay
    o=PerkOverlay(ROOT);o.layout_data=L;o.size_name='normal';o.client=N(x=0,y=0,width=1920,height=1080)
    a=o.cluster_geometry(1);o.scale=1.5;b=o.cluster_geometry(1)
    assert a[0]==b[0] and b[1]>a[1]
    w.close()
