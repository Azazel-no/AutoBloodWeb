"""Physical-pixel capture and click validation; never reads saved game images."""
from dataclasses import dataclass, asdict
from pathlib import Path
from contextlib import contextmanager
import ctypes
from ctypes import wintypes as W
import time
import math
import logging
import os
from collections import Counter
from src.core.runtime_paths import project_root
import numpy as np
import mss
import cv2


def pixel_variation(image):
    """Population deviation across the same BGR bytes, without float64 copies."""
    return float(cv2.meanStdDev(image.reshape(-1))[1][0,0])


class ScreenError(RuntimeError):
    def __init__(self,code,detail=''):
        super().__init__(code); self.code=code; self.detail=detail


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def contains(self, x, y):
        return self.x <= x < self.x+self.width and self.y <= y < self.y+self.height

    def intersects(self, other):
        return (self.x < other.x+other.width and other.x < self.x+self.width
                and self.y < other.y+other.height and other.y < self.y+self.height)


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    client: Rect
    monitor: Rect
    dpi: int = 96
    foreground: bool = False
    pid: int = 0

    @property
    def signature(self):
        return (self.hwnd, self.pid, self.client, self.monitor, self.dpi)


@dataclass
class CapturedFrame:
    image: np.ndarray
    window: WindowInfo
    roi: Rect
    captured_at: float
    provider: str = 'mss'
    live: bool = True
    bloodpoints: int | None = None
    raw_image: np.ndarray | None = None
    visibility: dict | None = None
    level: int | None = None
    level_confidence: float = 0.0
    probe_only: bool = False
    visuals: object | None = None
    confirmation_only: bool = False

    def to_screen(self, x, y):
        h,w=self.image.shape[:2]
        if not all(math.isfinite(v) for v in (x,y)) or not (0 <= x < w and 0 <= y < h):
            raise ScreenError('invalid_coordinates')
        point=(round(self.window.client.x+self.roi.x+x*self.roi.width/w),
               round(self.window.client.y+self.roi.y+y*self.roi.height/h))
        if not self.window.client.contains(*point):
            raise ScreenError('invalid_coordinates')
        return point

    def metadata(self):
        return dict(window=asdict(self.window), roi=asdict(self.roi),
                    image_size=list(self.image.shape[:2][::-1]), provider=self.provider, live=self.live,bloodpoints=self.bloodpoints, captured_at=self.captured_at,visibility=self.visibility,
                    level=self.level,level_confidence=self.level_confidence,probe_only=self.probe_only,
                    pipeline=self.visuals.summary() if self.visuals is not None else None,confirmation_only=self.confirmation_only)


class MonitorInfo(ctypes.Structure):
    # Defined once: argtypes on a shared DLL object must not be rebound per call,
    # or concurrent callers (overlay key watcher vs GUI) get each other's class.
    _fields_=[('size',W.DWORD),('monitor',W.RECT),('work',W.RECT),('flags',W.DWORD)]


