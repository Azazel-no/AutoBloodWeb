import threading
import time
from types import SimpleNamespace
from dataclasses import replace
import numpy as np
import pytest
from src.core.window_probe_capture import WindowProbeCapture,client_pixels
from src.core.screen_manager import ScreenManager,ScreenError,CapturedFrame,Rect,WindowInfo
from tests.test_screen_manager import Backend
from tests.test_automation import Device,engine,web


def test_wgc_requires_new_source_time_and_owns_its_pixels():
    class Capture:
        def __init__(self,**kwargs):self.args=kwargs;self.handlers={};self.stopped=False
        def event(self,fn):self.handlers[fn.__name__]=fn;return fn
        def start_free_threaded(self):return self
        def stop(self):self.stopped=True
    w=WindowInfo(42,'DBD',Rect(0,0,20,10),Rect(0,0,20,10),96,True,33)
    stream=WindowProbeCapture(w,Capture);out=[]
    def grab():out.append(stream.grab(w))
    thread=threading.Thread(target=grab);thread.start()
    deadline=time.monotonic()+1
    while stream.requested is None and time.monotonic()<deadline:time.sleep(.001)
    pixels=np.full((10,20,4),100,np.uint8)
    handler=stream.capture.handlers['on_frame_arrived']
    handler(SimpleNamespace(timespan=int((time.monotonic()-1)*10_000_000),frame_buffer=pixels),None)
    assert not out
    stamp=time.monotonic();handler(SimpleNamespace(timespan=int(stamp*10_000_000),frame_buffer=pixels),None)
    thread.join(1);assert not thread.is_alive() and out
    pixels[:]=0
    assert np.all(out[0][0]==100) and abs(out[0][1]-stamp)<.00001
    assert stream.capture.args['window_hwnd']==42 and stream.capture.args['cursor_capture'] is False
    capture=stream.capture
    stream.close();assert capture.stopped and stream.capture is None


def test_wgc_timeout_and_changed_hwnd_fail_closed():
    class Capture:
        def __init__(self,**kwargs):pass
        def event(self,fn):return fn
        def start_free_threaded(self):return self
        def stop(self):pass
    w=WindowInfo(42,'DBD',Rect(0,0,20,10),Rect(0,0,20,10),96,True,33)
    stream=WindowProbeCapture(w,Capture)
    with pytest.raises(ScreenError,match='window_changed'):stream.grab(replace(w,hwnd=43))
    with pytest.raises(ScreenError,match='probe_capture_failed'):stream.grab(w,timeout=.01)
    stream.close()


@pytest.mark.parametrize('defect',[None,'focus','geometry','stale','size'])
def test_hidden_tooltip_pixels_are_probe_only_and_never_bypass_input_occlusion(defect):
    w=WindowInfo(42,'DBD',Rect(0,0,20,10),Rect(0,0,20,10),96,True,33)
    b=Backend(w,np.ones((10,20,3),np.uint8),[Rect(0,0,20,10)])
    clean=CapturedFrame(b.image,w,Rect(0,0,20,10),time.monotonic())
    def grab(window):
        if defect=='focus':b.window=replace(w,foreground=False)
        if defect=='geometry':b.window=replace(w,dpi=192)
        return (np.ones((9 if defect=='size' else 10,20,3),np.uint8),'wgc_probe',
                time.monotonic()-(3 if defect=='stale' else 0))
    b.grab_probe=grab;s=ScreenManager(b)
    if defect:
        with pytest.raises(ScreenError):s.capture_overlay(clean)
    else:
        probe=s.capture_overlay(clean)
        assert probe.probe_only and probe.provider=='wgc_probe' and np.all(probe.image==1)
        with pytest.raises(ScreenError,match='live_frame_required'):s.validate_click(probe,10,5)
        b.owner=False
        with pytest.raises(ScreenError,match='target_occluded'):s.validate_click(clean,10,5)


def test_probe_stream_is_joined_before_input_and_cannot_run_inside_burst():
    d=Device([web()]);e=engine(d,dry_run=False);order=[]
    d.backend=SimpleNamespace(_probe_capture=object())
    def close():order.append('stream_joined');d.backend._probe_capture=None
    d.backend.close_probe=close
    original=d.purchase
    def purchase(*args):
        assert d.backend._probe_capture is None
        order.append('purchase');return original(*args)
    d.purchase=purchase
    f=d.capture();e.purchase_input(f.to_screen(230,200),.05,lambda:None,f,230,200)
    assert order==['stream_joined','purchase']
    e.burst_inputs_active=True;d.backend._probe_capture=object()
    with pytest.raises(ScreenError,match='burst_capture_forbidden'):e.live_safety()


