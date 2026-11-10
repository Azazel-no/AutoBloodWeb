import time
import cv2
import numpy as np
import pytest
from src.core.screen_manager import Rect,WindowInfo,CapturedFrame,ScreenManager,ScreenError
from src.utils.images import read_image


def test_cloaked_helper_is_not_a_desktop_occlusion():
    import ctypes
    from contextlib import nullcontext
    from src.core.screen_manager import WindowsBackend
    backend=object.__new__(WindowsBackend)
    class User:
        def IsIconic(self, hwnd): return False
        def GetWindowThreadProcessId(self, hwnd, pointer): return 0
        def GetWindowTextW(self, hwnd, buffer, size): return 0
        def GetClassNameW(self, hwnd, buffer, size): return 0
        def GetWindowRect(self, hwnd, pointer):
            rect=pointer._obj
            rect.left=rect.top=0; rect.right=rect.bottom=1
            return True
    backend.u=User()
    backend.pixels=lambda:nullcontext()
    backend.windows=lambda:[2,3,1]
    backend.cloaked=lambda hwnd:hwnd==2
    window=WindowInfo(1,'DBD',Rect(0,0,1920,1080),Rect(0,0,1920,1080),96,True)
    assert backend.occlusions(window)==[Rect(0,0,1,1)]

RESOLUTIONS=[(1366,768),(1600,900),(1920,1080),(2560,1440),(3440,1440),(3840,2160)]


class Backend:
    def __init__(self,window,image=None,covered=()):
        self.window=window; self.image=image; self.covered=list(covered); self.owner=True
    def info(self): return self.window
    def grab(self,rect):
        if self.image is None: raise ScreenError('capture_failed')
        return self.image.copy(),'fake_live'
    def occlusions(self,window): return self.covered
    def owns_point(self,window,point): return self.owner


@pytest.mark.parametrize('size',RESOLUTIONS)
@pytest.mark.parametrize('dpi',[96,120,144,192])
@pytest.mark.parametrize('origin',[(0,0),(-3840,-400),(250,180)])
def test_physical_coordinates_do_not_apply_dpi_twice(size,dpi,origin):
    w,h=size; x,y=origin
    window=WindowInfo(1,'DBD',Rect(x,y,w,h),Rect(x,y,w,h),dpi,True)
    frame=CapturedFrame(np.zeros((100,200,3),np.uint8),window,Rect(20,30,400,200),time.monotonic())
    assert frame.to_screen(100,50)==(x+220,y+130)
    manager=ScreenManager(Backend(window))
    assert manager.validate_click(frame,100,50)==(x+220,y+130)


@pytest.mark.parametrize('size',RESOLUTIONS)
def test_right_overlay_allows_visible_user_bloodweb(root,size):
    w,h=size; scale=h/1080
    source=read_image(root/'references/bloodweb/bloodweb_level43_start.png')
    source=cv2.resize(source,None,fx=scale,fy=scale)
    image=np.zeros((h,w,3),np.uint8)
    x,y=round(30*scale),round(90*scale)
    image[y:y+source.shape[0],x:x+source.shape[1]]=source
    window=WindowInfo(1,'DBD',Rect(-w,0,w,h),Rect(-w,0,w,h),120,True)
    right=Rect(-w+round(w*.67),0,round(w*.33),h)
    frame=ScreenManager(Backend(window,image,[right])).capture()
    assert frame.live and frame.provider=='fake_live'
    protected=Rect(window.client.x+frame.roi.x,frame.roi.y,frame.roi.width,frame.roi.height)
    assert not protected.intersects(right)
    assert frame.image.std()>10


def test_occluded_web_capture_failure_and_wrong_focus(root):
    image=read_image(root/'references/bloodweb/bloodweb_level43_start.png')
    h,w=image.shape[:2]; window=WindowInfo(1,'DBD',Rect(0,0,w,h),Rect(0,0,w,h),96,True)
    backend=Backend(window,image,[Rect(0,0,w//2,h)])
    with pytest.raises(ScreenError,match='bloodweb_occluded'): ScreenManager(backend).capture()
    with pytest.raises(ScreenError,match='capture_failed'): ScreenManager(Backend(window)).capture()
    frame=CapturedFrame(image,window,Rect(0,0,w,h),time.monotonic())
    backend=Backend(WindowInfo(1,'DBD',window.client,window.monitor,96,False))
    with pytest.raises(ScreenError,match='game_focus_lost'): ScreenManager(backend).validate_click(frame,300,300)


@pytest.mark.parametrize('changed',[Rect(1,0,900,800),Rect(0,0,901,800)])
def test_move_or_resize_invalidates_click(changed):
    window=WindowInfo(1,'DBD',Rect(0,0,900,800),Rect(0,0,1920,1080),96,True)
    frame=CapturedFrame(np.zeros((800,900,3),np.uint8),window,Rect(0,0,900,800),time.monotonic())
    backend=Backend(WindowInfo(1,'DBD',changed,window.monitor,96,True))
    with pytest.raises(ScreenError,match='window_changed'): ScreenManager(backend).validate_click(frame,100,100)


def test_stale_outside_and_covered_click():
    window=WindowInfo(1,'DBD',Rect(0,0,900,800),Rect(0,0,1920,1080),96,True)
    frame=CapturedFrame(np.zeros((800,900,3),np.uint8),window,Rect(0,0,900,800),0)
    with pytest.raises(ScreenError,match='invalid_coordinates'): frame.to_screen(float('nan'),1)
    with pytest.raises(ScreenError,match='stale_frame'): ScreenManager(Backend(window)).validate_click(frame,1,1)
    frame.captured_at=time.monotonic(); backend=Backend(window); backend.owner=False
    with pytest.raises(ScreenError,match='target_occluded'): ScreenManager(backend).validate_click(frame,100,100)
