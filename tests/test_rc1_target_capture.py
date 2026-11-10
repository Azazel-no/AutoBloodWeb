from dataclasses import replace
import hashlib
import json
import time
from types import SimpleNamespace
import pytest
from src.core.screen_manager import ScreenManager,Rect,WindowInfo,ScreenError
from src.core.target_evidence import target_observation
from src.utils.images import read_image
from tests.test_screen_manager import Backend
from tests.test_automation import Device,engine,web


def test_native_target_frame_has_fresh_header_anchor_and_no_input_permission(root,monkeypatch):
    record=json.loads((root/'tests/fixtures/rc1/target_capture.json').read_text(encoding='utf-8'))
    path=root/record['source'];assert hashlib.sha256(path.read_bytes()).hexdigest()==record['sha256']
    image=read_image(path);h,w=image.shape[:2]
    window=WindowInfo(42,'DBD',Rect(0,0,w,h),Rect(0,0,w,h),96,True,33)
    b=Backend(window,image);screen=ScreenManager(b);before=screen.capture()
    assert before.level==10
    def forbidden(*args,**kwargs):raise AssertionError('Full UI search inside target confirmation')
    monkeypatch.setattr('src.core.video_ui.certificate',forbidden)
    first=screen.capture_confirmation(before);second=screen.capture_confirmation(before)
    assert first.confirmation_only and second.confirmation_only and first.captured_at<second.captured_at
    assert first.level==second.level==10 and first.level_confidence>=.95
    assert first.visibility['confirmation_anchor'] and not first.image.flags.writeable
    with pytest.raises(ScreenError,match='live_frame_required'):screen.validate_click(first,100,100)
    b.covered=[Rect(before.roi.x,before.roi.y,100,100)]
    with pytest.raises(ScreenError,match='bloodweb_occluded'):screen.capture_confirmation(before)


def test_target_header_change_discards_partial_frame_instead_of_reusing_it_for_input():
    d=Device([web()]);e=engine(d,dry_run=False);before=replace(d.capture(),level=10,level_confidence=.99)
    fresh=replace(before,level=11,confirmation_only=True,captured_at=time.monotonic(),visibility={'confirmation_anchor':True})
    d.library=SimpleNamespace();d.capture_confirmation=lambda old:fresh
    e.opening.active=True;e.opening.features={1:None}
    e.capture_frame=lambda **kwargs:fresh
    assert target_observation(e,before,web().nodes[1]) is None
    assert getattr(e,'confirmation_capture',None) is None and not d.presses


def test_short_animation_retry_is_read_only_and_cannot_wait_past_its_bound():
    d=Device([web()]);e=engine(d,dry_run=False);before=replace(d.capture(),level=10,level_confidence=.99)
    fresh=replace(before,confirmation_only=True,captured_at=time.monotonic(),visibility={'confirmation_anchor':True})
    d.library=SimpleNamespace();d.capture_confirmation=lambda old:fresh
    e.opening.active=True;e.opening.features={1:None};e.capture_frame=lambda **kwargs:fresh
    e.target_probe_until=time.monotonic()+.5
    assert target_observation(e,before,web().nodes[1])==(fresh,None,None)
    assert not e.recent_frames and not d.presses
    e.target_probe_until=time.monotonic()-1
    assert target_observation(e,before,web().nodes[1]) is None