def test_windowed_probe_uses_exact_physical_frame_bounds_without_dpi_scaling():
    w=WindowInfo(42,'DBD',Rect(-100,230,20,10),Rect(-1920,0,1920,1080),192,True,33)
    pixels=np.zeros((40,24,3),np.uint8);pixels[30:40,2:22]=200
    outer=Rect(-102,200,24,40)
    crop=client_pixels(pixels,w,[outer,outer])
    assert crop.shape==(10,20,3) and np.all(crop==200)
    with pytest.raises(ScreenError,match='capture_size_mismatch'):
        client_pixels(pixels,w,[replace(outer,width=25)])
    with pytest.raises(ScreenError,match='capture_size_mismatch'):
        client_pixels(pixels,w,[outer,replace(outer,x=-103)])


def test_first_request_is_armed_before_static_windows_only_initial_frame():
    class Capture:
        def __init__(self,**kwargs):self.handlers={};self.stopped=False
        def event(self,fn):self.handlers[fn.__name__]=fn;return fn
        def start_free_threaded(self):
            frame=SimpleNamespace(timespan=int(time.monotonic()*10_000_000),
                                  frame_buffer=np.ones((10,20,4),np.uint8))
            self.handlers['on_frame_arrived'](frame,None)
            return self
        def stop(self):self.stopped=True
    w=WindowInfo(42,'DBD',Rect(0,0,20,10),Rect(0,0,20,10),96,True,33)
    s=WindowProbeCapture(w,Capture)
    try:
        pixels,stamp=s.grab(w,timeout=.05)
        assert pixels.shape==(10,20,3) and stamp>0
        # The same initial frame cannot satisfy a second independent request.
        with pytest.raises(ScreenError,match='probe_capture_failed'):s.grab(w,timeout=.01)
    finally:s.close()


def test_close_joins_then_breaks_native_bound_callback_cycle_and_is_idempotent():
    order=[]
    class Capture:
        def __init__(self,**kwargs):
            self.capture=SimpleNamespace(callback=self)
            self.frame_handler=None;self.closed_handler=None
        def event(self,fn):setattr(self,'frame_handler' if fn.__name__=='on_frame_arrived' else 'closed_handler',fn);return fn
        def start_free_threaded(self):return self
        def stop(self):order.append('stop');assert self.capture is not None
        def wait(self):order.append('join');assert self.capture is not None
    w=WindowInfo(42,'DBD',Rect(0,0,20,10),Rect(0,0,20,10),96,True,33)
    s=WindowProbeCapture(w,Capture);capture=s.capture
    with pytest.raises(ScreenError):s.grab(w,timeout=.001)
    s.close();s.close()
    assert order==['stop','join']
    assert capture.capture is None and capture.frame_handler is None and capture.closed_handler is None
    assert s.capture is None and s.control is None


@pytest.mark.parametrize('failures',[1,2])
def test_capture_timeout_has_one_fresh_restart_then_masked_visible_fallback(failures):
    w=WindowInfo(42,'DBD',Rect(0,0,20,10),Rect(0,0,20,10),96,True,33)
    b=Backend(w,np.full((10,20,3),200,np.uint8),[Rect(10,0,10,10)])
    calls=[];closes=[];recoveries=[];guards=[]
    def grab(window):
        calls.append(True)
        if len(calls)<=failures:raise ScreenError('probe_capture_failed','fresh_wgc_frame_timeout')
        return b.image.copy(),'wgc_probe',time.monotonic()
    b.grab_probe=grab;b.close_probe=lambda:closes.append(True)
    s=ScreenManager(b);s.probe_guard=lambda:guards.append(True)
    s.probe_recovery=lambda detail,fallback:recoveries.append((detail,fallback))
    f=CapturedFrame(b.image,w,Rect(0,0,20,10),time.monotonic())
    out=s.capture_overlay(f)
    assert len(calls)==2 and len(closes)==failures and len(guards)==2
    assert out.probe_only and out.captured_at>=f.captured_at
    if failures==2:
        assert np.all(out.image[:,:10]==200) and np.all(out.image[:,10:]==0)
        assert recoveries[-1][1] is True
    else:assert out.provider=='wgc_probe' and np.all(out.image==200)


@pytest.mark.parametrize('change',['focus','geometry','stop'])
def test_capture_restart_rechecks_safety_before_requesting_second_frame(change):
    w=WindowInfo(42,'DBD',Rect(0,0,20,10),Rect(0,0,20,10),96,True,33)
    b=Backend(w,np.ones((10,20,3),np.uint8));calls=[]
    def grab(window):
        calls.append(True)
        if change=='focus':b.window=replace(w,foreground=False)
        if change=='geometry':b.window=replace(w,dpi=192)
        raise ScreenError('probe_capture_failed','fresh_wgc_frame_timeout')
    b.grab_probe=grab;b.close_probe=lambda:None;s=ScreenManager(b)
    def guard():
        if calls and change=='stop':raise InterruptedError
    s.probe_guard=guard
    f=CapturedFrame(b.image,w,Rect(0,0,20,10),time.monotonic())
    with pytest.raises(InterruptedError if change=='stop' else ScreenError):s.capture_overlay(f)
    assert len(calls)==1
