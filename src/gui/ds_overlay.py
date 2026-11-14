"""Decisive Strike reminder drawn beside the survivor portraits in killer matches."""
import logging
import time

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QPolygonF

from src.core.killer_status import DsTracker, HUD_GONE_SECONDS, column_rect, load_templates, read_column
from src.core.own_perks import load_matchers, own_rect, read_own
from src.core.match_reader import Geometry, fit_hud, load_layout, right_centre_x, supported_ui_scale, with_hud_fit
from src.core.perk_catalog import load_catalog
from src.core.screen_manager import Rect, ScreenError, WindowsBackend
from src.gui.match_overlay import PerkOverlay, foreground_handle, overlay_in_front

log=logging.getLogger(__name__)
TICK_MS=100
READ_EVERY=3  # A read about three times a second during a killer match; drawing stays smooth between reads.
IDLE_READ_EVERY=10  # Once a second while no killer HUD is on screen.
OWN_EVERY=3   # The own-perk check (the costly one) runs on every third read once the role is known.
DIAMOND=30    # Half diagonal at 1920x1080.
CENTRE_X=50


def outline(points,fraction):
    """The first `fraction` of a closed polygon, clockwise from its first point."""
    out=[points[0]];remaining=fraction*4
    for a,b in zip(points,points[1:]+points[:1]):
        if remaining<=0:break
        step=min(1.,remaining);out.append(a+(b-a)*step);remaining-=1
    return out


class DsOverlay(PerkOverlay):
    def __init__(self,root):
        super().__init__(root)
        record=next((p for p in load_catalog(root,'survivor') if p.name=='Decisive Strike'),None)
        self.icon=QImage(str(self.root/record.icon)) if record else QImage()
        self.shown={};self.position='left';self.scale=1.

    def present_timers(self,client,layout,shown):
        self.layout_data=layout;self.client=client;self.shown=shown
        screen=QGuiApplication.screenAt(QPointF(client.x,client.y).toPoint()) or QGuiApplication.primaryScreen()
        self.ratio=r=screen.devicePixelRatio() if screen else 1.
        from src.gui.match_overlay import hud_overlay_rect
        self.setGeometry(*hud_overlay_rect(client,r))   # J4: never the whole monitor
        if not self.isVisible():self.show()
        self.update()

    def paintEvent(self,event):
        if not self.shown or not self.layout_data:return
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);p.setRenderHint(QPainter.SmoothPixmapTransform)
        g=Geometry(self.layout_data,(self.client.height,self.client.width));hud=self.layout_data['hud'];r=self.ratio
        fit=hud.get('fit_scale',1.)
        column=CENTRE_X*fit if self.position=='left' else right_centre_x(self.layout_data,DIAMOND*fit)
        for slot,(kind,value) in self.shown.items():
            x,y=g.hud(column,hud['portrait_y'][slot]);x/=r;y/=r;d=min(g.size(DIAMOND*fit)*self.scale,g.size(min(b-a for a,b in zip(hud['portrait_y'],hud['portrait_y'][1:]))/2-1))/r
            points=[QPointF(x,y-d),QPointF(x+d,y),QPointF(x,y+d),QPointF(x-d,y)]
            path=QPainterPath();path.addPolygon(QPolygonF(points));path.closeSubpath()
            p.setPen(Qt.NoPen);p.setBrush(QColor(0,0,0,150));p.drawPath(path)
            if not self.icon.isNull():
                p.save();p.setClipPath(path);p.drawImage(QRectF(x-d,y-d,d*2,d*2),self.icon);p.restore()
            p.setBrush(Qt.NoBrush);p.setPen(QPen(QColor(150,150,150,170),2));p.drawPath(path)
            red=QPen(QColor(232,32,48),max(3.,d*.13));red.setCapStyle(Qt.RoundCap);red.setJoinStyle(Qt.RoundJoin)
            if kind=='running':
                p.setPen(red);p.drawPolyline(QPolygonF(outline(points,value)))
            else:  # Expired: the whole diamond turns red, then fades away.
                p.setOpacity(.35+.65*value);p.setPen(red);p.setBrush(QColor(200,20,36,130));p.drawPath(path);p.setOpacity(1)


