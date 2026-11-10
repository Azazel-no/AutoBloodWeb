"""Native CONTINUE from two different notifications; no game input in tests."""
import copy,json,hashlib,time
from dataclasses import replace
import pytest
from src.core.video_ui import certificate
from src.core.bloodweb_detector import BloodwebDetector
from src.core.models import ScreenState
from src.core.modal_recovery import tutorial_target,dismiss_tutorial
from src.utils.images import read_image
from tests.test_v54_tutorial import setup,next_web,tutorial
from tests.test_gui import app


def test_continue_native_different_title_and_level(root,library,monkeypatch):
    from src.core.screen_manager import ScreenManager,WindowInfo,Rect
    item=json.loads((root/'tests/fixtures/v54/continue_perk_slot_ii.json').read_text(encoding='utf-8'))
    path=root/item['source'];assert hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256']
    pixels=read_image(path);detector=BloodwebDetector(library,'killer','dark_lord')
    class Backend:
        def info(self):return WindowInfo(1,'DBD',Rect(0,0,1920,1080),Rect(0,0,1920,1080),96,True)
        def grab(self,rect):return pixels.copy(),'native_holdout'
        def occlusions(self,window):return []
    def forbidden(*a,**k):raise AssertionError('No centre search on CONTINUE')
    monkeypatch.setattr('src.core.bloodweb_detector.find_center',forbidden)
    frame=ScreenManager(backend=Backend(),detector=detector).capture();result=detector.analyze_live(frame)
    assert frame.level==result.level==5 and result.screen_state==ScreenState.TUTORIAL
    assert result.ok_box==(686,722,101,20) and tutorial_target(frame,result)==(736.5,732.)


@pytest.mark.parametrize('position',[(30,40),(1400,920)])
def test_continue_searches_whole_client_without_title(root,position):
    import numpy as np
    word=read_image(root/'assets/ui/video_v54/continue_word.png');h,w=word.shape[:2]
    # Explicit relocation injection verifies the search area, not physical work.
    pixels=np.zeros((1080,1920,3),dtype='uint8');x,y=position;pixels[y:y+h,x:x+w]=word
    proof=certificate(pixels,root,'continue')
    assert proof and proof['regions']['continue']==(x,y,w,h)


@pytest.mark.parametrize('negative',['absent','partial','ambiguous'])
def test_continue_requires_readable_unambiguous_word(root,negative):
    import numpy as np
    word=read_image(root/'assets/ui/video_v54/continue_word.png');h,w=word.shape[:2]
    pixels=np.zeros((1080,1920,3),dtype='uint8')
    if negative!='absent':pixels[200:200+h,300:300+w]=word
    if negative=='partial':pixels[200:200+h,350:300+w]=0
    if negative=='ambiguous':pixels[600:600+h,800:800+w]=word
    assert certificate(pixels,root,'continue') is None


def generic(level=5):
    result=tutorial();result.level=level;result.level_confidence=0
    result.details['tutorial']=dict(kind='continue',source_sha256='b'*64,
        regions={'continue':result.ok_box},scores={'continue':.99})
    return result


@pytest.mark.parametrize('level',[5,10,None])
def test_continue_command_needs_no_specific_level_or_notification(monkeypatch,level):
    clock,d,r=setup(monkeypatch,[generic(level),generic(level),next_web(),next_web()])
    frame=d.capture();_,web,_=dismiss_tutorial(r,frame,generic(level))
    assert len(d.presses)==r.attempts==1 and r.actions==0 and web.screen_state==ScreenState.BLOODWEB


def test_automatic_start_does_not_wait_for_f9_even_with_old_setting(app,library,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from tests.test_automation import Device
    from src.gui.main_window import MainWindow
    from src.gui.session_check_dialog import SessionCheckDialog
    class Keys:
        ready=True
        def __init__(self,*a,**k):pass
        def close(self):pass
        def native_health(self):return dict(ready=True,physical_key_verified=False)
    monkeypatch.setattr('src.gui.main_window.GlobalHotkeys',Keys)
    owner=MainWindow(library,tmp_path/'preferences.json');owner.physical_f9_check.setChecked(True)
    dialog=SessionCheckDialog(owner,auto_start=True);scene=generic();device=Device([scene]);scene.live_frame=device.capture()
    scene.capture_manager=SimpleNamespace(validate_click=lambda *a,**k:None)
    dialog.checked_result(scene)
    assert not dialog.waiting and dialog.receipt['check_kind']=='registration' and not device.presses
    second=copy.copy(scene);second.live_frame=replace(scene.live_frame,captured_at=scene.live_frame.captured_at+.1)
    dialog.checked_result(second)
    assert dialog.succeeded and owner.current_session_check_kind=='registration' and not device.presses
    dialog.close();owner.close()


@pytest.mark.parametrize('forever',[False,True])
def test_first_read_on_new_web_discards_slow_capture_before_any_click(monkeypatch,forever):
    from src.core.screen_manager import ScreenError
    from src.core.models import NodeState
    purchased=next_web();purchased.nodes[1].state=NodeState.PURCHASED
    clock,d,r=setup(monkeypatch,[next_web(),next_web(),next_web(),purchased,purchased])
    r.settings.max_actions=1;r.settings.max_input_commands=3
    original=r.read;calls=0
    def slow():
        nonlocal calls
        calls+=1
        if forever or calls==1:
            assert not d.presses;clock.advance(2.1);raise ScreenError('stale_frame')
        return original()
    r.read=slow;r.run()
    assert r.actions==(0 if forever else 1)
    assert len(d.presses)==(0 if forever else 1)
    assert sum(e['stage']=='stale_frame_discarded' for e in r.report.entries)==(2 if forever else 1)
