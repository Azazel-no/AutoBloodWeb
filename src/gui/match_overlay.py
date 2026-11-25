"""Teammate perk overlay: diamond clusters left of the HUD portraits.

Screen capture only. It reads whether the configured key is held (GetAsyncKeyState,
no hook) while DBD is in front, confirms the scoreboard visually, reads it, and
draws its own click-through window. No input, no game memory, no network. Off
during Bloodweb automation, because the purchase checks treat any window as a cover.
"""
import ctypes
import functools
import json
import logging
import threading
import time
import weakref
from datetime import datetime
from pathlib import Path
import cv2
import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPainterPath, QPolygonF
from PySide6.QtWidgets import QWidget
from src.core.display_mode import exclusive_fullscreen
from src.core.hotkey_config import parse_hotkey
from src.core import match_identity
from src.core.match_reader import EMPTY, SLOTS, Geometry, right_centre_x, hud_portraits, hud_visible, supported_ui_scale, is_end_scoreboard, is_lobby, is_scoreboard, find_scoreboard, fit_hud, with_hud_fit, lobby_evidence, scoreboard_evidence, load_layout, read_tab, score_rows, scoreboard_signature, tab_rows_identity
from src.core.match_state import HudCache, MatchState
from src.core.nicknames import NicknameOcr, hud_strips, name_scores, read_hud_names, read_tab_names
from src.core.screen_manager import ScreenError, WindowsBackend
from src.core.overlay_capture import OverlayBackend

log=logging.getLogger(__name__)
WS_EX=0x00000020|0x00080000|0x08000000|0x00000080  # Transparent, layered, no-activate, tool window.
# J4 (14.11): NVIDIA instant replay/recording switched off while the overlays ran. A topmost window exactly the
# size of the monitor reads as a fullscreen application, and ours is not a game. The HUD overlays now cover only
# the left part of the game, where they draw (the HUD column and the perks beside it).
HUD_OVERLAY_SHARE=.5


def hud_overlay_rect(client,ratio):
    """Logical (x, y, w, h) of a HUD overlay: the client's left half, never the whole monitor."""
    return round(client.x/ratio),round(client.y/ratio),max(1,round(client.width*HUD_OVERLAY_SHARE/ratio)),round(client.height/ratio)
NAMED_KEYS={'TAB':0x09,'CAPSLOCK':0x14,'SHIFT':0x10,'CTRL':0x11,'ALT':0x12,'SPACE':0x20}
OFFSETS={'top':(0,-1),'left':(-1,0),'right':(1,0),'bottom':(0,1)}


def key_code(name):
    key=(name or 'TAB').upper().replace(' ','')
    return NAMED_KEYS[key] if key in NAMED_KEYS else parse_hotkey(key)[1]


class KeyWatcher:
    """Reads one key's state while the game window is in front. Never hooks or sends keys."""
    def __init__(self,vk,is_game_foreground,callback,interval=.05,read=None):
        self.vk=vk;self.is_game_foreground=is_game_foreground;self.callback=callback;self.interval=interval
        user=ctypes.windll.user32
        self.read=read or (lambda code:bool(user.GetAsyncKeyState(code)&0x8000))
        self.stop=threading.Event();self.down=False;self.failures=0;self.backoff=1.
        self.presses=0;self.steps=0;self.away=0  # K1 diagnostics: presses seen in DBD; reads while DBD was not in front
        self.thread=threading.Thread(target=self.run,name='OverlayKey',daemon=True)

    def start(self):self.thread.start()

    def step(self):
        front=self.is_game_foreground();down=front and self.read(self.vk)
        self.steps+=1;self.away+=not front
        if down!=self.down:
            self.down=down;self.presses+=down;self.callback('key_down' if down else 'key_up')

    def stats(self):
        return dict(alive=self.thread.is_alive(),presses=self.presses,dbd_not_in_front=f'{self.away/max(1,self.steps):.0%}',errors=self.failures)

    def restart_stats(self):
        self.presses=0;self.steps=0;self.away=0

    def run(self):
        """Never dies on an error: each new error is logged once, then it waits 1 s and goes on."""
        seen=set()
        while not self.stop.wait(self.interval):
            try:self.step()
            except Exception as exc:
                key=(type(exc).__name__,str(exc)[:120])
                if key not in seen:seen.add(key);log.exception('Overlay key watcher step failed; continuing')
                self.failures+=1;self.stop.wait(self.backoff)

    def close(self):
        self.stop.set()
        if self.thread.is_alive():self.thread.join(1)


_FOREGROUND=None


# J6 (14.11): NVIDIA recording stopped 1-1.5 s after it started, only with AutoBloodWeb running. NVIDIA's log
# (C:\ProgramData\NVIDIA Corporation\ShadowPlay\CaptureCore.log) shows the cause every time: "Capture Proc HDR
# state changed [1] -> [0]", then StopDVRCapture 1.5 s later. With HDR on, any of our windows appearing or
# disappearing over DBD changes how the game is presented, which NVIDIA reads as an HDR change. Starting a
# recording brings NVIDIA's own overlay to the front for a moment, and our overlays hid because "DBD is not in
# front". Two fixes: NVIDIA's overlay (or the Xbox Game Bar) in front counts as DBD in front, and an optional
# recording-friendly mode in which overlays never hide during play, they only go blank.
OVERLAY_PROCESSES=frozenset(('nvidia overlay.exe','nvidia share.exe','gamebar.exe','gamebarftserver.exe'))
GAME_PROCESS_PREFIX='deadbydaylight'


_NAMES={};_NAMES_AT=[0.]


def _process_name(pid):
    """Executable name by process id from the system's process list (Toolhelp snapshot). No process is
    opened, the game's least of all (anticheat): only the list Windows keeps for Task Manager is read."""
    if pid not in _NAMES and time.monotonic()-_NAMES_AT[0]>2.:
        _NAMES_AT[0]=time.monotonic()
        try:
            import ctypes.wintypes as W
            class Entry(ctypes.Structure):
                _fields_=[('dwSize',W.DWORD),('cntUsage',W.DWORD),('th32ProcessID',W.DWORD),('th32DefaultHeapID',ctypes.c_size_t),
                          ('th32ModuleID',W.DWORD),('cntThreads',W.DWORD),('th32ParentProcessID',W.DWORD),('pcPriClassBase',W.LONG),
                          ('dwFlags',W.DWORD),('szExeFile',ctypes.c_wchar*260)]
            k=ctypes.WinDLL('kernel32',use_last_error=True);k.CreateToolhelp32Snapshot.restype=ctypes.c_void_p
            k.CreateToolhelp32Snapshot.argtypes=[W.DWORD,W.DWORD];k.CloseHandle.argtypes=[ctypes.c_void_p]
            k.Process32FirstW.argtypes=k.Process32NextW.argtypes=[ctypes.c_void_p,ctypes.POINTER(Entry)]
            snap=k.CreateToolhelp32Snapshot(0x2,0)   # TH32CS_SNAPPROCESS
            if snap and snap!=ctypes.c_void_p(-1).value:
                try:
                    entry=Entry();entry.dwSize=ctypes.sizeof(Entry);names={}
                    ok=k.Process32FirstW(snap,ctypes.byref(entry))
                    while ok:
                        names[entry.th32ProcessID]=entry.szExeFile.lower();ok=k.Process32NextW(snap,ctypes.byref(entry))
                    _NAMES.clear();_NAMES.update(names)
                finally:k.CloseHandle(snap)
        except Exception:
            pass
    return _NAMES.get(pid,'')


