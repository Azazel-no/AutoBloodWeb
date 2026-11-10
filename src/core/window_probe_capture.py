"""Fresh HWND-bound WGC pixels for read-only tooltips hidden by our panel."""
import threading
import time
import cv2
from src.core.screen_manager import ScreenError


def client_pixels(pixels,window,bounds=()):
    c=window.client
    if pixels.shape[:2]==(c.height,c.width):return pixels
    origins=set()
    for rect in bounds:
        if pixels.shape[:2]!=(rect.height,rect.width):continue
        x,y=c.x-rect.x,c.y-rect.y
        if x>=0 and y>=0 and x+c.width<=rect.width and y+c.height<=rect.height:origins.add((x,y))
    if len(origins)!=1:raise ScreenError('capture_size_mismatch','wgc_client_geometry_unproven')
    x,y=origins.pop()
    return pixels[y:y+c.height,x:x+c.width].copy()


class WindowProbeCapture:
    def __init__(self, window, factory=None, bounds=None):
        if factory is None:
            from windows_capture import WindowsCapture
            factory=WindowsCapture
        self.signature=window.signature
        self.bounds=bounds
        self.condition=threading.Condition()
        self.requested=None;self.result=None;self.closed=False;self.failure=None;self.last_stamp=0
        self.capture=factory(window_hwnd=window.hwnd,cursor_capture=False,draw_border=False,
                             minimum_update_interval=33)
        @self.capture.event
        def on_frame_arrived(frame, control):
            with self.condition:
                if self.requested is None:return
                try:
                    # WGC SystemRelativeTime uses the QPC/monotonic epoch in
                    # 100ns ticks. Arrival time cannot make an old GPU frame fresh.
                    stamp=frame.timespan/10_000_000
                    now=time.monotonic()
                    if not self.requested<=stamp<=now or now-stamp>.5 or stamp<=self.last_stamp:return
                    pixels=cv2.cvtColor(frame.frame_buffer,cv2.COLOR_BGRA2BGR)
                    self.result=(pixels,stamp);self.last_stamp=stamp;self.requested=None
                except Exception as exc:
                    self.failure=exc;self.requested=None
                self.condition.notify_all()
        @self.capture.event
        def on_closed():
            with self.condition:self.closed=True;self.condition.notify_all()
        # Arm the first request before starting WGC. A static window may emit
        # its only initial update during start_free_threaded(). Discarding it
        # and waiting for another update used to time out unnecessarily.
        self.control=None

    def grab(self,window,timeout=1.5):
        if window.signature!=self.signature:raise ScreenError('window_changed')
        until=time.monotonic()+timeout
        with self.condition:
            self.result=None;self.requested=time.monotonic()
            if not self.closed and self.control is None:
                try:self.control=self.capture.start_free_threaded()
                except Exception as exc:self.failure=exc
            while self.result is None and not self.closed and self.failure is None:
                remaining=until-time.monotonic()
                if remaining<=0:break
                self.condition.wait(min(.05,remaining))
            self.requested=None
            if self.failure:raise ScreenError('probe_capture_failed',str(self.failure))
            if self.closed or self.result is None:raise ScreenError('probe_capture_failed','fresh_wgc_frame_timeout')
            pixels,stamp=self.result;self.result=None
        return client_pixels(pixels,window,self.bounds(window) if self.bounds else ()),stamp

    def close(self):
        with self.condition:
            self.closed=True;self.requested=None;self.condition.notify_all()
        if self.capture is None:return
        if self.control is not None:
            self.control.stop()
            if hasattr(self.control,'wait'):self.control.wait()
        # windows-capture's native object owns bound Python callbacks, whose
        # owner in turn owns the native object. stop/wait alone leaves that
        # cross-language cycle alive. Release it only after the thread joined.
        # Repeated start/stop previously retained D3D/WGC sessions until the
        # process failed. The dependency is pinned to the reviewed wrapper.
        capture=self.capture
        if hasattr(capture,'frame_handler'):capture.frame_handler=None
        if hasattr(capture,'closed_handler'):capture.closed_handler=None
        if hasattr(capture,'capture'):capture.capture=None
        self.result=None;self.capture=None;self.control=None