class DsTimerController(QObject):
    """Runs while enabled and the game is in front; only a killer match shows anything."""
    status=Signal(str)
    def __init__(self,root,is_killer,automation_active,backend=None,overlay=None,foreground_window=None,clock=time.monotonic,expired_seconds=15.):
        super().__init__()
        self.is_killer=is_killer;self.automation_active=automation_active;self.backend=backend or WindowsBackend()
        self.overlay=overlay or DsOverlay(root);self.foreground_window=foreground_window or foreground_handle;self.clock=clock
        self.layout=self.base_layout=load_layout(root);self.hud_scale=1.;self.hud_fitted=False;self.fit_reads=0;self.templates=load_templates(root);self.tracker=DsTracker(expired_seconds)
        self.ticks=0;self.hud_seen_at=None;self.client=None;self.hud_shown=False;self.own_reads=0;self.own_present=False
        self.root=root;self.own_matchers=None;self.match_role=None
        self.timer=QTimer(self);self.timer.setInterval(TICK_MS);self.timer.timeout.connect(self.tick)

    def enable(self):
        self.timer.start()

    def disable(self):
        self.timer.stop();self.tracker.clear();self.overlay.hide()

    def tick(self):
        try:self.step()
        except ScreenError:self.overlay.hide()
        except Exception:
            log.exception('Decisive Strike timer step failed');self.overlay.hide()

    def try_fit(self,client):
        """Finds DBD's HUD scale from the left part of the window; the player's own perks confirm it."""
        band,_=self.backend.grab(Rect(client.x,client.y,round(client.width*.3),client.height))
        scale,shift,confident=fit_hud(band,self.base_layout,band=True)
        if not confident or (scale==1. and abs(shift)<6):return False
        if self.own_matchers is None:self.own_matchers=load_matchers(self.root)
        ox,oy,ow,oh=own_rect(client.width,client.height,scale);own,_=self.backend.grab(Rect(client.x+ox,client.y+oy,ow,oh))
        if not read_own(own,client.width,client.height,self.own_matchers,scale)[0]:return False
        self.layout=with_hud_fit(self.base_layout,scale,shift);self.hud_scale=scale;self.hud_fitted=True
        log.info('DS timer fitted to HUD scale %.2f shift %s',scale,shift)
        if not supported_ui_scale(scale):self.status.emit('ui_scale_unsupported')
        return True

    def step(self):
        if self.automation_active():
            self.tracker.clear();self.overlay.hide();self.match_role=None;return
        self.ticks+=1
        if self.ticks%(READ_EVERY if self.hud_shown else IDLE_READ_EVERY)==0:
            window=self.backend.info()
            if not (window.foreground or overlay_in_front()):
                self.overlay.hide();self.hud_shown=False;return
            client=window.client;x,y,w,h=column_rect(self.layout,client.width,client.height)
            image,_=self.backend.grab(Rect(client.x+x,client.y+y,w,h))
            statuses,visible=read_column(image,self.layout,client.width,client.height,self.templates)
            if not visible and not self.hud_fitted:
                self.fit_reads=(self.fit_reads+1)%3
                if self.fit_reads==1 and self.try_fit(client):
                    x,y,w,h=column_rect(self.layout,client.width,client.height);image,_=self.backend.grab(Rect(client.x+x,client.y+y,w,h))
                    statuses,visible=read_column(image,self.layout,client.width,client.height,self.templates)
            present,role=False,None
            if visible and self.own_present and self.match_role and self.own_reads%OWN_EVERY:present,role=True,self.match_role
            elif visible:  # No nicknames, no match: menus never pay for (or load) the perk catalogs.
                ox,oy,ow,oh=own_rect(client.width,client.height,self.hud_scale)
                own,_=self.backend.grab(Rect(client.x+ox,client.y+oy,ow,oh))
                if self.own_matchers is None:self.own_matchers=load_matchers(self.root)
                # After the role is known only its own catalog is needed for the HUD check.
                matchers={self.match_role:self.own_matchers[self.match_role]} if self.match_role else self.own_matchers
                present,role=read_own(own,client.width,client.height,matchers,self.hud_scale)
            self.own_reads+=1;self.own_present=present
            if present and self.match_role is None and role:self.match_role=role
            now=self.clock();self.client=client
            killer=self.match_role=='killer' or (self.match_role is None and self.is_killer())
            self.hud_shown=visible=visible and present and killer
            if visible:
                self.hud_seen_at=now;self.tracker.update(statuses,now)
            elif self.hud_seen_at is not None and now-self.hud_seen_at>HUD_GONE_SECONDS:
                self.tracker.clear();self.hud_seen_at=None;self.match_role=None
                self.layout=self.base_layout;self.hud_scale=1.;self.hud_fitted=False  # The UI scale can change between matches.
            elif self.hud_seen_at is None and not present:
                self.match_role=None
        shown=self.tracker.display(self.clock())
        if shown and self.hud_shown and self.client is not None:self.overlay.present_timers(self.client,self.layout,shown)
        else:self.overlay.hide()
