import math
from PySide6.QtCore import Qt, QVariantAnimation, QEasingCurve, QSize, QRect, QPoint, QRectF, QPointF, Signal
from PySide6.QtGui import QColor, QPainter, QLinearGradient, QRadialGradient, QPen, QPolygonF, QPixmap, QIcon
from PySide6.QtWidgets import QCheckBox, QWidget, QPushButton, QGridLayout, QToolButton, QLabel, QLayout, QSizePolicy, QDialog, QVBoxLayout


HOVER_MS=180


def app_icon(root,tray=False):
    """The app icon, the simplified tray variant when present, else the old role icon."""
    for name in (('assets/tray_icon.ico',) if tray else ())+('assets/app_icon.ico',):
        icon=QIcon(str(root/name)) if (root/name).exists() else QIcon()
        if not icon.isNull():return icon
    return QIcon(str(root/'assets/ui/roles/killer.png'))


class Hover:
    """Smooth 0..1 hover progress for anything that paints a highlight."""
    def init_hover(self):
        self.progress=0.0
        self.animation=QVariantAnimation(self,duration=HOVER_MS)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.valueChanged.connect(self.set_progress)
        self.setCursor(Qt.PointingHandCursor)

    def set_progress(self,value):
        self.progress=float(value); self.update()

    def fade(self,target):
        self.animation.stop(); self.animation.setStartValue(self.progress); self.animation.setEndValue(target)
        self.animation.start()

    def enterEvent(self,event):
        self.fade(1.0); super().enterEvent(event)

    def leaveEvent(self,event):
        self.fade(0.0); super().leaveEvent(event)


def red_gradient(width,bright=0.):
    """Dark crimson to blood red; `bright` shifts it towards the hover colours."""
    gradient=QLinearGradient(0,0,width,0)
    gradient.setColorAt(0,QColor(round(110+40*bright),round(14+10*bright),round(26+10*bright)))
    gradient.setColorAt(1,QColor(round(190+50*bright),round(24+22*bright),round(36+18*bright)))
    return gradient


class Background(QWidget):
    def __init__(self,root,parent=None):
        super().__init__(parent)

    def paintEvent(self,event):
        painter=QPainter(self)
        painter.fillRect(self.rect(),QColor('#0a0708'))
        glow=QRadialGradient(self.width(),0,max(self.width(),self.height())*.9)
        glow.setColorAt(0,QColor(120,14,24,120)); glow.setColorAt(1,QColor(10,7,8,0))
        painter.fillRect(self.rect(),glow)


class BloodButton(Hover,QPushButton):
    def __init__(self,text='',parent=None,primary=False):
        super().__init__(text,parent)
        self.primary=primary
        self.init_hover()
        self.setMinimumHeight(34)

    def paintEvent(self,event):
        p=QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        rect=QRectF(self.rect()).adjusted(1.5,1.5,-1.5,-1.5);radius=min(14,rect.height()/2)
        enabled=self.isEnabled();down=self.isDown() and enabled;lit=self.progress if enabled else 0.
        if lit>0:  # Red glow around the button.
            p.setPen(Qt.NoPen);p.setBrush(QColor(220,30,45,round(46*lit)))
            p.drawRoundedRect(QRectF(self.rect()),radius+1,radius+1)
        if self.primary and enabled:
            p.setPen(Qt.NoPen);p.setBrush(red_gradient(self.width(),0. if down else lit))
        else:
            p.setPen(QPen(QColor(round(120+90*lit),round(30+10*lit),40) if enabled else QColor('#3a2c2e'),1.2))
            if lit>0:
                gradient=red_gradient(self.width());gradient.setColorAt(0,QColor(90,14,24,round(170*lit)));gradient.setColorAt(1,QColor(170,24,36,round(170*lit)))
                p.setBrush(gradient)
            else:p.setBrush(QColor(22,15,16,235))
        if down:p.setOpacity(.8)
        p.drawRoundedRect(rect,radius,radius);p.setOpacity(1)
        p.setPen(QColor('#f4eeee') if enabled else QColor('#6c5f60'))
        p.setFont(self.font()); p.drawText(rect,Qt.AlignCenter,self.text())