class WindowsBackend:
    def grab_probe(self,window):
        from src.core.window_probe_capture import WindowProbeCapture
        capture=getattr(self,'_probe_capture',None)
        if capture is not None and capture.signature!=window.signature:
            self.close_probe();capture=None
        if capture is None:
            try:self._probe_capture=WindowProbeCapture(window,bounds=self.probe_bounds)
            except (ImportError,RuntimeError) as exc:raise ScreenError('probe_capture_failed',str(exc)) from exc
        image,stamp=self._probe_capture.grab(window)
        return image,'wgc_probe',stamp

    def probe_bounds(self,window):
        """Only exact native outer-frame bounds may define a client crop."""
        bounds=[]
        with self.pixels():
            rect=W.RECT()
            try:
                dwm=ctypes.WinDLL('dwmapi')
                if dwm.DwmGetWindowAttribute(W.HWND(window.hwnd),9,ctypes.byref(rect),ctypes.sizeof(rect))==0:
                    bounds.append(Rect(rect.left,rect.top,rect.right-rect.left,rect.bottom-rect.top))
            except (OSError,AttributeError):pass
            if self.u.GetWindowRect(window.hwnd,ctypes.byref(rect)):
                bounds.append(Rect(rect.left,rect.top,rect.right-rect.left,rect.bottom-rect.top))
        return bounds

    def close_probe(self):
        capture=getattr(self,'_probe_capture',None)
        if capture is not None:capture.close()
        self._probe_capture=None

    def __init__(self):
        self.u=ctypes.WinDLL('user32',use_last_error=True)
        self.g=ctypes.WinDLL('gdi32',use_last_error=True)
        self.u.GetForegroundWindow.restype=W.HWND
        self.u.WindowFromPoint.argtypes=[W.POINT]; self.u.WindowFromPoint.restype=W.HWND
        self.u.GetAncestor.argtypes=[W.HWND,W.UINT]; self.u.GetAncestor.restype=W.HWND
        self.u.MonitorFromWindow.argtypes=[W.HWND,W.DWORD]; self.u.MonitorFromWindow.restype=W.HANDLE
        self.u.GetClientRect.argtypes=[W.HWND,ctypes.POINTER(W.RECT)]
        self.u.GetWindowRect.argtypes=[W.HWND,ctypes.POINTER(W.RECT)]
        self.u.ClientToScreen.argtypes=[W.HWND,ctypes.POINTER(W.POINT)]
        self.u.GetWindowTextW.argtypes=[W.HWND,W.LPWSTR,ctypes.c_int]
        self.u.GetWindowThreadProcessId.argtypes=[W.HWND,ctypes.POINTER(W.DWORD)]
        for name in ('IsWindowVisible','IsIconic','GetDpiForWindow'):
            getattr(self.u,name).argtypes=[W.HWND]
        self.u.SetThreadDpiAwarenessContext.argtypes=[W.HANDLE]
        self.u.SetThreadDpiAwarenessContext.restype=W.HANDLE
        self.u.GetMonitorInfoW.argtypes=[W.HANDLE,ctypes.POINTER(MonitorInfo)]

    def activate(self,window):
        """Bring the game window to the front after Start. Window activation only: no key or click."""
        u=self.u;hwnd=window.hwnd
        for name in ('BringWindowToTop','SetForegroundWindow'):getattr(u,name).argtypes=[W.HWND]
        u.ShowWindow.argtypes=[W.HWND,ctypes.c_int];u.AttachThreadInput.argtypes=[W.DWORD,W.DWORD,W.BOOL]
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        current=kernel.GetCurrentThreadId();foreground=u.GetForegroundWindow()
        other=u.GetWindowThreadProcessId(foreground,None) if foreground else 0
        attached=bool(other and other!=current and u.AttachThreadInput(current,other,True))
        try:
            if u.IsIconic(hwnd):u.ShowWindow(hwnd,9)  # SW_RESTORE
            u.BringWindowToTop(hwnd);ok=bool(u.SetForegroundWindow(hwnd))
        finally:
            if attached:u.AttachThreadInput(current,other,False)
        return ok

    def foreground_handle(self):
        """Sample focus immediately, without enumerating windows or querying processes."""
        return self.u.GetForegroundWindow()

    def access_status(self,window):
        from src.core.process_access import input_access
        return input_access(window.pid)

    @contextmanager
    def pixels(self):
        old=self.u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        if not old: raise ScreenError('dpi_context_failed')
        try: yield
        finally: self.u.SetThreadDpiAwarenessContext(old)

    def windows(self):
        found=[]
        callback_type=ctypes.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
        @callback_type
        def callback(hwnd,param):
            if self.u.IsWindowVisible(hwnd): found.append(hwnd)
            return True
        self.u.EnumWindows.argtypes=[callback_type,W.LPARAM]
        self.u.EnumWindows(callback,0)
        return found

    def game_process(self,hwnd):
        """Verify public executable metadata, without reading process memory."""
        pid=W.DWORD(); self.u.GetWindowThreadProcessId.argtypes=[W.HWND,ctypes.POINTER(W.DWORD)]
        self.u.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD]; kernel.OpenProcess.restype=W.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes=[W.HANDLE,W.DWORD,W.LPWSTR,ctypes.POINTER(W.DWORD)]
        kernel.CloseHandle.argtypes=[W.HANDLE]
        process=kernel.OpenProcess(0x1000,False,pid.value)
        if not process: raise ScreenError('game_process_unverified')
        try:
            buffer=ctypes.create_unicode_buffer(32768); length=W.DWORD(len(buffer))
            if not kernel.QueryFullProcessImageNameW(process,0,buffer,ctypes.byref(length)):
                raise ScreenError('game_process_unverified')
            return buffer.value.replace('\\','/').rsplit('/',1)[-1].casefold()=='deadbydaylight-win64-shipping.exe'
        finally: kernel.CloseHandle(process)

    def info(self):
        with self.pixels():
            for hwnd in self.windows():
                pid=W.DWORD();self.u.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
                if pid.value==os.getpid():continue  # Avoid synchronous WM_GETTEXT to the busy Qt thread.
                title=ctypes.create_unicode_buffer(512); self.u.GetWindowTextW(hwnd,title,512)
                normalized=title.value.casefold().replace(' ','')
                if normalized=='deadbydaylight' or normalized.startswith('deadbydaylight-win64'):
                    if not self.game_process(hwnd): continue
                    if self.u.IsIconic(hwnd): raise ScreenError('game_minimized')
                    r=W.RECT(); p=W.POINT()
                    if not self.u.GetClientRect(hwnd,ctypes.byref(r)) or not self.u.ClientToScreen(hwnd,ctypes.byref(p)):
                        raise ScreenError('client_area_failed')
                    monitor=MonitorInfo(); monitor.size=ctypes.sizeof(monitor)
                    if not self.u.GetMonitorInfoW(self.u.MonitorFromWindow(hwnd,2),ctypes.byref(monitor)):
                        raise ScreenError('monitor_failed')
                    m=monitor.monitor
                    pid=W.DWORD(); self.u.GetWindowThreadProcessId.argtypes=[W.HWND,ctypes.POINTER(W.DWORD)]
                    self.u.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
                    return WindowInfo(hwnd,title.value,Rect(p.x,p.y,r.right,r.bottom),
                        Rect(m.left,m.top,m.right-m.left,m.bottom-m.top),
                        self.u.GetDpiForWindow(hwnd) or 96,self.u.GetForegroundWindow()==hwnd,pid.value)
        raise ScreenError('game_not_found')

    def occlusions(self, window):
        result=[];own=[];details=[]
        with self.pixels():
            for hwnd in self.windows():
                if hwnd==window.hwnd: break
                if self.u.IsIconic(hwnd): continue
                if self.cloaked(hwnd): continue
                r=W.RECT()
                if self.u.GetWindowRect(hwnd,ctypes.byref(r)):
                    rect=Rect(r.left,r.top,r.right-r.left,r.bottom-r.top)
                    if rect.intersects(window.client):
                        result.append(rect)
                        pid=W.DWORD();self.u.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
                        title=ctypes.create_unicode_buffer(120);kind=ctypes.create_unicode_buffer(80)
                        self.u.GetWindowTextW(hwnd,title,120);self.u.GetClassNameW(hwnd,kind,80)
                        details.append(dict(rect=rect,title=title.value,cls=kind.value,pid=pid.value))
                        # Another app's leftover tooltip (live: an invisible CapCut one) is masked like our panel.
                        if pid.value==os.getpid() or 'tooltip' in kind.value.lower():own.append(rect)
        # Our own panel's pixels are still blacked out; it only must not fail
        # the region check, since every click separately checks its point.
        self.own_occlusions=own;self.occluders=details
        return result

    def cloaked(self, hwnd):
        # Visible helper windows may be excluded from desktop composition by
        # DWM. Their bounding rectangles do not cover any captured pixels.
        value=W.DWORD()
        try:
            dwm=ctypes.WinDLL('dwmapi')
            return dwm.DwmGetWindowAttribute(W.HWND(hwnd),14,ctypes.byref(value),
                                           ctypes.sizeof(value))==0 and bool(value.value)
        except (OSError,AttributeError):
            return False  # An unavailable compositor query never hides a cover.

    def owns_point(self, window, point):
        with self.pixels():
            return self.u.GetAncestor(self.u.WindowFromPoint(W.POINT(*point)),2)==window.hwnd

    def grab(self, rect):
        with self.pixels():
            try:
                with mss.MSS() as screen:
                    image=cv2.cvtColor(np.asarray(screen.grab(dict(left=rect.x,top=rect.y,width=rect.width,height=rect.height))),cv2.COLOR_BGRA2BGR)
                if pixel_variation(image)>2: return image,'mss'
            except Exception as exc:
                logging.getLogger(__name__).warning('MSS failed; trying desktop GDI: %s',exc)
            # Desktop GDI fallback. This also captures visible pixels, not hidden game contents.
            u,g=self.u,self.g
            u.GetDC.argtypes=[W.HWND]; u.GetDC.restype=W.HDC
            u.ReleaseDC.argtypes=[W.HWND,W.HDC]
            g.CreateCompatibleDC.argtypes=[W.HDC]; g.CreateCompatibleDC.restype=W.HDC
            g.CreateCompatibleBitmap.argtypes=[W.HDC,ctypes.c_int,ctypes.c_int]; g.CreateCompatibleBitmap.restype=W.HBITMAP
            g.SelectObject.argtypes=[W.HDC,W.HGDIOBJ]; g.SelectObject.restype=W.HGDIOBJ
            g.BitBlt.argtypes=[W.HDC,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,W.HDC,ctypes.c_int,ctypes.c_int,W.DWORD]
            g.DeleteObject.argtypes=[W.HGDIOBJ]; g.DeleteDC.argtypes=[W.HDC]
            class Header(ctypes.Structure):
                _fields_=[('size',W.DWORD),('width',W.LONG),('height',W.LONG),('planes',W.WORD),('bits',W.WORD),
                          ('compression',W.DWORD),('image_size',W.DWORD),('x',W.LONG),('y',W.LONG),('used',W.DWORD),('important',W.DWORD)]
            dc=u.GetDC(None); memory=g.CreateCompatibleDC(dc); bitmap=g.CreateCompatibleBitmap(dc,rect.width,rect.height)
            old=g.SelectObject(memory,bitmap)
            try:
                if not g.BitBlt(memory,0,0,rect.width,rect.height,dc,rect.x,rect.y,0x40CC0020):
                    raise ScreenError('capture_failed',f'BitBlt: {ctypes.get_last_error()}')
                g.SelectObject(memory,old); old=None
                header=Header(); header.size=40; header.width=rect.width; header.height=-rect.height; header.planes=1; header.bits=32
                pixels=ctypes.create_string_buffer(rect.width*rect.height*4)
                g.GetDIBits.argtypes=[W.HDC,W.HBITMAP,W.UINT,W.UINT,ctypes.c_void_p,ctypes.c_void_p,W.UINT]
                if not g.GetDIBits(memory,bitmap,0,rect.height,pixels,ctypes.byref(header),0): raise ScreenError('capture_failed')
                image=cv2.cvtColor(np.frombuffer(pixels,dtype=np.uint8).reshape(rect.height,rect.width,4),cv2.COLOR_BGRA2BGR)
                if pixel_variation(image)<=2: raise ScreenError('capture_black_frame')
                return image,'gdi'
            finally:
                if old: g.SelectObject(memory,old)
                g.DeleteObject(bitmap); g.DeleteDC(memory); u.ReleaseDC(None,dc)


