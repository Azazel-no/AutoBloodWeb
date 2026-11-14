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
from datetime import datetime
from pathlib import Path
import cv2
import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPainterPath, QPolygonF
from PySide6.QtWidgets import QWidget
from src.core.display_mode import exclusive_fullscreen
from src.core.hotkey_config import parse_hotkey
from src.core.match_reader import EMPTY, SLOTS, Geometry, right_centre_x, hud_portraits, hud_visible, supported_ui_scale, is_end_scoreboard, is_lobby, is_scoreboard, find_scoreboard, fit_hud, with_hud_fit, scoreboard_evidence, load_layout, read_tab, score_rows, scoreboard_signature
from src.core.match_state import HudCache, MatchState
from src.core.nicknames import NicknameOcr, hud_strips, name_scores, read_hud_names, read_tab_names
from src.core.screen_manager import ScreenError, WindowsBackend

log=logging.getLogger(__name__)
WS_EX=0x00000020|0x00080000|0x08000000|0x00000080  # Transparent, layered, no-activate, tool window.
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
        self.thread=threading.Thread(target=self.run,name='OverlayKey',daemon=True)

    def start(self):self.thread.start()

    def step(self):
        down=self.is_game_foreground() and self.read(self.vk)
        if down!=self.down:
            self.down=down;self.callback('key_down' if down else 'key_up')

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

    def showEvent(self,event):
        super().showEvent(event)
        u=ctypes.windll.user32;hwnd=int(self.winId())
        u.SetWindowLongPtrW.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_ssize_t]
        u.GetWindowLongPtrW.argtypes=[ctypes.c_void_p,ctypes.c_int];u.GetWindowLongPtrW.restype=ctypes.c_ssize_t
        u.SetWindowLongPtrW(hwnd,-20,u.GetWindowLongPtrW(hwnd,-20)|WS_EX)

    def present(self,client,layout,slots):
        """client: physical client rect. Qt geometry is logical, hence the device ratio."""
        self.layout_data=layout;self.client=client
        self.slots={slot:(perks.slots,[to_qimage(t) if t is not None else None for t in perks.tiles]) for slot,perks in slots.items()}
        screen=QGuiApplication.screenAt(QPointF(client.x,client.y).toPoint()) or QGuiApplication.primaryScreen()
        self.ratio=screen.devicePixelRatio() if screen else 1.
        r=self.ratio
        self.setGeometry(round(client.x/r),round(client.y/r),round(client.width/r),round(client.height/r))
        self.show();self.update()

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

    def paintEvent(self,event):
        if not self.slots or not self.layout_data:return
        painter=QPainter(self);painter.setRenderHint(QPainter.Antialiasing);painter.setRenderHint(QPainter.SmoothPixmapTransform)
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
    key_event=Signal(str)
    scoreboard_seconds=1.5  # How long after the key goes down the scoreboard may take to appear.
    followup_seconds=3.     # How long after Tab closes fresh HUD frames are tried.
    error_seconds=10.       # An error is shown this long unless the overlay works again.

    def __init__(self,root,automation_active,backend=None,matcher_factory=None,overlay=None,key='TAB',key_reader=None,log_dir=None,
                 size='large',ocr=None,foreground_window=None,is_killer=lambda:False,own_reader=None):
        super().__init__()
        self.root=Path(root);self.automation_active=automation_active;self.backend=backend or WindowsBackend()
        self.matcher_factory=matcher_factory;self.overlay=overlay or PerkOverlay(root);self.key=key;self.key_reader=key_reader
        self.overlay.size_name=size
        self.state=MatchState();self.cache=HudCache();self.layout=load_layout(self.root);self.matcher=None;self.window=None
        self.base_layout=self.layout;self.next_tab_search=0.;self.last_reject=None;self.hud_fitted=False;self.hud_ticks=0;self.hud_scale=1.
        self.ocr=ocr if ocr is not None else NicknameOcr(self.root)
        self.foreground_window=foreground_window or foreground_handle
        self.watcher=None;self.mode='off';self.busy=False;self.hud_before=None;self.session=None
        self.game_hwnd=None;self.last_error=None;self.last_error_at=-1e9;self.errors_seen=set();self.restarts=0
        self.log_dir=Path(log_dir) if log_dir else self.root/'debug/overlay'
        self.finished_read.connect(self.read_done);self.finished_score.connect(self.score_done);self.key_event.connect(self.handle_action)
        self.presence=QTimer(self);self.presence.setInterval(500);self.presence.timeout.connect(self.poll)
        self.scoreboard_wait=QTimer(self);self.scoreboard_wait.setInterval(50);self.scoreboard_wait.timeout.connect(self.look_for_scoreboard)
        self.followup=QTimer(self);self.followup.setInterval(150);self.followup.timeout.connect(self.followup_tick)
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
        if self.phase!='lobby':self.record('lobby_seen',previous=self.phase)
        self.phase='lobby';self.reset();self.cache.clear();self.scoreboard_wait.stop();self.match_role=None
        self.hud_fitted=False;self.hud_scale=1.;self.layout=self.base_layout  # The UI scale can change between matches.
        self.status.emit('overlay_lobby')

    def check_lock(self):
        teammates=sum(not r.own for r in self.session['rows']) if self.session else 3
        if self.phase!='locked' and self.state.all_known(teammates):
            self.phase='locked';self.record('perks_locked',assigned=self.assigned_log())
            self.status.emit('overlay_locked')

    def game_foreground(self):
        """Called from the key watcher thread: compares window handles only, no screen reads."""
        return bool(self.game_hwnd) and self.foreground_window()==self.game_hwnd

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
        self.presence.stop();self.scoreboard_wait.stop();self.followup.stop();self.reset()
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
        if not window.foreground:raise ScreenError('game_focus_lost')
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
        if action in ('key_down','capture'):
            # Locked for this match, or in the lobby: a Tab press does nothing, not even a capture.
            if self.phase in ('locked','lobby'):return self.tab_ignored(self.phase)
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
                hud=read_hud_names(self.ocr,strips) if strips else {}
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
            hud_names=names.get('hud',{})
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
        if not window.foreground:
            self.overlay.hide();return
        if self.tab_open():
            self.overlay.hide();return  # The scoreboard needs no clusters over it.
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
        # The costly perk check is skipped when there are no nicknames and nothing to hide.
        present,role=(False,None) if ended or not (visible or self.overlay.isVisible()) else self.read_own(image)
        if not present or not visible:
            self.hud_missing=getattr(self,'hud_missing',0)+1
            if not present or self.hud_missing>=2:self.overlay.hide()
            return
        self.hud_missing=0
        if self.match_role is None and role:
            self.match_role=role;self.record('match_role',role=role)
        if self.killer_match():
            self.overlay.hide();return
        if self.phase in ('lobby','unknown'):
            self.record('match_detected',previous=self.phase);self.phase='match'
            self.status.emit('overlay_reading')
        if self.phase!='locked':self.cache.add(hud_portraits(image,self.layout),hud_strips(image,self.layout))
        # The perks stay until the lobby is seen again: no portrait-based 'match ended' guess.
        if self.state.slots and not self.overlay.isVisible():self.window=window;self.show()