class ToggleSwitch(QCheckBox):
    """On/off switch: red when on, grey when off. Keeps the QCheckBox interface."""
    TRACK=34;GAP=8

    def __init__(self,text='',parent=None):
        super().__init__(text,parent);self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self):
        metrics=self.fontMetrics()
        return QSize(self.TRACK+(self.GAP+metrics.horizontalAdvance(self.text())+2 if self.text() else 0),max(22,metrics.height()+4))

    def minimumSizeHint(self):return self.sizeHint()

    def hitButton(self,position):return self.rect().contains(position)

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);top=(self.height()-18)/2
        if not self.isEnabled():p.setOpacity(.45)
        p.setPen(QPen(QColor(226,60,76),1) if self.underMouse() else Qt.NoPen)
        p.setBrush(red_gradient(self.TRACK,.5) if self.isChecked() else QColor('#3b3234'))
        p.drawRoundedRect(QRectF(.5,top,self.TRACK-1,18),9,9)
        p.setPen(Qt.NoPen);p.setBrush(QColor('#ffffff') if self.isChecked() else QColor('#a89a9b'))
        p.drawEllipse(QPointF(self.TRACK-9.5 if self.isChecked() else 9.5,top+9),6.5,6.5)
        if self.text():
            area=QRectF(self.TRACK+self.GAP,0,self.width()-self.TRACK-self.GAP,self.height())
            p.setPen(QColor('#ded2d3'));p.setFont(self.font())
            p.drawText(area,Qt.AlignVCenter|Qt.AlignLeft,self.fontMetrics().elidedText(self.text(),Qt.ElideRight,round(area.width())))


class TrashButton(Hover,QPushButton):
    """Small icon-only button with a drawn bin."""
    def __init__(self,parent=None):
        super().__init__('',parent);self.setFixedSize(28,28);self.init_hover()

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        if self.progress>0 and self.isEnabled():
            p.setPen(Qt.NoPen);p.setBrush(QColor(200,28,44,round(150*self.progress)));p.drawRoundedRect(QRectF(self.rect()),8,8)
        p.setBrush(Qt.NoBrush);p.setPen(QPen(QColor('#d9c9ca') if self.isEnabled() else QColor('#5c5052'),1.4,Qt.SolidLine,Qt.RoundCap))
        self.glyph(p)

    def glyph(self,p):
        p.drawLine(QPointF(8,10),QPointF(20,10));p.drawLine(QPointF(12,10),QPointF(12.5,7.5));p.drawLine(QPointF(12.5,7.5),QPointF(15.5,7.5));p.drawLine(QPointF(15.5,7.5),QPointF(16,10))
        p.drawPolyline(QPolygonF([QPointF(9.5,10),QPointF(10.5,21),QPointF(17.5,21),QPointF(18.5,10)]))
        p.drawLine(QPointF(12.5,13),QPointF(12.8,18));p.drawLine(QPointF(15.5,13),QPointF(15.2,18))


class ResetButton(TrashButton):
    """Small icon-only button with a drawn circular arrow."""
    def glyph(self,p):
        p.drawArc(QRectF(7.5,7.5,13,13),60*16,290*16)
        p.drawPolyline(QPolygonF([QPointF(17,4.5),QPointF(17.5,9),QPointF(13,9.5)]))