NATIVE_MISSES=4   # native-only negative answers in a row before one full centre search


class ScreenManager:
    def __init__(self, backend=None, detector=None, clock=time.monotonic):
        self.backend=backend or WindowsBackend(); self.detector=detector; self.clock=clock
        self._region=None
        self.last_raw=None
        self.last_capture=None
        self.pipeline_counts=Counter()
        if detector:
            from src.core.resources import ResourceReader
            self.resources=ResourceReader(detector.library.root)
        else: self.resources=None

    def capture(self):
        started=time.perf_counter();self.last_timings={};self.last_intervals={}
        self._current_visuals=None
        try:return self._capture()
        finally:
            total=time.perf_counter()-started
            self.last_timings['capture_other']=max(0,total-sum(self.last_timings.values()))
            self.last_timings['capture_total']=total
            context=self._current_visuals
            self.pipeline_counts[context.mode if context else 'LEGACY_CAPTURE']+=1
            if context:
                context.capture_seconds=total
                self.pipeline_counts.update(context.stats)

    def _timing(self,name,started):
        ended=time.perf_counter()
        self.last_timings[name]=ended-started
        self.last_intervals[name]=dict(started_at=started,ended_at=ended)

    def _capture(self):
        from src.core.bloodweb_detector import find_center
        import cv2
        self.last_raw=None; self.last_capture=None
        self.capture_status=dict(window_found=False,frame_received=False)
        started=time.perf_counter();window=self.backend.info(); client=window.client
        self._timing('capture_window',started)
        self.capture_status.update(window_found=True,window=asdict(window))
        started=time.perf_counter();image,provider=self.backend.grab(client)
        self._timing('capture_pixels',started)
        self.last_raw=image
        self.last_capture=dict(window=asdict(window),provider=provider)
        self.capture_status.update(frame_received=True,provider=provider)
        captured_at=self.clock()
        self.last_capture['captured_at']=captured_at
        if image.shape[:2]!=(client.height,client.width): raise ScreenError('capture_size_mismatch')
        started=time.perf_counter();occlusions=self.backend.occlusions(window);visible=image.copy() if occlusions else image
        for r in occlusions:
            x1=max(0,r.x-client.x); y1=max(0,r.y-client.y)
            x2=min(client.width,r.x+r.width-client.x); y2=min(client.height,r.y+r.height-client.y)
            visible[y1:y2,x1:x2]=0
        self._timing('capture_occlusion',started)
        context=None
        if getattr(self,'pipeline_enabled',True) and getattr(self.detector,'frame_pipeline_version',None)==1:
            from src.core.frame_pipeline import FrameVisuals,FULL_SCENE_ANALYSIS
            context=FrameVisuals(visible,window.signature,captured_at,getattr(self,'_scene_generation',0),FULL_SCENE_ANALYSIS,self.detector.library.root)
            self._current_visuals=context
            context.stats.update(masked_full_copies=int(bool(occlusions)),roi_copies=0)
        # Read once from today's masked pixels, before centre lookup. This
        # also avoids checking the prestige wreath on a proven ordinary level.
        from src.core.bloodweb_header import read_level
        started=time.perf_counter();level,level_confidence=read_level(visible,project_root())
        self._timing('level_ocr',started)
        if level is not None:self._scale_strikes=0
        elif self.clock()-getattr(self,'_scale_checked_at',-9.)>=1:
            # At most once a second and three times running: a transition frame must not stop a run.
            self._scale_checked_at=self.clock()
            from src.core.bloodweb_header import unsupported_ui_scale
            found=unsupported_ui_scale(visible,project_root())
            self._scale_strikes=getattr(self,'_scale_strikes',0)+1 if found is not None else 0
            if self._scale_strikes>=3:
                self._scale_strikes=0
                raise ScreenError('ui_scale_unsupported',f'header_scale_{found:.2f}')
        started=time.perf_counter()
        hint=getattr(self,'_center_hint',None)
        # Mid-level, the engine clears this flag; a lost centre anchor sets it again.
        skip_dialog=(not getattr(self,'dialog_check_needed',True) and hint and hint[0]==window.signature and self._region is not None)
        if skip_dialog:tutorial=reward=None
        else:
            tutorial=self.detector.tutorial_certificate(visible,context=context) if context else self.detector.tutorial_certificate(visible) if hasattr(self.detector,'tutorial_certificate') else None
            reward=None if tutorial else self.detector.reward_box(visible,context=context) if context else self.detector.reward_box(visible) if self.detector else None
        dialog=tutorial['regions']['continue'] if tutorial else reward
        if context:context.tutorial=tutorial;context.reward=reward
        if context:context.release_working_arrays()
        self._timing('capture_dialog_check',started)
        if dialog:
            roi=Rect(0,0,client.width,client.height)
            if context:context.mode='SCENE_TRANSITION_CHECK'
        else:
            # An in-game hover card may hide the central anchor. Identify its
            # actual header/body before spending seconds on an impossible
            # multi-scale anchor search; this never supplies click coordinates.
            if self.detector and hasattr(self.detector,'_tooltip_regions'):
                base=round(client.height*(.037 if client.width/client.height>1.5 else .047))
                pane=(round(client.width*(.355 if client.width/client.height>1.5 else .5)),round(client.height*.55))
                started=time.perf_counter();tooltips=self.detector._tooltip_regions(visible,base,pane)
                self._timing('capture_tooltip',started)
                if tooltips:
                    self.last_capture['tooltip_regions']=tooltips
                    raise ScreenError('item_tooltip','central_anchor_or_nodes_covered_by_game_tooltip')
            hint=getattr(self,'_center_hint',None)
            valid_hint=hint and hint[0]==window.signature
            if context:context.mode='FAST_FRAME_CHECK' if valid_hint and self._region else 'FULL_SCENE_ANALYSIS'
            def automatic_hover():
                from src.core.bloodweb_header import automatic_tooltip
                return automatic_tooltip(visible,project_root())
            if not valid_hint and automatic_hover():raise ScreenError('item_tooltip','automatic_unlock_hover_card')
            if valid_hint and any(r.intersects(Rect(client.x+hint[1][0]-hint[1][2],client.y+hint[1][1]-hint[1][2],hint[1][2]*2,hint[1][2]*2)) for r in occlusions):
                raise ScreenError('bloodweb_occluded','central_anchor_covered')
            # A live window whose glyph was found at its own scale: a frame without it (a level
            # transition) is answered from the native scales only; every 4th miss runs the full search.
            native_known=getattr(self,'_native_center',None)==window.signature and getattr(self,'_native_misses',0)<NATIVE_MISSES
            started=time.perf_counter();center=find_center(visible,hint=hint[1] if valid_hint else None,local_only=bool(valid_hint),observed_level=(level,level_confidence),native_only=native_known)
            if context:context.stats['center_local_searches' if valid_hint else 'center_global_searches']=1
            if center is None and valid_hint:
                if context:context.mode='SCENE_TRANSITION_CHECK'
                if automatic_hover():raise ScreenError('item_tooltip','automatic_unlock_hover_card')
                since=getattr(self,'_local_center_missing_since',None)
                if since is None:self._local_center_missing_since=self.clock();since=self.clock()
                if getattr(self,'fast_center_retry',False) and self.clock()-since<4:
                    self._timing('capture_center',started)
                    # During a certified transition/automatic-hover cleanup,
                    # the old anchor can fade for >1.2s. Keep taking fresh
                    # local captures; a 2s global negative search would stale
                    # this very frame. No input is permitted without a glyph.
                    raise ScreenError('frame_unstable','fresh_center_not_yet_visible')
                center=find_center(visible,observed_level=(level,level_confidence),native_only=native_known)
                if context:context.stats['center_global_searches']=context.stats.get('center_global_searches',0)+1
            from src.core import bloodweb_detector as _detector
            if center is not None:
                self._local_center_missing_since=None;self._native_misses=0
                if _detector.last_center_native and not valid_hint:self._native_center=window.signature   # only a global find at the native scale proves it
            else:
                self.dialog_check_needed=True
                self._native_misses=0 if not native_known else getattr(self,'_native_misses',0)+1
            self._timing('capture_center',started)
            if center is None:
                self._center_hint=None
                from src.core.reward_popups import find_ok_button
                self.popup_scan=(CapturedFrame(visible,window,Rect(0,0,client.width,client.height),captured_at),find_ok_button(visible))
                # A right-side application is not proof that the web is covered.
                # A raw search is only needed to establish an actually covered
                # anchor. Right-side windows and 1px notification overlays cannot
                # cover the permitted Bloodweb search panel. Do not run the same
                # expensive negative template search twice on those pixels.
                pane=Rect(client.x,client.y+round(client.height*.14),round(client.width*(.64 if client.width/client.height>1.5 else 1)),round(client.height*.77))
                raw_center=find_center(image,observed_level=(level,level_confidence)) if any(r.intersects(pane) for r in occlusions) else None
                blocked=raw_center and any(r.intersects(Rect(client.x+raw_center[0]-raw_center[2],client.y+raw_center[1]-raw_center[2],raw_center[2]*2,raw_center[2]*2)) for r in occlusions)
                recent=getattr(self.backend,'_last_visible_web',None)
                if not blocked and recent and recent[0]==window.signature and captured_at-recent[2]<2:
                    region=recent[1]
                    anchor=(client.x+region.x+region.width/2,client.y+region.y+region.height/2)
                    blocked=any(r.contains(*anchor) for r in occlusions)
                self._region=None
                raise ScreenError('bloodweb_occluded' if blocked else 'bloodweb_not_found',
                    'central_anchor_covered' if blocked else 'central_glyph_not_visible')
            cx,cy=map(int,center[:2])
            self._center_hint=(window.signature,center)
            started=time.perf_counter()
            base=float(center[2])
            circles=None
            if not self._region or self._region[0]!=window.signature:
                circles=cv2.HoughCircles(cv2.GaussianBlur(cv2.cvtColor(visible,cv2.COLOR_BGR2GRAY),(5,5),0),
                    cv2.HOUGH_GRADIENT,1.2,max(20,base*1.5),param1=100,param2=28,
                    minRadius=max(10,int(base*.65)),maxRadius=int(base*1.4))
            radii=[] if circles is None else [r for x,y,r in circles[0] if base*1.6<math.hypot(x-cx,y-cy)<base*9]
            # Purchased rings shrink. They must not shrink the capture region
            # and clip still-unpurchased nodes on the outer rim.
            radius=max(base,float(np.quantile(radii,.75)) if radii else base)
            reach=radius*10.5
            x1=max(0,int(cx-reach)); y1=max(0,int(cy-reach)); x2=min(client.width,int(cx+reach)); y2=min(client.height,int(cy+reach))
            roi=Rect(x1,y1,x2-x1,y2-y1)
            if self._region and self._region[0]==window.signature:
                previous=self._region[1]
                if math.hypot(cx-(previous.x+previous.width/2),cy-(previous.y+previous.height/2))<base:
                    roi=previous
                else:
                    self._region=(window.signature,roi)
            else:
                self._region=(window.signature,roi)
            x1,y1=roi.x,roi.y
            protected=Rect(client.x+x1,client.y+y1,roi.width,roi.height)
            own=getattr(self.backend,'own_occlusions',[])
            covering=[r for r in occlusions if r not in own and protected.intersects(r)]
            if covering:
                names=[f"{d['title'] or d['cls']} (pid {d['pid']})" for d in getattr(self.backend,'occluders',[]) if d['rect'] in covering]
                raise ScreenError('bloodweb_occluded',', '.join(names) or 'unknown window')
            self._timing('capture_roi',started)
        if self.backend.info().signature!=window.signature: raise ScreenError('window_changed')
        started=time.perf_counter();bloodpoints=self.resources.read(visible) if self.resources and getattr(self,'read_bloodpoints',True) else None
        self._timing('capture_resources',started)
        self.backend._last_visible_web=(window.signature,roi,self.clock())
        self.last_capture.update(roi=asdict(roi),occlusions=[asdict(r) for r in occlusions],center=list(center) if 'center' in locals() else None)
        if context:
            scene_key=(window.signature,level if level_confidence>=.95 else None)
            if scene_key!=getattr(self,'_pipeline_scene_key',None):
                self._scene_generation=getattr(self,'_scene_generation',0)+1;self._pipeline_scene_key=scene_key
                if not dialog:context.mode='FULL_SCENE_ANALYSIS'
            context.generation=self._scene_generation;context.center=tuple(center) if 'center' in locals() and not dialog else None
            visible.setflags(write=False)
        crop=visible[roi.y:roi.y+roi.height,roi.x:roi.x+roi.width]
        frame=CapturedFrame(crop if context else crop.copy(),window,roi,captured_at,provider,
            bloodpoints=bloodpoints,raw_image=image,visibility=self.last_capture,level=level,level_confidence=level_confidence,visuals=context)
        if context:context.bind(frame)
        return frame

    def capture_overlay(self,frame):
        """Fresh, focus-checked UI evidence. This probe never authorizes clicks."""
        window=self.backend.info()
        if window.signature!=frame.window.signature:raise ScreenError('window_changed')
        if not window.foreground:raise ScreenError('game_focus_lost')
        hidden_probe=hasattr(self.backend,'grab_probe')
        if hidden_probe:
            for attempt in range(2):
                guard=getattr(self,'probe_guard',None)
                if guard:guard()
                current=self.backend.info()
                if current.signature!=window.signature:raise ScreenError('window_changed')
                if not current.foreground:raise ScreenError('game_focus_lost')
                try:
                    image,provider,captured_at=self.backend.grab_probe(window)
                    break
                except ScreenError as exc:
                    if exc.code!='probe_capture_failed':raise
                    # Read-only failure: retire the broken stream once. The
                    # current hover remains guarded; no purchase is retried.
                    close=getattr(self.backend,'close_probe',None)
                    if close:close()
                    recovery=getattr(self,'probe_recovery',None)
                    if recovery:recovery(exc.detail,attempt==1)
                    if attempt==1:
                        # A real visible capture is still useful when the
                        # tooltip is outside the panel. Mask every occlusion;
                        # obscured prices remain unreadable, never invented.
                        hidden_probe=False
                        image,provider=self.backend.grab(window.client);captured_at=self.clock()
        else:image,provider=self.backend.grab(window.client);captured_at=self.clock()
        if image.shape[:2]!=(window.client.height,window.client.width):raise ScreenError('capture_size_mismatch')
        if not 0<=self.clock()-captured_at<=2:raise ScreenError('stale_frame')
        for r in ([] if hidden_probe else self.backend.occlusions(window)):
            x=max(0,r.x-window.client.x);y=max(0,r.y-window.client.y)
            image[y:min(window.client.height,r.y+r.height-window.client.y),x:min(window.client.width,r.x+r.width-window.client.x)]=0
        current=self.backend.info()
        if current.signature!=window.signature:raise ScreenError('window_changed')
        if not current.foreground:raise ScreenError('game_focus_lost')
        return CapturedFrame(image,window,Rect(0,0,window.client.width,window.client.height),captured_at,provider,raw_image=image,probe_only=True)

    def capture_confirmation(self,before):
        """Fresh target evidence only; it can never become an input frame."""
        started=time.perf_counter();self.last_timings={};self.last_intervals={}
        try:
            from src.core.bloodweb_detector import find_center
            from src.core.bloodweb_header import read_level
            tick=time.perf_counter();window=self.backend.info()
            self._timing('capture_window',tick)
            if window.signature!=before.window.signature:raise ScreenError('window_changed')
            if not window.foreground:raise ScreenError('game_focus_lost')
            tick=time.perf_counter();image,provider=self.backend.grab(window.client);stamp=self.clock()
            self._timing('capture_pixels',tick)
            if image.shape[:2]!=(window.client.height,window.client.width):raise ScreenError('capture_size_mismatch')
            r=before.roi;c=window.client
            if min(r.x,r.y)<0 or r.x+r.width>c.width or r.y+r.height>c.height:raise ScreenError('roi_invalid')
            tick=time.perf_counter();covers=self.backend.occlusions(window)
            if any(Rect(c.x+r.x,c.y+r.y,r.width,r.height).intersects(cover) for cover in covers if cover not in getattr(self.backend,'own_occlusions',[])):
                raise ScreenError('bloodweb_occluded')
            visible=image.copy() if covers else image
            for cover in covers:
                x=max(0,cover.x-c.x);y=max(0,cover.y-c.y)
                visible[y:min(c.height,cover.y+cover.height-c.y),x:min(c.width,cover.x+cover.width-c.x)]=0
            self._timing('capture_occlusion',tick)
            root=project_root()
            hint=(before.visibility or {}).get('center')
            tick=time.perf_counter();level,confidence=read_level(visible,root)
            self._timing('level_ocr',tick)
            tick=time.perf_counter();center=find_center(visible,root,hint,local_only=True,observed_level=(level,confidence)) if hint else None
            self._timing('capture_center',tick)
            tick=time.perf_counter();bp=self.resources.read(visible) if self.resources and getattr(self,'read_bloodpoints',True) else None
            self._timing('capture_resources',tick)
            current=self.backend.info()
            if current.signature!=window.signature:raise ScreenError('window_changed')
            if not current.foreground:raise ScreenError('game_focus_lost')
            visibility=dict(window=asdict(window),provider=provider,captured_at=stamp,roi=asdict(r),
                            center=center,confirmation_anchor=center is not None,confirmation_only=True)
            visible.setflags(write=False)
            self.pipeline_counts['TARGET_FRAME_CHECK']+=1
            return CapturedFrame(visible[r.y:r.y+r.height,r.x:r.x+r.width],window,r,stamp,provider,
                bloodpoints=bp,raw_image=image,visibility=visibility,level=level,level_confidence=confidence,confirmation_only=True)
        finally:
            self.last_timings['capture_total']=time.perf_counter()-started

    def reset_region(self):
        self._region=None
        self._center_hint=None

    def reset_layout(self):
        # A new web changes the layout, not the screen coordinate system.
        # The hint narrows an independent glyph match on each fresh capture.
        self._region=None

    def parking_point(self,frame):
        # Only move away from hover cards after an actual purchase. No click.
        current=self.backend.info()
        if current.signature!=frame.window.signature: raise ScreenError('window_changed')
        if not current.foreground: raise ScreenError('game_focus_lost')
        c=current.client; r=frame.roi
        for x,y in ((c.x+8,c.y+c.height//2),(c.x+r.x+ r.width//2,c.y+8)):
            if c.contains(x,y) and self.backend.owns_point(current,(x,y)):
                if not Rect(c.x+r.x,c.y+r.y,r.width,r.height).contains(x,y): return (x,y)
        return None

    def park_hover(self,mouse,stop,guard):
        """Clear a positively detected hover card without a click or clean map.

        Used only during read recovery. The last actual capture supplies window
        context; it never becomes a purchasable frame or a purchase certificate.
        """
        capture=getattr(self,'last_capture',None)
        if not capture or self.clock()-capture.get('captured_at',float('-inf'))>2:return False
        current=self.backend.info();old=capture['window']
        if any(asdict(current)[key]!=old.get(key) for key in ('hwnd','pid','client','monitor','dpi')):raise ScreenError('window_changed')
        point=(current.client.x+8,current.client.y+current.client.height//2)
        def check():
            guard()
            fresh=self.backend.info()
            if fresh.signature!=current.signature:raise ScreenError('window_changed')
            if not fresh.foreground:raise ScreenError('game_focus_lost')
            if hasattr(self.backend,'access_status'):
                access=self.backend.access_status(fresh)
                if not access['allowed']:raise ScreenError(access['reason'])
            if not fresh.client.contains(*point) or not self.backend.owns_point(fresh,point):raise ScreenError('target_occluded')
        check();mouse.release();mouse.park(point,stop,check)
        return True

    def validate_click(self, frame, x, y, require_focus=True, max_age=2):
        if frame.probe_only or frame.confirmation_only:raise ScreenError('live_frame_required','frame is observation only')
        if not frame.live or (max_age is not None and self.clock()-frame.captured_at>max_age): raise ScreenError('stale_frame')
        current=self.backend.info()
        if current.signature!=frame.window.signature: raise ScreenError('window_changed')
        if require_focus and not current.foreground: raise ScreenError('game_focus_lost')
        if hasattr(self.backend,'access_status'):
            access=self.backend.access_status(current)
            if not access['allowed']: raise ScreenError(access['reason'],str(access))
        point=frame.to_screen(x,y)
        for dx,dy in ((0,0),(-6,-6),(6,6),(-6,6),(6,-6)):
            p=(point[0]+dx,point[1]+dy)
            if not current.client.contains(*p) or not self.backend.owns_point(current,p): raise ScreenError('target_occluded')
        return point
