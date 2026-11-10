import json,time,uuid
from PySide6.QtCore import Qt, QSize, Signal,QMimeData,QPoint,QRect,QTimer
from PySide6.QtGui import QIcon,QDrag,QPixmap,QPainter,QColor,QPen
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QAbstractItemView,QListView,QStyleOptionViewItem,QStyle,QStyledItemDelegate


class PriorityDelegate(QStyledItemDelegate):
    """The selected item gets only a red outline: no highlight or focus shape on its number."""
    def paint(self,painter,option,index):
        selected=bool(option.state&QStyle.State_Selected)
        plain=QStyleOptionViewItem(option);plain.state&=~(QStyle.State_Selected|QStyle.State_HasFocus)
        super().paint(painter,plain,index)
        if selected:
            painter.save();painter.setRenderHint(QPainter.Antialiasing);painter.setBrush(Qt.NoBrush);painter.setPen(QPen(QColor(216,50,71),2))
            painter.drawRoundedRect(option.rect.adjusted(2,2,-2,-2),9,9);painter.restore()


class PriorityList(QListWidget):
    reordered=Signal(list)
    preview_changed=Signal(int)
    details_requested=Signal(str)
    remove_requested=Signal(str)
    MIME='application/x-autobloodweb-priority'

    def __init__(self,parent=None):
        super().__init__(parent)
        self.setItemDelegate(PriorityDelegate(self))
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction); self.setIconSize(QSize(38,38)); self.setFixedHeight(126)
        self.setViewMode(QListView.IconMode);self.setFlow(QListView.LeftToRight);self.setWrapping(True)
        self.setMovement(QListView.Snap);self.setResizeMode(QListView.Adjust);self.setSpacing(4)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded);self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAutoScroll(True);self.setAutoScrollMargin(24);self.setDragEnabled(True);self.setAcceptDrops(True)
        self.setDropIndicatorShown(False);self.setSelectionMode(QAbstractItemView.SingleSelection)
        self._drag_id=None;self._drag_token=None;self._drag_snapshot=();self._preview_slot=None
        self._context=None;self._drag_context=None;self._hover=None;self.allow_remove=True;self.anchor=None
        self.setMouseTracking(True);self.viewport().setMouseTracking(True)
        self._drag_position=QPoint();self._press_position=QPoint();self._inside=False;self._last_scroll=0.
        self._scroll_timer=QTimer(self);self._scroll_timer.setInterval(40);self._scroll_timer.timeout.connect(self._scroll_edge)
        self.model().rowsMoved.connect(self.publish)

    def identities(self):
        return [self.item(i).data(Qt.UserRole) for i in range(self.count())]

    def populate(self,ids,entries,root,display_images=None,context=None):
        if self._drag_id is not None and context!=self._context:QDrag.cancel()
        self._context=context
        if list(ids)==self.identities():
            for i,ident in enumerate(ids):
                self.item(i).setIcon(QIcon(str(root/(display_images or {}).get(ident,entries[ident].image))))
            return
        if self._drag_id is not None:QDrag.cancel()
        current=self.currentRow(); self.clear()
        for index,ident in enumerate(ids):
            item=QListWidgetItem(QIcon(str(root/(display_images or {}).get(ident,entries[ident].image))),f'{index+1}.')
            item.setData(Qt.UserRole,ident); item.setToolTip(ident); self.addItem(item)
            item.setSizeHint(QSize(54,56))
        self.setCurrentRow(min(current,len(ids)-1))

    def publish(self,*args):
        ids=self.identities()
        for i in range(self.count()): self.item(i).setText(f'{i+1}.')
        self.reordered.emit(ids)

    def move_current(self,offset):
        old=self.currentRow(); new=old+offset
        if old<0 or not 0<=new<self.count(): return
        self.move_identity(self.item(old).data(Qt.UserRole),new)

    def move_identity(self,ident,new):
        ids=self.identities()
        if ids.count(ident)!=1 or not 0<=new<len(ids):return False
        old=ids.index(ident)
        if old==new:return False
        item=self.takeItem(old);self.insertItem(new,item);self.setCurrentRow(new);self.publish()
        return True

    def contextMenuEvent(self,event):
        item=self.itemAt(event.pos())
        if item is not None:
            rect=self.visualItemRect(item);self.anchor=QRect(self.viewport().mapToGlobal(rect.topLeft()),rect.size())
            self.details_requested.emit(item.data(Qt.UserRole));event.accept()

    def _close_rect(self,row):
        rect=self.visualItemRect(self.item(row));return QRect(rect.right()-15,rect.top()+1,15,15)

    def _set_hover(self,row):
        if row!=self._hover:self._hover=row;self.viewport().update()

    def mouseMoveEvent(self,event):
        item=self.itemAt(event.position().toPoint());self._set_hover(None if item is None else self.row(item))
        super().mouseMoveEvent(event)

    def leaveEvent(self,event):
        self._set_hover(None);super().leaveEvent(event)

    def mousePressEvent(self,event):
        point=event.position().toPoint();item=self.itemAt(point)
        if item is not None and self.allow_remove and event.button()==Qt.LeftButton and self._close_rect(self.row(item)).contains(point):
            self._hover=None;self.remove_requested.emit(item.data(Qt.UserRole));event.accept();return
        self._press_position=point;super().mousePressEvent(event)

    def keyPressEvent(self,event):
        if event.key()==Qt.Key_Escape and self._drag_id is not None:
            self.cancel_drag();event.accept();return
        super().keyPressEvent(event)

    def cancel_drag(self):
        if self._drag_id is not None:QDrag.cancel()

    def startDrag(self,supportedActions):
        old=self.currentRow()
        if old<0:return
        self._drag_id=self.item(old).data(Qt.UserRole);self._drag_token=uuid.uuid4().hex
        self._drag_snapshot=tuple(self.identities());self._preview_slot=old;self._inside=False
        self._drag_context=self._context
        drag=QDrag(self);mime=QMimeData();mime.setData(self.MIME,json.dumps(dict(id=self._drag_id,token=self._drag_token)).encode('utf-8'));drag.setMimeData(mime)
        rect=self.visualItemRect(self.item(old));source=self.viewport().grab(rect)
        ghost=QPixmap(source.size());ghost.setDevicePixelRatio(source.devicePixelRatio());ghost.fill(Qt.transparent)
        painter=QPainter(ghost);painter.setOpacity(.62);painter.drawPixmap(0,0,source);painter.end()
        drag.setPixmap(ghost);drag.setHotSpot(self._press_position-rect.topLeft())
        try:
            # Do not call QAbstractItemView.startDrag: after MoveAction it can
            # remove the selected row although our drop already moved it.
            drag.exec(Qt.MoveAction,Qt.MoveAction)
        finally:
            self._scroll_timer.stop();self._drag_id=None;self._drag_token=None;self._drag_snapshot=()
            self._preview_slot=None;self._inside=False;self.setState(QAbstractItemView.NoState)
            self.preview_changed.emit(-1);self.viewport().update()

    def _valid_drag(self,event):
        if event.source() is not self or self._drag_id is None or self._drag_snapshot!=tuple(self.identities()) or self._drag_context!=self._context:return False
        try:
            data=json.loads(bytes(event.mimeData().data(self.MIME)))
            return data==dict(id=self._drag_id,token=self._drag_token)
        except (ValueError,TypeError,AttributeError):return False

    def _slot(self,point):
        if not self.count():return None
        if self._preview_slot is not None:
            rect=self.visualItemRect(self.item(self._preview_slot)).adjusted(-4,0,4,0)
            if rect.contains(point):return self._preview_slot
        target=self.itemAt(point)
        if target is not None:return self.row(target)
        return min(range(self.count()),key=lambda i:((self.visualItemRect(self.item(i)).center()-point).manhattanLength(),-i))

    def _preview(self,point):
        self._drag_position=point;self._inside=self.viewport().rect().contains(point)
        slot=self._slot(point) if self._inside else None
        if slot!=self._preview_slot:self._preview_slot=slot;self.preview_changed.emit(-1 if slot is None else slot)
        self.viewport().update()

    def dragEnterEvent(self,event):
        if not self._valid_drag(event):event.ignore();return
        event.setDropAction(Qt.MoveAction);event.accept();self._preview(event.position().toPoint());self._scroll_timer.start()

    def dragMoveEvent(self,event):
        if not self._valid_drag(event):event.ignore();return
        event.setDropAction(Qt.MoveAction);event.accept();self._preview(event.position().toPoint());self._scroll_edge()

    def dragLeaveEvent(self,event):
        self._inside=False;self._scroll_timer.stop();self.viewport().update();event.accept()

    def _scroll_edge(self):
        if not self._inside or self._drag_id is None or time.monotonic()-self._last_scroll<.04:return
        y=self._drag_position.y();height=self.viewport().height();bar=self.verticalScrollBar()
        direction=-1 if y<24 else 1 if y>height-24 else 0
        if direction:
            self._last_scroll=time.monotonic();bar.setValue(bar.value()+direction*12);self._preview(self._drag_position)

    def dropEvent(self,event):
        # Actual Qt events always have mimeData. The no-mime branch retains
        # the existing model-only injected regression, not a native entry path.
        injected=not hasattr(event,'mimeData')
        if event.source() is not self or not injected and not self._valid_drag(event):event.ignore();return
        point=event.position().toPoint()
        if not self.viewport().rect().contains(point):event.ignore();return
        ident=self.item(self.currentRow()).data(Qt.UserRole) if injected and self.currentRow()>=0 else self._drag_id
        slot=self._slot(point)
        if ident is None or slot is None:event.ignore();return
        self._scroll_timer.stop();self.move_identity(ident,slot)
        if hasattr(event,'setDropAction'):event.setDropAction(Qt.MoveAction);event.accept()
        else:event.acceptProposedAction()

    def paintEvent(self,event):
        if not (self._inside and self._preview_slot is not None and self._drag_id in self.identities() and self._drag_snapshot==tuple(self.identities())):
            super().paintEvent(event)
            if self.allow_remove and self._hover is not None and self._hover<self.count() and self._drag_id is None:
                rect=self._close_rect(self._hover);p=QPainter(self.viewport());p.setRenderHint(QPainter.Antialiasing)
                p.setPen(Qt.NoPen);p.setBrush(QColor(200,28,44));p.drawEllipse(rect)
                p.setPen(QPen(QColor('#ffffff'),1.6));c=rect.center();p.drawLine(c.x()-3,c.y()-3,c.x()+4,c.y()+4);p.drawLine(c.x()+4,c.y()-3,c.x()-3,c.y()+4);p.end()
            return
        order=list(range(self.count()));old=self.identities().index(self._drag_id);order.remove(old);order.insert(self._preview_slot,old)
        painter=QPainter(self.viewport());painter.fillRect(self.viewport().rect(),QColor(14,9,10))
        for destination,source in enumerate(order):
            rect=self.visualItemRect(self.item(destination))
            if not rect.intersects(self.viewport().rect()):continue
            option=QStyleOptionViewItem();self.initViewItemOption(option)
            self.itemDelegate().initStyleOption(option,self.model().index(source,0));option.rect=rect;option.text=f'{destination+1}.'
            painter.save()
            if source==old:
                painter.setOpacity(.35);painter.fillRect(rect,QColor(200,28,44,100))
            self.style().drawControl(QStyle.CE_ItemViewItem,option,painter,self)
            painter.restore()
        rect=self.visualItemRect(self.item(self._preview_slot));painter.setPen(QPen(QColor(232,60,76),3))
        painter.drawLine(rect.left()+1,rect.top()+2,rect.left()+1,rect.bottom()-2);painter.end()

    def wheelEvent(self,event):
        delta=event.pixelDelta().y() or event.angleDelta().y()/2
        bar=self.verticalScrollBar();bar.setValue(bar.value()-round(delta));event.accept()