class IconTile(Hover,QToolButton):
    """Library tile: the usual icon with an animated red highlight on hover."""
    details_requested=Signal()

    def __init__(self,parent=None):
        super().__init__(parent)
        self.init_hover()

    def contextMenuEvent(self,event):
        self.details_requested.emit();event.accept()

    def paintEvent(self,event):
        super().paintEvent(event)
        if self.progress<=0 and not self.isDown():return
        p=QPainter(self); p.setRenderHint(QPainter.Antialiasing); p.setPen(Qt.NoPen)
        amount=.75 if self.isDown() else self.progress
        gradient=QLinearGradient(0,self.height(),self.width(),0)
        gradient.setColorAt(0,QColor(200,24,40,round(120*amount)));gradient.setColorAt(1,QColor(200,24,40,round(20*amount)))
        p.setBrush(gradient);p.drawRoundedRect(QRectF(self.rect()).adjusted(1,1,-1,-1),9,9)


def gear_path(center,body=8.6,tip=12.2,tooth=4.6,teeth=8):
    """Outline of a classic gear: a ring with `teeth` rectangular teeth, one closed path."""
    from PySide6.QtGui import QPainterPath,QTransform
    path=QPainterPath();path.addEllipse(center,body,body)
    for index in range(teeth):
        rect=QPainterPath();rect.addRoundedRect(QRectF(-tooth/2,-tip,tooth,tip-body+1.2),.8,.8)
        transform=QTransform();transform.translate(center.x(),center.y());transform.rotate(index*360/teeth)
        path=path.united(transform.map(rect))
    return path.simplified()


class NavButton(Hover,QPushButton):
    """Narrow sidebar button with a small hand-drawn glyph."""
    def __init__(self,kind,parent=None):
        super().__init__('',parent)
        self.kind=kind;self.setCheckable(True);self.setFixedSize(48,48);self.init_hover()

    def glyph(self,p):
        c=QPointF(24,24)
        if self.kind=='bloodweb':
            for angle in range(0,360,60):
                point=QPointF(24+12*math.cos(math.radians(angle)),24+12*math.sin(math.radians(angle)))
                p.drawLine(c,point);p.drawEllipse(point,2.4,2.4)
            p.drawEllipse(c,3.5,3.5)
        elif self.kind=='match':
            for dx,dy in ((0,-7),(-7,0),(7,0),(0,7)):
                p.drawPolygon(QPolygonF([QPointF(24+dx,24+dy-5.5),QPointF(24+dx+5.5,24+dy),QPointF(24+dx,24+dy+5.5),QPointF(24+dx-5.5,24+dy)]))
        elif self.kind=='memes':  # Laughing face: ring, two squinting eyes and an open grin.
            p.drawEllipse(c,12,12)
            for x in (19.5,28.5):p.drawArc(QRectF(x-3,18,6,6),20*16,140*16)
            p.drawChord(QRectF(17,19,14,14),200*16,140*16)
        elif self.kind=='crosshair':
            p.drawEllipse(c,9,9)
            for a,b in (((24,11),(24,19)),((24,29),(24,37)),((11,24),(19,24)),((29,24),(37,24))):p.drawLine(QPointF(*a),QPointF(*b))
        elif self.kind=='onevone':  # Stopwatch: ring, crown button and one hand.
            p.drawEllipse(QPointF(24,26),10,10);p.drawLine(QPointF(24,13),QPointF(24,16));p.drawLine(QPointF(21,13),QPointF(27,13))
            p.drawLine(QPointF(24,26),QPointF(24,20));p.drawLine(QPointF(24,26),QPointF(28,28))
        elif self.kind=='maps':  # Folded map: three panels and a pin.
            p.drawPolygon(QPolygonF([QPointF(12,16),QPointF(20,13),QPointF(28,16),QPointF(36,13),QPointF(36,33),QPointF(28,36),QPointF(20,33),QPointF(12,36)]))
            p.drawLine(QPointF(20,13),QPointF(20,33));p.drawLine(QPointF(28,16),QPointF(28,36))
        elif self.kind=='settings':
            p.drawPath(gear_path(c));p.drawEllipse(c,3.6,3.6)
        else:
            for y,x in ((16,33),(22,33),(28,27),(34,31)):p.drawLine(QPointF(15,y),QPointF(x,y))

    def paintEvent(self,event):
        p=QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        rect=QRectF(self.rect()).adjusted(2,2,-2,-2)
        if self.isChecked():
            p.setPen(Qt.NoPen);p.setBrush(red_gradient(self.width(),self.progress));p.drawRoundedRect(rect,14,14)
        elif self.progress>0 or self.isDown():
            p.setPen(Qt.NoPen);p.setBrush(QColor(170,24,36,round(120*(.7 if self.isDown() else self.progress))));p.drawRoundedRect(rect,14,14)
        p.setBrush(Qt.NoBrush);p.setPen(QPen(QColor('#ffffff') if self.isChecked() else QColor(205,190,190),1.6))
        self.glyph(p)