def window_process(hwnd):
    """Lower-case executable name of the process that owns a window ('' when unknown)."""
    if not hwnd:return ''
    u=ctypes.WinDLL('user32',use_last_error=True);u.GetWindowThreadProcessId.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32)]
    pid=ctypes.c_uint32();u.GetWindowThreadProcessId(ctypes.c_void_p(hwnd),ctypes.byref(pid))
    return _process_name(pid.value) if pid.value else ''


def overlay_in_front(hwnd=None):
    """True while a recording/overlay tool (NVIDIA overlay, Game Bar) is the foreground window."""
    hwnd=foreground_handle() if hwnd is None else hwnd
    return window_process(hwnd) in OVERLAY_PROCESSES


def game_in_front(game_hwnd,foreground=None):
    """DBD is in front, or only an overlay tool sits in front of it for a moment."""
    if not game_hwnd:return False
    current=(foreground or foreground_handle)()
    return current==game_hwnd or overlay_in_front(current)


def dbd_or_overlay_in_front():
    name=window_process(foreground_handle())
    return name.startswith(GAME_PROCESS_PREFIX) or name in OVERLAY_PROCESSES


class Recording:
    """Recording-friendly mode (off by default): during play our overlay windows never hide or appear,
    they only go blank, so nothing over DBD changes while NVIDIA records. Automation hides them for real."""
    on=False
    automation=staticmethod(lambda:False)


RECORDING=Recording()
_WINDOWS=weakref.WeakSet()


class Match:
    """J7 (15.11): while a match is on, no overlay window appears or disappears over DBD (any change of our
    windows over the game is an "HDR change" for NVIDIA, which then stops recording). A match is on while any
    overlay saw the match HUD in the last HOLD seconds (Tab, a downed screen or a short HUD gap stay inside
    it) and no lobby was seen since."""
    HOLD=60.
    def __init__(self,clock=time.monotonic):
        self.clock=clock;self.hud_at=None;self.epoch=0
        self.role=None   # L2/L3 (16.11): 'survivor' or 'killer' from the player's own perks, once any overlay read them.

    def note_hud(self):
        if not self.active():self.epoch+=1;self.role=None;log.info('Overlay windows: match on (no show/hide until it ends)')
        self.hud_at=self.clock()

    def note_role(self,role):
        """The role read from the player's own perks (no capture of its own); the first one of a match counts."""
        if role and self.role is None and self.active():
            self.role=role;log.info('Overlay windows: %s match',role)

    def note_lobby(self):
        if self.hud_at is not None:log.info('Overlay windows: match over (lobby)')
        self.hud_at=None;self.role=None

    def active(self):
        return self.hud_at is not None and self.clock()-self.hud_at<=self.HOLD


MATCH=Match()


def keep_mapped():
    """A window over DBD stays mapped (it only goes blank): during a match, or always in recording-friendly mode.
    Bloodweb automation, and DBD not in front, hide windows for real."""
    return (RECORDING.on or MATCH.active()) and not RECORDING.automation() and dbd_or_overlay_in_front()


_SWEEP={}


def role_allows(window):
    """L2/L3: a window made for one role (map: survivor, crosshair: killer) is never mapped in the other's match."""
    roles=getattr(window,'roles',None)
    return roles is None or MATCH.role in roles


def sweep_windows():
    """Once a second, on the GUI thread: blank windows that no longer need to stay mapped are hidden; at the
    start of a match the overlays that were in use are mapped (blank) at once, so none appears later in it.
    A window for one role waits until the match's role is known (about a second) and then maps or stays away."""
    try:keep=keep_mapped()
    except Exception:return
    epoch=MATCH.epoch;start=keep and MATCH.active() and _SWEEP.get('epoch')!=epoch
    if MATCH.active():_SWEEP['epoch']=epoch
    for window in list(_WINDOWS):
        try:
            roles=getattr(window,'roles',None)
            if window.blank and QWidget.isVisible(window) and (not keep or not role_allows(window) and MATCH.role is not None):
                window.blank=False;QWidget.hide(window);window.log_change('hidden (match over)' if not keep else f'hidden ({MATCH.role} match)')
            elif roles is None:
                if start and window.armed and not QWidget.isVisible(window):window.map_blank('match start')
            elif (keep and MATCH.active() and window.armed and not QWidget.isVisible(window) and MATCH.role in roles
                  and getattr(window,'mapped_epoch',None)!=epoch):
                window.mapped_epoch=epoch;window.map_blank(f'{MATCH.role} match start')
        except RuntimeError:pass


def start_sweeper():
    if _SWEEP.get('timer') is None and QGuiApplication.instance() is not None:
        timer=QTimer(QGuiApplication.instance());timer.setInterval(1000);timer.timeout.connect(sweep_windows);timer.start()
        _SWEEP['timer']=timer


def set_recording_friendly(on,automation=None):
    RECORDING.on=bool(on)
    if automation is not None:RECORDING.automation=automation
    sweep_windows()


def hide_all_overlays():
    """Before automation: every overlay window really hidden (they would count as covers of the Bloodweb)."""
    for window in list(_WINDOWS):
        try:
            if QWidget.isVisible(window):window.log_change('hidden (automation)')
            window.blank=False;QWidget.hide(window)
        except RuntimeError:pass


def blank_aware(paint):
    """Wraps a paintEvent: a blank overlay draws nothing, so its translucent window is fully clear."""
    if getattr(paint,'blank_aware',False):return paint
    def paintEvent(self,event):
        if getattr(self,'blank',False):return
        return paint(self,event)
    paintEvent.blank_aware=True
    return paintEvent


def foreground_handle():
    """Handle of the foreground window; a private DLL object, so no argtypes are shared."""
    global _FOREGROUND
    if _FOREGROUND is None:
        user=ctypes.WinDLL('user32',use_last_error=True);user.GetForegroundWindow.restype=ctypes.c_void_p
        _FOREGROUND=user.GetForegroundWindow
    return _FOREGROUND() or 0


def to_qimage(tile):
    rgb=np.ascontiguousarray(cv2.cvtColor(tile,cv2.COLOR_BGR2RGB))
    image=QImage(rgb.data,rgb.shape[1],rgb.shape[0],rgb.strides[0],QImage.Format_RGB888)
    return image.copy()


