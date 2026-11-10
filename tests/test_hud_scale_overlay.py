"""DS timer and overlay size at other HUD scales; OCR restart."""
import queue
from pathlib import Path
from types import SimpleNamespace as N

import cv2
import numpy as np
import pytest

from src.core.killer_status import OTHER,column_rect,load_templates,read_column
from src.core.match_reader import fit_hud,load_layout,with_hud_fit
from src.core.nicknames import NicknameOcr
from src.core.own_perks import crop_own,load_matchers,read_own

ROOT=Path(__file__).resolve().parents[1];S=ROOT/'debug/match_samples';L=load_layout(ROOT)
needs=pytest.mark.skipif(not S.is_dir(),reason='local samples')


def rescaled(image,s):
    """The HUD corners of a killer sample moved as DBD moves them: left column to the bottom left, own perks to the bottom right."""
    h,w=image.shape[:2];small=cv2.resize(image,None,fx=s,fy=s,interpolation=cv2.INTER_AREA);sh,sw=small.shape[:2]
    out=np.full_like(image,30);left=sw//2
    out[1075-round(1075*s):1075-round(1075*s)+sh,:left]=small[:,:left]
    out[h-sh:,w-(sw-left):]=small[:,left:]
    return out


@needs
@pytest.mark.parametrize('s',[.85,.70])
@pytest.mark.parametrize('name',['killer_01_all_healthy','killer_03_slot1_hooked_slot4_injured'])
def test_killer_samples_rescaled(s,name):
    path=next((S/'killer_ds').glob(name[:12]+'*.png'));base=cv2.imread(str(path));image=rescaled(base,s)
    found,shift,confident=fit_hud(image,L);assert abs(found-s)<=.04 and (confident or 'hooked' in name)  # Dim names on a bad frame: the fit just retries later.
    fitted=with_hud_fit(L,found,shift);h,w=image.shape[:2];x,y,cw,ch=column_rect(fitted,w,h);t=load_templates(ROOT)
    statuses,visible=read_column(image[y:y+ch,x:x+cw],fitted,w,h,t);assert visible
    x0,y0,cw0,ch0=column_rect(L,w,h);expected,_=read_column(base[y0:y0+ch0,x0:x0+cw0],L,w,h,t)
    assert statuses==expected
    assert read_own(*crop_own(image,found),load_matchers(ROOT),found)[0]


@needs
@pytest.mark.parametrize('name,scale',[('uiscale/hud_uiscale_small_01',.84),('uiscale/hud_uiscale_smaller_02',.69)])
def test_own_perks_found_at_small_hud(name,scale):
    assert read_own(*crop_own(cv2.imread(str(S/(name+'.png'))),scale),load_matchers(ROOT),scale)[0]


@pytest.fixture(scope='module')
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('position',['left','right'])
def test_cluster_centre_stays_and_size_is_limited(app,position):
    from src.gui.match_overlay import PerkOverlay
    o=PerkOverlay(ROOT);o.layout_data=L;o.client=N(x=0,y=0,width=1920,height=1080);o.position=position;o.size_name='normal';h=L['hud'];seen=[]
    for scale in (.5,1.,1.5,2.):
        o.scale=scale;(c,size,centres)=o.cluster_geometry(1);seen.append((c,size))
        left=min(x for x,_ in centres.values())-size/2;right=max(x for x,_ in centres.values())+size/2
        assert right<=h['portrait_x']-h['portrait_radius']+1 if position=='left' else left>=h['portrait_x']+h['portrait_radius']-1
    assert len({c for c,_ in seen})==1 and seen[0][1]<seen[1][1]<=seen[2][1]<=seen[3][1]


def test_a_new_helper_ignores_the_old_helpers_end_marker(tmp_path):
    class Fake:
        def __init__(self,lines):self.stdout=iter(lines);self.stdin=None
        def kill(self):pass
    ocr=NicknameOcr(tmp_path,popen=lambda *a,**k:Fake(['{"ready":true,"languages":["en-US"]}\n']))
    ocr.lines.put('')  # What the killed helper's reader leaves behind.
    ocr._start();assert ocr.ready and ocr.languages==['en-US']
