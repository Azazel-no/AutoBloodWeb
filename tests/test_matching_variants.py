import cv2
import numpy as np
import pytest
from src.core.node_classifier import IconMatcher
from src.utils.images import read_image


@pytest.fixture(scope='module')
def matcher(library): return IconMatcher(library)


@pytest.fixture(scope='module')
def patches(root,detector):
    # Exercise actual Bloodweb interiors, not inventory PNGs with dropped alpha.
    image=read_image(root/'references/bloodweb/bloodweb_level43_start.png')
    result=detector.analyze(image)
    expected=[(282,362,'survivor_item_020'),(461,409,'survivor_offering_022'),
        (219,470,'survivor_addon_001'),(357,469,'survivor_addon_009'),
        (567,469,'survivor_offering_044'),(283,703,'survivor_item_003')]
    patches={}
    for x,y,ident in expected:
        node=min(result.nodes,key=lambda n:np.hypot(n.x-x,n.y-y))
        radius=max(node.radius,round(image.shape[0]*.047*.9))
        patches[ident]=(image[node.y-radius:node.y+radius+1,
            node.x-radius:node.x+radius+1].copy(),radius)
    return patches


@pytest.mark.parametrize('ident',['survivor_item_003','survivor_item_020','survivor_addon_001',
    'survivor_offering_022','survivor_addon_009','survivor_offering_044'])
@pytest.mark.parametrize('angle,scale,brightness',[(0,1,1),(-6,.94,.65),(6,1.06,.8)])
def test_real_icon_variants_are_correct_or_explicitly_unknown(ident,angle,scale,brightness,matcher,patches):
    raw,radius=patches[ident]
    transform=cv2.getRotationMatrix2D((radius,radius),angle,scale)
    raw=cv2.warpAffine(raw,transform,(raw.shape[1],raw.shape[0]),flags=cv2.INTER_LINEAR,borderValue=0)
    raw=np.round(raw*brightness).astype(np.uint8)
    result=matcher.match(raw,radius)
    assert result.icon_id in (None,ident),(ident,result)
    assert result.candidates and len(result.candidates)<=3
    if angle==0: assert result.icon_id==ident


def test_color_alone_never_identifies_an_item(matcher):
    for color in ((40,160,40),(20,90,150),(110,20,120)):
        image=np.full((96,96,3),color,np.uint8)
        assert matcher.match(image,64).icon_id is None