class PerkOverlay(QWidget):
    def __init__(self,root):
        super().__init__(None,Qt.FramelessWindowHint|Qt.Tool|Qt.WindowStaysOnTopHint|Qt.WindowDoesNotAcceptFocus|Qt.WindowTransparentForInput)
        self.root=Path(root);self.slots={};self.layout_data=None;self.client=None;self.ratio=1.
        self.size_name='large'  # 'normal' or 'large', from assets/ui/match_layout.json hud.sizes
        self.position='left'    # 'left' of the portrait or 'right' of the nickname
        self.scale=1.           # The user's size setting, around each cluster's centre.
        self.setAttribute(Qt.WA_TranslucentBackground);self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents);self.setFocusPolicy(Qt.NoFocus)
        self.reported=False;self.blank=False;_WINDOWS.add(self)
        self.armed=False      # Shown by its controller and not switched off: mapped blank at the next match start.
        self.origin=(0,0)     # Client-relative logical point of the window's top-left (the perk window is small).
        start_sweeper()
        # J5 (14.11): the native window is made now, at app start, with its final styles, not on the first
        # show() in the middle of a match start (the map overlay's first appearance coincided with NVIDIA
        # recording switching off); the first frame on screen already has no-activate/transparent/tool.
        try:self.apply_styles()
        except Exception:logging.getLogger(__name__).exception('Overlay window styles could not be set early')

    def apply_styles(self):
        u=ctypes.windll.user32;hwnd=int(self.winId())
        u.SetWindowLongPtrW.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_ssize_t]
        u.GetWindowLongPtrW.argtypes=[ctypes.c_void_p,ctypes.c_int];u.GetWindowLongPtrW.restype=ctypes.c_ssize_t
        old=u.GetWindowLongPtrW(hwnd,-20)
        if old&WS_EX!=WS_EX:u.SetWindowLongPtrW(hwnd,-20,old|WS_EX)
        return hwnd

    def __init_subclass__(cls,**kwargs):
        super().__init_subclass__(**kwargs)
        if 'paintEvent' in cls.__dict__:cls.paintEvent=blank_aware(cls.__dict__['paintEvent'])

    def show(self):
        from src.core.overlay_capture import windows_allowed
        if not windows_allowed():return self.suppressed()
        if MATCH.role is not None and not role_allows(self):return self.hide()   # Controllers wait for the role; this is the backstop.
        if not QWidget.isVisible(self):self.log_change('shown')
        self.armed=True;self.blank=False;super().show();self.update()

    def hide(self):
        if QWidget.isVisible(self) and keep_mapped():
            if not self.blank:self.blank=True;self.update()
            return
        if QWidget.isVisible(self):self.log_change('hidden')
        self.blank=False;super().hide()

    def suppressed(self):
        """J8 capture test 'no_windows': the overlay reads as usual but its window never appears."""
        if not getattr(self,'_suppressed_logged',False):self._suppressed_logged=True;self.log_change('not shown (capture test: no windows)')

    def map_blank(self,why):
        """Mapped with nothing drawn, so a later show only paints."""
        from src.core.overlay_capture import windows_allowed
        if not windows_allowed():return self.suppressed()
        self.blank=True;QWidget.show(self);self.log_change(f'shown blank ({why})')

    def retire(self):
        """The feature is switched off: hidden for real and not mapped again at match starts."""
        self.armed=False
        if QWidget.isVisible(self):self.log_change('hidden (switched off)')
        self.blank=False;QWidget.hide(self)

    def log_change(self,what):
        """J6: every window appearing or disappearing over the game, with the foreground process, so a
        recording that stops can be matched to the overlay that changed at that moment."""
        try:logging.getLogger(__name__).info('Overlay window %s %s (foreground: %s)',type(self).__name__,what,window_process(foreground_handle()) or '?')
        except Exception:pass

    def isVisible(self):
        return QWidget.isVisible(self) and not self.blank

    def showEvent(self,event):
        super().showEvent(event)
        hwnd=self.apply_styles()
        if not self.reported:
            self.reported=True
            # Measured once Qt has moved the window into place (inside showEvent it still has its default rect).
            QTimer.singleShot(0,lambda:self.safe_report(hwnd))

    def safe_report(self,hwnd):
        try:self.report(hwnd)
        except Exception:pass

    def report(self,hwnd):
        """Once per window: its real size and styles, and whether showing it took the focus (J5 diagnostics)."""
        import ctypes.wintypes as W
        u=ctypes.windll.user32;r=W.RECT();u.GetWindowRect(ctypes.c_void_p(hwnd),ctypes.byref(r))
        u.GetWindowLongPtrW.argtypes=[ctypes.c_void_p,ctypes.c_int];u.GetWindowLongPtrW.restype=ctypes.c_ssize_t
        u.GetForegroundWindow.restype=ctypes.c_void_p
        w,h=r.right-r.left,r.bottom-r.top;sw,sh=u.GetSystemMetrics(0),u.GetSystemMetrics(1)
        log=logging.getLogger(__name__)
        log.info('Overlay window %s: %dx%d at (%d,%d), style 0x%08x, ex-style 0x%08x, screen %dx%d',type(self).__name__,w,h,r.left,r.top,
                 u.GetWindowLongPtrW(hwnd,-16)&0xffffffff,u.GetWindowLongPtrW(hwnd,-20)&0xffffffff,sw,sh)
        if (u.GetForegroundWindow() or 0)==hwnd:log.warning('Overlay window %s took the foreground when shown',type(self).__name__)
        if w>=sw and h>=sh:log.warning('Overlay window %s covers the whole screen',type(self).__name__)

    def present(self,client,layout,slots):
        """client: physical client rect. Qt geometry is logical, hence the device ratio."""
        self.slots={slot:(perks.slots,[to_qimage(t) if t is not None else None for t in perks.tiles]) for slot,perks in slots.items()}
        self.place(client,layout)
        self.show();self.update()

    def place(self,client,layout):
        """J7: the window is only as large as the three clusters (about 100x330 px at 1080p), not half the screen."""
        self.layout_data=layout;self.client=client
        screen=QGuiApplication.screenAt(QPointF(client.x,client.y).toPoint()) or QGuiApplication.primaryScreen()
        self.ratio=r=screen.devicePixelRatio() if screen else 1.
        x0,y0,x1,y1=self.cluster_bounds()
        self.origin=(x0,y0)
        self.setGeometry(round(client.x/r)+x0,round(client.y/r)+y0,x1-x0,y1-y0)

    def cluster_bounds(self,margin=3):
        """Client-relative logical (x0, y0, x1, y1) around every slot's backdrop and diamonds."""
        import math
        xs=[];ys=[]
        for slot in SLOTS:
            (cx,cy),size,centres=self.cluster_geometry(slot);half=min(size*1.15,self.backdrop_limit())
            xs+=[cx-half,cx+half]+[x+s*size/2 for x,_ in centres.values() for s in (-1,1)]
            ys+=[cy-half,cy+half]+[y+s*size/2 for _,y in centres.values() for s in (-1,1)]
        r=self.ratio;width=self.client.width/r;height=self.client.height/r
        x0=max(0,math.floor(min(xs))-margin);y0=max(0,math.floor(min(ys))-margin)
        x1=min(math.ceil(width),math.ceil(max(xs))+margin);y1=min(math.ceil(height),math.ceil(max(ys))+margin)
        return x0,y0,max(x0+1,x1),max(y0+1,y1)

    def clear(self):
        self.slots={};self.hide()

    def cluster_geometry(self,slot):
        """Centre, diamond size and the four diamond centres for one HUD slot, logical px."""
        layout=self.layout_data;h=layout['hud']
        g=Geometry(layout,(self.client.height,self.client.width));r=self.ratio
        preset=h.get('sizes',{}).get(self.size_name,h)
        column=preset['cluster_x'] if self.position=='left' else right_centre_x(layout,round(preset['diamond']*1.15))
        cx,cy=g.hud(column,h['portrait_y'][slot]);size=g.size(preset['diamond'])*self.scale/r;gap=g.size(h['diamond_gap'])*self.scale/r
        cx/=r;cy/=r
        # The centre never moves: at the limit only the size stops growing.
        ys=h['portrait_y'];pitch=min(b-a for a,b in zip(ys,ys[1:]))*g.scale/r;portrait=g.hud(h['portrait_x'],0)[0]/r;radius=g.size(h['portrait_radius'])/r
        room=min(pitch/2-.5,cx if self.position=='left' else self.client.width/r-cx,(portrait-radius-cx) if self.position=='left' else (cx-portrait-radius))
        if size+gap>room>0:
            shrink=room/(size+gap);size*=shrink;gap*=shrink
        step=size/2+gap
        positions={int(k):v for k,v in layout['perk_positions'].items() if k!='note'}
        centres={s:(cx+OFFSETS[positions[s]][0]*step,cy+OFFSETS[positions[s]][1]*step) for s in (1,2,3,4)}
        return (cx,cy),size,centres

    def backdrop_limit(self):
        h=self.layout_data['hud'];ys=h['portrait_y']
        pitch=min(b-a for a,b in zip(ys,ys[1:]))*self.client.height/self.layout_data['reference_height']/self.ratio
        return pitch/2-.5

    @blank_aware
    def paintEvent(self,event):
        if not self.slots or not self.layout_data:return
        painter=QPainter(self);painter.setRenderHint(QPainter.Antialiasing);painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.translate(-self.origin[0],-self.origin[1])
        for slot,(results,images) in self.slots.items():
            (cx,cy),size,centres=self.cluster_geometry(slot)
            half=min(size*1.15,self.backdrop_limit())  # Never into the neighbouring slot.
            painter.setPen(Qt.NoPen);painter.setBrush(QColor(0,0,0,120))
            painter.drawRoundedRect(QRectF(cx-half,cy-half,half*2,half*2),size*.25,size*.25)
            for index,(x,y) in centres.items():
                result=results[index-1];image=images[index-1];d=size/2
                diamond=QPolygonF([QPointF(x,y-d),QPointF(x+d,y),QPointF(x,y+d),QPointF(x-d,y)])
                path=QPainterPath();path.addPolygon(diamond);path.closeSubpath()
                if result.icon_id==EMPTY:
                    painter.setPen(QColor(120,120,120,160));painter.setBrush(Qt.NoBrush);painter.drawPath(path);continue
                if result.icon_id and image is not None:
                    # The captured Tab tile is the game's own art with its rarity colour.
                    painter.save();painter.setClipPath(path)
                    painter.drawImage(QRectF(x-d,y-d,size,size),image);painter.restore()
                    painter.setPen(QColor(0,0,0,200));painter.setBrush(Qt.NoBrush);painter.drawPath(path)
                else:
                    painter.setPen(QColor(200,200,200));painter.setBrush(QColor(60,60,60,220));painter.drawPath(path)
                    painter.setFont(QFont('Segoe UI',max(6,round(size*.38)),QFont.Bold))
                    painter.drawText(QRectF(x-d,y-d,size,size),Qt.AlignCenter,'?')