class IconGrid(QWidget):
    def __init__(self,root,on_toggle,parent=None,on_details=None):
        super().__init__(parent)
        self.root,self.on_toggle,self.on_details=root,on_toggle,on_details
        self.layout=QGridLayout(self)
        self.layout.setSizeConstraint(QLayout.SetNoConstraint)
        self.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        self.layout.setContentsMargins(4,4,4,4); self.layout.setSpacing(7)
        self.buttons={}; self.entries=[]; self.columns=0; self.anchor=None

    def set_entries(self,entries,selected,display_images=None):
        while self.layout.count():
            item=self.layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.entries=entries; self.buttons={}; self.columns=0
        for entry in entries:
            button=IconTile(self)
            button.setObjectName('iconTile'); button.setCheckable(True)
            button.setFixedSize(52,52); button.setIconSize(QSize(46,46))
            button.setIcon(QIcon(str(self.root/(display_images or {}).get(entry.id,entry.image)))); button.setChecked(entry.id in selected)
            button.setToolTip(entry.id)
            button.clicked.connect(lambda checked,ident=entry.id:self.on_toggle(ident))
            if self.on_details:button.details_requested.connect(lambda ident=entry.id,tile=button:self.open_details(ident,tile))
            self.buttons[entry.id]=button
        self.reflow()

    def open_details(self,ident,tile):
        self.anchor=QRect(tile.mapToGlobal(QPoint(0,0)),tile.size());self.on_details(ident)

    def reflow(self):
        columns=max(1,(self.width()-1)//59)
        if columns==self.columns: return
        for col in range(max(self.columns,columns)+1): self.layout.setColumnStretch(col,0)
        self.columns=columns
        for i,entry in enumerate(self.entries):
            self.layout.addWidget(self.buttons[entry.id],i//columns,i%columns,Qt.AlignLeft)
        for col in range(columns): self.layout.setColumnStretch(col,0)
        self.layout.setColumnStretch(columns,1)
        self.setMinimumHeight(max(1,(len(self.entries)+columns-1)//columns)*59+8)

    def resizeEvent(self,event):
        super().resizeEvent(event); self.reflow()


class Preview(QLabel):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.original=QPixmap(); self.setAlignment(Qt.AlignCenter); self.setMinimumHeight(160)
        self.setMinimumWidth(0); self.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Ignored)

    def set_image(self,path):
        self.original=QPixmap(str(path)); self.refresh()

    def set_pixels(self,pixels):
        from PySide6.QtGui import QImage
        h,w=pixels.shape[:2]
        image=QImage(pixels.data,w,h,pixels.strides[0],QImage.Format_BGR888).copy()
        self.original=QPixmap.fromImage(image);self.refresh()

    def refresh(self):
        if not self.original.isNull():
            size=self.size().boundedTo(self.original.size())
            self.setPixmap(self.original.scaled(size,Qt.KeepAspectRatio,Qt.SmoothTransformation))

    def mouseDoubleClickEvent(self,event):
        if self.original.isNull(): return
        dialog=QDialog(self); dialog.setWindowTitle('AutoBloodWeb'); dialog.resize(1000,850)
        layout=QVBoxLayout(dialog); preview=Preview(); preview.original=self.original
        layout.addWidget(preview); dialog.exec()

    def resizeEvent(self,event):
        super().resizeEvent(event); self.refresh()
