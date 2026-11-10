"""Original MSS frames: crop height must not enlarge the native node scale."""
import hashlib,json
import pytest
from dataclasses import asdict
from src.core.bloodweb_detector import BloodwebDetector
from src.core.screen_manager import CapturedFrame,Rect,WindowInfo
from src.core.models import NodeState
from src.core.purchase_outcome import physical_node
from src.utils.images import read_image


@pytest.mark.parametrize('index',[0,1])
def test_large_native_roi_keeps_full_client_glyph_scale_and_sees_pending_red(root,library,index):
    rows=json.loads((root/'references/bloodweb/v55_dark_lord/manifest.json').read_text());row=rows[index]
    path=root/row['path'];assert hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256']
    raw=read_image(path);meta=row['frame'];window=dict(meta['window'])
    window['client']=Rect(**window['client']);window['monitor']=Rect(**window['monitor']);roi=Rect(**meta['roi'])
    frame=CapturedFrame(raw[roi.y:roi.y+roi.height,roi.x:roi.x+roi.width].copy(),WindowInfo(**window),roi,meta['captured_at'],
        raw_image=raw,visibility=meta['visibility'],level=meta['level'],level_confidence=meta['level_confidence'],bloodpoints=meta['bloodpoints'])
    detector=BloodwebDetector(library,'killer','dark_lord');result=detector.analyze_live(frame)
    assert result.details['center_scale_source']=='same_native_capture' and result.details['center'][2]==40
    node=physical_node(frame,result,(443,623),40)
    assert node is not None and node.state==NodeState.PURCHASED and node.state_confidence>=.8
    assert sum(n.state==NodeState.PURCHASED for n in result.nodes)>=8


def test_early_native_crop_includes_faint_outer_node_before_center_budget(root,library):
    from src.core.bloodweb_detector import find_center
    folder=root/'debug/v56/unknown_20261017/price_review_011'
    request=json.loads((folder/'request.json').read_text())
    path=root/request['clean'];assert hashlib.sha256(path.read_bytes()).hexdigest()==request['clean_sha256']
    raw=read_image(path);key=request['scene'];roi=Rect(**key['roi']);s=key['signature']
    window=WindowInfo(s['hwnd'],'DeadByDaylight',Rect(**s['client']),Rect(**s['monitor']),s['dpi'],True,s['pid'])
    visibility=dict(captured_at=1.,window=asdict(window),center=find_center(raw,root))
    frame=CapturedFrame(raw[roi.y:roi.y+roi.height,roi.x:roi.x+roi.width].copy(),window,roi,1.,
        raw_image=raw,visibility=visibility,level=4,level_confidence=.99)
    result=BloodwebDetector(library,'killer','unknown').analyze_live(frame)
    assert result.details['same_capture_native_geometry_candidates']>0
    assert len(result.nodes)==6
    found=physical_node(frame,result,(506,390),40)
    assert found is not None and found.state==NodeState.LOCKED and found.geometry_confidence>=.65


@pytest.mark.parametrize('index',[0,1])
def test_current_early_center_has_strict_local_match_on_independent_pixels(root,index):
    from src.core.bloodweb_detector import find_center
    record=json.loads((root/'references/bloodweb/v56_early_center/manifest.json').read_text())
    row=record['holdouts'][index];path=root/row['source']
    assert hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256']
    raw=read_image(path);roi=Rect(**row['frame']['roi']);image=raw[roi.y:roi.y+roi.height,roi.x:roi.x+roi.width]
    hint=(684-roi.x,562-roi.y,40)
    actual=find_center(image,root,hint,local_only=True)
    assert actual is not None and abs(actual[0]-hint[0])<=1 and abs(actual[1]-hint[1])<=1