def guarded(method):
    """Overlay slots never raise: an error is logged once, shown in the status and the overlay keeps going."""
    @functools.wraps(method)
    def wrapper(self,*args,**kwargs):
        try:return method(self,*args,**kwargs)
        except Exception as exc:
            self.failed(method.__name__,exc)
    return wrapper


class MatchOverlayController(QObject):
    """States: off, ready. Status keys go to the GUI; `summary()` describes the overlay.

    Assignment speed: HUD portraits and nickname strips are cached every 0.5 s while
    Tab is closed, so a single Tab press can be matched at once by portrait or by
    nickname (Windows OCR on the nickname strips only, run while the perks are read);
    after Tab closes, fresh HUD frames are scored for up to `followup_seconds`.
    """
    status=Signal(str)
    finished_read=Signal(object,object)
    finished_score=Signal(object)
    finished_identity=Signal(object)
    key_event=Signal(str)
    scoreboard_seconds=1.5  # How long after the key goes down the scoreboard may take to appear.
    followup_seconds=3.     # How long after Tab closes fresh HUD frames are tried.
    error_seconds=10.       # An error is shown this long unless the overlay works again.
    identity_seconds=3.     # K1: while locked, the HUD is compared with the locked match this often (on the poll's frame).
    absent_report=10.       # K1: while the HUD is gone, its lobby evidence is logged this often.

    def __init__(self,root,automation_active,backend=None,matcher_factory=None,overlay=None,key='TAB',key_reader=None,log_dir=None,
                 size='large',ocr=None,foreground_window=None,is_killer=lambda:False,own_reader=None):
        super().__init__()
        self.root=Path(root);self.automation_active=automation_active;self.backend=backend or OverlayBackend()
        self.matcher_factory=matcher_factory;self.overlay=overlay or PerkOverlay(root);self.key=key;self.key_reader=key_reader
        self.overlay.size_name=size
        self.state=MatchState();self.cache=HudCache();self.layout=load_layout(self.root);self.matcher=None;self.window=None
        self.base_layout=self.layout;self.next_tab_search=0.;self.last_reject=None;self.hud_fitted=False;self.hud_ticks=0;self.hud_scale=1.
        self.ocr=ocr if ocr is not None else NicknameOcr(self.root)
        self.own_row=None   # B2 (25.11): src.core.own_row.OwnRowLock shared with the chase meme, set by the main window
        self.foreground_window=foreground_window or foreground_handle
        self.watcher=None;self.mode='off';self.busy=False;self.hud_before=None;self.session=None
        self.game_hwnd=None;self.last_error=None;self.last_error_at=-1e9;self.errors_seen=set();self.restarts=0
        self.log_dir=Path(log_dir) if log_dir else self.root/'debug/overlay'
        self.finished_read.connect(self.read_done);self.finished_score.connect(self.score_done);self.key_event.connect(self.handle_action)
        self.finished_identity.connect(self.identity_done)
        # K1 (15.11): the locked match's identity; a new match unlocks without the lobby.
        self.identity=None;self.identity_busy=False;self.identity_strikes=0;self.identity_unknown=0;self.next_identity=0.
        self.suspect=False;self.tab_check=False;self.away_since=None;self.hud_gone_since=None;self.next_absent_report=0.
        self.presence=QTimer(self);self.presence.setInterval(500);self.presence.timeout.connect(self.poll)
        self.scoreboard_wait=QTimer(self);self.scoreboard_wait.setInterval(50);self.scoreboard_wait.timeout.connect(self.look_for_scoreboard)
        self.followup=QTimer(self);self.followup.setInterval(150);self.followup.timeout.connect(self.followup_tick)
        self.test_hide=QTimer(self);self.test_hide.setSingleShot(True);self.test_hide.timeout.connect(lambda:self.overlay.clear())
        self.wait_until=0.;self.wait_frames=0;self.key_down_at=0.
        self.phase='unknown'  # 'lobby', 'match' (reading perks) or 'locked'; 'unknown' until the first frame.
        self.last_scoreboard=None;self.lobby_ticks=0
        self.is_killer=is_killer;self.own_reader=own_reader;self.own_matchers=None;self.match_role=None

    def read_own(self,image,scale=None):
        """(HUD present, role or None) from the player's own perks."""
        if self.own_reader:return self.own_reader(image)
        scale=self.hud_scale if scale is None else scale
        from src.core.own_perks import crop_own,load_matchers,read_own
        if self.own_matchers is None:self.own_matchers=load_matchers(self.root)
        return read_own(*crop_own(image,scale),{self.match_role:self.own_matchers[self.match_role]} if self.match_role else self.own_matchers,scale)

    def tab_ignored(self,reason):
        """Every ignored press is in the overlay log: a Freddy match (13.11) left no trace of why nothing was read."""
        self.record('tab_ignored',reason=reason,phase=self.phase,role=self.match_role)

    def killer_match(self):
        return self.match_role=='killer' or (self.match_role is None and self.is_killer())

    @property
    def pending(self):
        return self.session['rows'] if self.session else None

    def record(self,event,**details):
        try:
            self.log_dir.mkdir(parents=True,exist_ok=True)
            with open(self.log_dir/f'{datetime.now():%Y%m%d}.jsonl','a',encoding='utf-8') as stream:
                stream.write(json.dumps(dict(time=datetime.now().isoformat(timespec='milliseconds'),event=event,**details),
                                        default=str,ensure_ascii=False)+'\n')
        except OSError:pass

    def failed(self,where,exc):
        key=(where,type(exc).__name__,str(exc)[:120])
        if key not in self.errors_seen:
            self.errors_seen.add(key);log.exception('Overlay %s failed',where)
        self.last_error=f'{where}: {type(exc).__name__}';self.last_error_at=time.monotonic()
        self.record('overlay_error',where=where,error=repr(exc)[:300])
        self.busy=False
        self.status.emit('overlay_error')

    def summary(self):
        """('off'|'error'|'lobby'|'locked'|'reading'|'waiting', teammates shown, nicknames 'on'|'starting'|'off')."""
        names='on' if getattr(self.ocr,'ready',False) else 'off' if getattr(self.ocr,'failed',None) else 'starting'
        if self.mode=='off':return 'off',0,names
        if time.monotonic()-self.last_error_at<self.error_seconds:return 'error',0,names
        if self.phase=='lobby':return 'lobby',0,names
        if self.phase=='locked':return 'locked',len(self.state.slots),names
        if self.phase=='match':return 'reading',len(self.state.slots),names
        return 'waiting',len(self.state.slots),names

    def enter_lobby(self):
        """Lobby or loadout: unlock, hide and forget everything; wait for the next match."""
        if self.phase!='lobby':self.record('lobby_seen',previous=self.phase,key_watcher=self.watcher_stats(restart=True))
        MATCH.note_lobby();self.forget_identity()
        self.phase='lobby';self.reset();self.cache.clear();self.scoreboard_wait.stop();self.match_role=None
        self.hud_fitted=False;self.hud_scale=1.;self.layout=self.base_layout  # The UI scale can change between matches.
        self.status.emit('overlay_lobby')

    def check_lock(self):
        teammates=sum(not r.own for r in self.session['rows']) if self.session else 3
        if self.phase!='locked' and self.state.all_known(teammates):
            self.phase='locked';self.lock_identity()
            self.record('perks_locked',assigned=self.assigned_log(),identity=self.identity_log())
            self.status.emit('overlay_locked')

    # --- K1: a new match is recognised without the lobby ---------------------------------------------
    def watcher_stats(self,restart=False):
        stats=self.watcher.stats() if self.watcher is not None and hasattr(self.watcher,'stats') else None
        if restart and stats is not None:self.watcher.restart_stats()
        return stats

    def forget_identity(self):
        self.identity=None;self.identity_strikes=0;self.identity_unknown=0;self.suspect=False;self.tab_check=False
        if getattr(self,'own_row',None) is not None:self.own_row.reset('perks overlay: new match or match over')

    def share_own_row(self,rows,looks,hud_names):
        """B2: hand the chase meme the evidence of my HUD row: the gold-stripe Tab row's portrait against the
        HUD portraits, and the HUD nicknames (all four rows) against the nickname in the settings."""
        if self.own_row is None:return
        try:
            own=next((r for r in rows if r.own),None)
            if own is not None:self.own_row.observe_tab(own.portrait,looks)
            if hud_names:self.own_row.observe_names(hud_names,looks)
        except Exception:
            log.exception('Own row evidence failed; the chase meme stays on its last answer')

    def lock_identity(self):
        self.identity=match_identity.identity_from_session(self.state.slots,self.session)
        self.identity['epoch']=MATCH.epoch;self.identity_strikes=0;self.identity_unknown=0;self.suspect=False
        self.next_identity=time.monotonic()+self.identity_seconds

    def identity_log(self):
        if not self.identity:return None
        return dict(hud={f'slot{s+1}':v[:2] for s,v in sorted(self.identity['names'].items())},
                    tab={f'row{r+1}':v[:1] for r,v in sorted(self.identity['tab_names'].items())},
                    looks={f'slot{s+1}':len(v) for s,v in sorted(self.identity['looks'].items())})

    def unlock(self,event,**evidence):
        """The locked perks belong to another match: forget them and read the next Tab normally."""
        self.record(event,previous_identity=self.identity_log(),key_watcher=self.watcher_stats(),**evidence)
        log.info('Teammate perks: %s, the previous match\'s perks are cleared',event.replace('_',' '))
        self.reset();self.cache.clear();self.forget_identity();self.phase='match';self.match_role=None
        self.hud_fitted=False;self.hud_scale=1.;self.layout=self.base_layout
        self.status.emit('overlay_reading')

    def match_over(self,why):
        """K1.4: the end-game scoreboard: the perks are cleared at once; Tab does nothing until the next HUD."""
        self.record('match_over',why=why,previous=self.phase,key_watcher=self.watcher_stats(restart=True))
        self.reset();self.cache.clear();self.forget_identity();self.scoreboard_wait.stop();self.phase='ended'

    def check_identity(self,image,force=False):
        """Every few seconds while locked, on the frame poll() already has: nicknames (OCR) and portraits of the HUD."""
        now=time.monotonic()
        if self.identity is None or self.identity_busy or (now<self.next_identity and not force):return
        self.next_identity=now+self.identity_seconds;self.identity_busy=True
        looks=hud_portraits(image,self.layout);strips=hud_strips(image,self.layout) if getattr(self.ocr,'ready',False) else None
        threading.Thread(target=self.identity_worker,args=('hud',looks,strips,self.identity),name='OverlayIdentity',daemon=True).start()

    def identity_worker(self,kind,looks,strips,identity,image=None):
        try:
            if kind=='hud':
                names=read_hud_names(self.ocr,strips) if strips is not None else {}
                verdict,evidence=match_identity.compare_hud(identity,names,looks)
            else:
                own,portraits=tab_rows_identity(image,self.layout)
                rows=[r for r in range(len(self.layout['tab']['rows_y'])) if r!=own and r not in identity.get('own_rows',())]
                names=read_tab_names(self.ocr,image,self.layout,rows) if getattr(self.ocr,'ready',False) else {}
                verdict,evidence=match_identity.compare_tab(identity,{r:v for r,v in names.items() if r in rows},{r:portraits[r] for r in rows})
            result=(kind,identity,verdict,evidence,image)
        except Exception as exc:
            log.exception('Overlay identity check failed');result=exc
        self.finished_identity.emit(result)

    @guarded
    def identity_done(self,result):
        self.identity_busy=False
        if isinstance(result,Exception):return self.failed('identity',result)
        kind,identity,verdict,evidence,image=result
        if identity is not self.identity or self.phase!='locked' or self.automation_active():return
        if kind=='tab':return self.tab_identity_done(verdict,evidence,image)
        if verdict=='different':
            self.identity_strikes+=1;self.identity_unknown=0
            self.record('identity_check',verdict=verdict,strike=self.identity_strikes,slots=evidence)
            if self.identity_strikes>=match_identity.CHECKS_TO_UNLOCK:self.unlock('new_match_without_lobby',slots=evidence)
        elif verdict=='same':
            if self.suspect:
                self.record('identity_check',verdict=verdict,after='hud_gap',slots=evidence);self.suspect=False
                if self.state.slots and self.window is not None:self.show()
            self.identity_strikes=0;self.identity_unknown=0
        else:
            self.identity_strikes=0;self.identity_unknown+=1
            if self.suspect:
                self.record('identity_check',verdict=verdict,after='hud_gap',slots=evidence)
                # After a long HUD gap nothing confirms the old match: a re-read costs one Tab press, stale perks cost more.
                if self.identity_unknown>=match_identity.CHECKS_TO_UNLOCK:self.unlock('new_match_identity_unknown',slots=evidence)

    def check_tab_locked(self,window):
        """K1.3: a Tab press while locked: the poll's own frame (no burst) is compared with the locked scoreboard."""
        try:image,_=self.backend.grab(window.client)
        except ScreenError:return
        if not is_scoreboard(image,self.layout):return
        self.tab_check=False;self.identity_busy=True;self.window=window
        threading.Thread(target=self.identity_worker,args=('tab',None,None,self.identity,image.copy()),name='OverlayIdentity',daemon=True).start()

    def tab_identity_done(self,verdict,evidence,image):
        if verdict=='same' or (verdict=='unknown' and not self.suspect):
            self.record('tab_locked_same_match',verdict=verdict,rows=evidence)
            if verdict=='same' and self.suspect:self.suspect=False
            return
        # This match's recent HUD looks only (the cache may still hold the previous match's for a while).
        candidates=self.cache.candidates(max_age=20.);strips=self.cache.name_strips()
        self.unlock('new_match_at_tab',verdict=verdict,rows=evidence)
        if self.busy:return
        self.record('tab_pressed',phase=self.phase,role=self.match_role,after='unlock');self.key_down_at=time.monotonic()
        self.busy=True;self.session=None
        threading.Thread(target=self.read_worker,args=(image,candidates,strips),name='OverlayRead',daemon=True).start()

    def game_foreground(self):
        """Called from the key watcher thread: window handles and process names only, no screen reads."""
        return game_in_front(self.game_hwnd,self.foreground_window)

    def set_size(self,size):
        self.overlay.size_name=size
        if self.state.slots and self.overlay.isVisible():self.show()

    def start_watcher(self):
        self.watcher=KeyWatcher(key_code(self.key),self.game_foreground,self.key_event.emit,read=self.key_reader)
        self.watcher.start()

    def enable(self):
        if self.mode!='off':return
        self.start_watcher();self.mode='ready';self.presence.start()
        if hasattr(self.ocr,'start') and not getattr(self.ocr,'ready',False):self.ocr.start()
        self.record('overlay_started',key=self.key,size=self.overlay.size_name)
        self.status.emit('overlay_ready')

    def disable(self):
        if self.watcher:self.watcher.close();self.watcher=None
        if hasattr(self.ocr,'close'):self.ocr.close()
        self.presence.stop();self.scoreboard_wait.stop();self.followup.stop();self.reset();getattr(self.overlay,'retire',self.overlay.hide)()
        if self.mode!='off':self.record('overlay_stopped')
        self.mode='off'

    def suspend(self):
        """Bloodweb automation is starting: nothing may remain over the game."""
        self.overlay.clear();self.state.clear();self.cache.clear();self.scoreboard_wait.stop();self.followup.stop();self.session=None

    def reset(self):
        self.overlay.clear();self.state.clear();self.followup.stop();self.session=None
        if self.phase=='locked':self.phase='match'

    def grab(self):
        window=self.backend.info()
        self.game_hwnd=window.hwnd
        if not (window.foreground or overlay_in_front()):raise ScreenError('game_focus_lost')
        image,_=self.backend.grab(window.client);self.window=window
        return image

    def tab_open(self):
        return self.watcher is not None and self.watcher.down

    @guarded
    def handle_action(self,action):
        if self.mode=='off':return
        if self.automation_active():
            if action in ('key_down','capture'):self.status.emit('overlay_blocked_automation');self.tab_ignored('automation')
            return
        if action=='key_down':self.overlay.hide()
        from src.core.overlay_capture import POLICY
        if POLICY.mode=='no_capture':
            # J8 capture test: no pixels are read, but the perk window appears as after a real read.
            if action=='key_up':self.show_test_cluster()
            return
        if action in ('key_down','capture'):
            # Locked for this match, in the lobby or after the end scoreboard: a Tab press reads no perks.
            if self.phase=='locked' and action=='key_down' and self.identity is not None:self.tab_check=True   # K1.3: same match?
            if self.phase in ('locked','lobby','ended'):return self.tab_ignored(self.phase)
            if self.killer_match():return self.tab_ignored('killer_match')  # Killers can't see survivor perks on Tab.
            if exclusive_fullscreen():self.tab_ignored('exclusive_fullscreen');return self.status.emit('overlay_exclusive_fullscreen')
            try:self.hud_before=self.grab()  # Taken as the key goes down, before Tab covers the HUD.
            except ScreenError:self.tab_ignored('game_not_focused');return self.status.emit('overlay_game_not_focused')
            self.record('tab_pressed',phase=self.phase,role=self.match_role)
            self.followup.stop();self.session=None
            self.key_down_at=time.monotonic();self.wait_until=self.key_down_at+self.scoreboard_seconds;self.wait_frames=0
            self.last_scoreboard=None;self.scoreboard_wait.start()
        elif action=='key_up' and self.session is not None and not self.followup.isActive():
            self.start_followup()

    def show_test_cluster(self,seconds=8.):
        """J8 'no_capture' test: '?' clusters for every slot after Tab, hidden again after a few seconds."""
        from src.core.match_state import SlotPerks
        from src.core.perk_matcher import SlotResult
        try:window=self.backend.info()
        except ScreenError:return
        if not (window.foreground or overlay_in_front()):return
        unknown=SlotResult(None,None,(),0,0,'capture_test')
        slots={slot:SlotPerks((unknown,)*4,(None,)*4,None) for slot in SLOTS}
        self.record('capture_test_cluster')
        self.overlay.present(window.client,self.layout,slots)
        self.test_hide.start(round(seconds*1000))

    @guarded
    def look_for_scoreboard(self):
        """Read as soon as the scoreboard is really on screen; a short press is enough."""
        if time.monotonic()>self.wait_until or self.automation_active():
            self.scoreboard_wait.stop()
            self.record('scoreboard_not_seen',frames=self.wait_frames,key_down=self.tab_open(),busy=self.busy,reject=self.last_reject)
            return self.status.emit('overlay_not_scoreboard')
        if self.busy:return
        try:image=self.grab()
        except ScreenError:return
        self.wait_frames+=1
        if not self.scoreboard_found(image):
            self.last_scoreboard=None;return
        # Fully shown only when two consecutive frames agree; a fading scoreboard reads tiles as '?'.
        signature=scoreboard_signature(image,self.layout)
        if signature!=self.last_scoreboard:
            self.last_scoreboard=signature;self.last_reject='frames_differ';return
        self.last_scoreboard=None
        self.scoreboard_wait.stop();self.busy=True
        candidates=self.cache.candidates();strips=self.cache.name_strips()
        if self.hud_before is not None:
            for slot,feature in enumerate(hud_portraits(self.hud_before,self.layout)):
                if feature is not None:candidates[slot].append(feature)
            before=hud_strips(self.hud_before,self.layout)
            strips=[s if s is not None else b for s,b in zip(strips,before)]
        threading.Thread(target=self.read_worker,args=(image.copy(),candidates,strips),name='OverlayRead',daemon=True).start()

    def scoreboard_found(self,image):
        """The current fit first; a search for another UI scale at most every 0.5 s, since it costs ~0.2 s."""
        if is_scoreboard(image,self.layout):return True
        self.last_reject=scoreboard_evidence(image,self.layout)
        now=time.monotonic()
        if now<self.next_tab_search:return False
        self.next_tab_search=now+.5
        fitted=find_scoreboard(image,self.base_layout)
        if fitted is None:return False
        self.layout={**self.layout,'tab':fitted['tab']}
        self.record('scoreboard_fitted',rows_y=[round(y,1) for y in fitted['tab']['rows_y']],perk_x=[round(x,1) for x in fitted['tab']['perk_x']])
        return True

    def update_hud_fit(self,image):
        """Once per match: finds DBD's HUD scale. Nothing changes at the usual scale."""
        self.hud_ticks=(self.hud_ticks+1)%4
        if self.hud_fitted or self.hud_ticks!=1:return
        scale,shift,confident=fit_hud(image,self.base_layout)
        if not confident:return
        if scale==1. and abs(shift)<6:
            self.hud_fitted=True;self.layout={**self.layout,'hud':self.base_layout['hud']};return
        if self.read_own(image,scale)[0]:  # A menu can look like a column of names; the player's own perks can't.
            self.hud_fitted=True;self.hud_scale=scale;self.layout={**self.layout,'hud':with_hud_fit(self.base_layout,scale,shift)['hud']}
            self.record('hud_fitted',scale=scale,shift=shift)
            if not supported_ui_scale(scale):self.status.emit('ui_scale_unsupported')

    def read_worker(self,image,candidates=None,strips=None):
        try:
            names={}
            def read_names():
                tab=read_tab_names(self.ocr,image,self.layout,range(len(self.layout['tab']['rows_y'])))
                hud=read_hud_names(self.ocr,strips,(0,)+tuple(SLOTS)) if strips else {}   # row 1 too: the chase meme's own row (B2)
                names.update(tab=tab,hud=hud)
            ocr_thread=None
            if getattr(self.ocr,'ready',False):
                ocr_thread=threading.Thread(target=read_names,name='OverlayOcr',daemon=True);ocr_thread.start()
            if self.matcher is None:
                if self.matcher_factory:self.matcher=self.matcher_factory()
                else:
                    from src.core.perk_matcher import PerkMatcher
                    self.matcher=PerkMatcher(self.root)
                    self.matcher.unknown_dir=Path(self.root)/'debug'/'overlay'/'unknown_tiles'
            rows=read_tab(image,self.matcher,self.layout)
            scores,best=score_rows(rows,candidates or [[] for _ in range(4)])
            if ocr_thread is not None:ocr_thread.join(5)
            teammates={r.index for r in rows if not r.own}
            tab_names={k:v for k,v in names.get('tab',{}).items() if k in teammates}
            all_hud_names=names.get('hud',{});hud_names={k:v for k,v in all_hud_names.items() if k in SLOTS}
            self.share_own_row(rows,candidates or [[] for _ in range(4)],all_hud_names)
            result=(rows,scores,best,name_scores(tab_names,hud_names) if tab_names and hud_names else {},
                    dict(tab=tab_names,hud=hud_names),candidates or [[] for _ in range(4)])
        except Exception as exc:
            log.exception('Overlay read failed');result=exc
        self.finished_read.emit(result,image)

    @guarded
    def read_done(self,result,image):
        self.busy=False
        if self.automation_active():return
        if isinstance(result,Exception):
            self.failed('read',result);return self.status.emit('overlay_capture_failed')
        rows,scores,best,names,texts,looks=result
        self.session=dict(rows=rows,scores=dict(scores),best=dict(best),names=dict(names),texts=texts,looks=looks,frames=0,deadline=None)
        self.apply_session('tab_read')
        if self.state.complete(rows):return self.finish_session()
        if not self.tab_open():self.start_followup()

    def start_followup(self):
        self.session['deadline']=time.monotonic()+self.followup_seconds;self.followup.start()

    @guarded
    def followup_tick(self):
        """Fresh HUD frames after Tab closes; the best score per pair is kept."""
        session=self.session
        if session is None or self.automation_active():
            self.followup.stop();return
        if time.monotonic()>session['deadline']:return self.finish_session()
        if self.busy or self.tab_open():return
        try:image=self.grab()
        except ScreenError:return
        if is_scoreboard(image,self.layout):return
        self.busy=True
        candidates=[[f] if f is not None else [] for f in hud_portraits(image,self.layout)]
        free=[s for s in SLOTS if s not in self.state.last_mapping]
        strips=hud_strips(image,self.layout) if session['texts'].get('tab') and free and getattr(self.ocr,'ready',False) else None
        threading.Thread(target=self.score_worker,args=(session,candidates,strips,free),name='OverlayRead',daemon=True).start()

    def score_worker(self,session,candidates,strips=None,free=()):
        try:
            scores,best=score_rows(session['rows'],candidates)
            names={}
            if strips is not None:
                hud=read_hud_names(self.ocr,strips,free)
                if hud:names=name_scores(session['texts']['tab'],hud)
            result=(session,scores,best,names,candidates)
        except Exception as exc:
            log.exception('Overlay scoring failed');result=exc
        self.finished_score.emit(result)

    @guarded
    def score_done(self,result):
        self.busy=False
        if isinstance(result,Exception):return self.failed('score',result)
        if result[0] is not self.session or self.automation_active():return
        session,scores,best,names,candidates=result
        for pair,value in scores.items():
            if value>session['scores'].get(pair,-1.):session['scores'][pair]=value;session['best'][pair]=best[pair]
        for pair,value in names.items():
            session['names'][pair]=max(value,session['names'].get(pair,0.))
        for slot,features in enumerate(candidates):session['looks'][slot]=session['looks'][slot]+features
        session['frames']+=1
        self.apply_session('tab_followup')
        if self.state.complete(session['rows']):self.finish_session()

    def finish_session(self):
        self.followup.stop();session=self.session;self.session=None
        if session is not None:
            self.record('tab_done',assigned=self.assigned_log(),followup_frames=session['frames'],
                seconds=round(time.monotonic()-self.key_down_at,2),scores=self.scores_log(session['scores']),
                names=self.scores_log(session['names']),nicknames=self.texts_log(session['texts']))

    def assigned_log(self):
        return {slot+1:dict(row=s.row+1,method=s.method) for slot,s in sorted(self.state.slots.items())}

    @staticmethod
    def scores_log(scores):
        return {f'row{r+1}-slot{s+1}':round(v,3) for (r,s),v in scores.items()}

    @staticmethod
    def texts_log(texts):
        return dict(tab={f'row{r+1}':v[0] for r,v in texts.get('tab',{}).items()},
                    hud={f'slot{s+1}':v[0] for s,v in texts.get('hud',{}).items()})

    def apply_session(self,event):
        session=self.session
        if session is None:return
        before=self.assigned_log()
        changed,_=self.state.apply(session['rows'],scores=session['scores'],best=session['best'],
                                   names=session['names'],looks=session['looks'])
        rows=session['rows']
        if event=='tab_read' or changed or self.assigned_log()!=before:
            self.record(event,own_row=[r.index+1 for r in rows if r.own],
                perks={r.index+1:[s.names[0] if s.names else s.icon_id for s in r.slots] for r in rows},
                assigned=self.assigned_log(),changed=[s+1 for s in changed],
                seconds=round(time.monotonic()-self.key_down_at,2),scores=self.scores_log(session['scores']),
                names=self.scores_log(session['names']),nicknames=self.texts_log(session['texts']))
        if self.state.slots:self.show();self.status.emit('overlay_showing')
        self.check_lock()

    def show(self):
        if self.window is not None and self.state.slots and not self.automation_active():
            self.overlay.present(self.window.client,self.layout,self.state.slots)

    def keep_watcher_alive(self):
        if self.mode!='off' and (self.watcher is None or not self.watcher.thread.is_alive()):
            self.restarts+=1;self.record('watcher_restarted',count=self.restarts)
            log.warning('Overlay key watcher restarted (%s)',self.restarts)
            self.start_watcher()

    @guarded
    def poll(self):
        """Caches HUD portraits and nickname strips; never floats over other apps, an automation run or a finished match."""
        self.keep_watcher_alive()
        if self.automation_active():
            if self.overlay.isVisible():self.suspend()
            return
        try:window=self.backend.info()
        except ScreenError:
            self.game_hwnd=None;self.reset();return
        self.game_hwnd=window.hwnd
        if not (window.foreground or overlay_in_front()):
            if self.away_since is None and self.phase in ('match','locked'):
                self.away_since=time.monotonic();self.record('game_not_in_front',phase=self.phase,foreground=window_process(foreground_handle()) or '?')
            self.overlay.hide();return
        if self.away_since is not None:
            self.record('game_in_front_again',phase=self.phase,seconds=round(time.monotonic()-self.away_since,1));self.away_since=None
        if self.tab_open():
            self.overlay.hide()  # The scoreboard needs no clusters over it.
            if self.phase=='locked' and self.tab_check and not self.identity_busy and not self.automation_active():self.check_tab_locked(window)
            return
        if self.phase=='lobby':  # Nothing changes here: look every 1.5 s instead of 0.5 s.
            self.lobby_ticks=(self.lobby_ticks+1)%3
            if self.lobby_ticks:return
        try:image,_=self.backend.grab(window.client)
        except ScreenError:return
        if is_lobby(image,self.layout):return self.enter_lobby()
        if is_scoreboard(image,self.layout):return  # Tab open: the HUD is hidden, not gone.
        self.update_hud_fit(image)
        # Escaped or dead, or the end-game table: hide, but keep the perks until the lobby.
        ended=is_end_scoreboard(image);visible=not ended and hud_visible(image,self.layout)
        if ended and self.phase in ('match','locked'):return self.match_over('end_scoreboard')
        # The costly perk check is skipped when there are no nicknames and nothing to hide.
        present,role=(False,None) if ended or not (visible or self.overlay.isVisible()) else self.read_own(image)
        if not present or not visible:
            self.hud_missing=getattr(self,'hud_missing',0)+1
            if not present or self.hud_missing>=2:self.overlay.hide()
            self.report_absent(image)
            return
        self.hud_missing=0;self.hud_gone_since=None;MATCH.note_hud()
        if self.phase=='locked' and self.identity is not None:
            if MATCH.epoch!=self.identity['epoch'] and not self.suspect:
                # K1.2/K1.4: the match HUD was gone longer than Match.HOLD and is back: no stale perks over it, even
                # for a second, until the HUD's nicknames say whether it is still the same match.
                self.suspect=True;self.identity_unknown=0;self.identity['epoch']=MATCH.epoch
                self.record('locked_hud_back_after_gap',hold=Match.HOLD);self.overlay.clear();self.cache.clear()
                self.check_identity(image,force=True)
            else:self.check_identity(image)
        if self.match_role is None and role:
            self.match_role=role;self.record('match_role',role=role)
        MATCH.note_role(self.match_role)
        if self.killer_match():
            self.overlay.hide();return
        if self.phase in ('lobby','unknown','ended'):
            self.record('match_detected',previous=self.phase,key_watcher=self.watcher_stats());self.phase='match'
            self.status.emit('overlay_reading')
            # J7: the perk window is mapped (blank) now, at match start, not on the first Tab read later in it.
            if isinstance(self.overlay,QWidget) and not QWidget.isVisible(self.overlay) and keep_mapped():
                self.overlay.place(window.client,self.layout);self.overlay.map_blank('match start')
        # K1: kept while locked too (cheap crops of this frame), so a Tab that unlocks can pair at once.
        self.cache.add(hud_portraits(image,self.layout),hud_strips(image,self.layout))
        # The perks stay until the lobby, the end scoreboard or another match's HUD is seen.
        if self.state.slots and not self.suspect and not self.overlay.isVisible():self.window=window;self.show()

    def report_absent(self,image):
        """K1: while the HUD is gone after a match, how close the frame is to the lobby (is_lobby's evidence), every 10 s."""
        if self.phase not in ('match','locked'):return
        now=time.monotonic()
        if self.hud_gone_since is None:self.hud_gone_since=now;self.next_absent_report=now+self.absent_report;return
        if now<self.next_absent_report:return
        self.next_absent_report=now+self.absent_report
        try:evidence=lobby_evidence(image,self.layout)
        except Exception:evidence=None
        self.record('hud_absent',phase=self.phase,seconds=round(now-self.hud_gone_since),
                    lobby={k:round(v,2) for k,v in evidence.items()} if evidence else None)
