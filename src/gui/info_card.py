"""Popup with an item's details, opened with a right click right under its icon."""
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication, QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

STYLE="""
QDialog#infoCard { background: #171012; border: 1px solid #7a1c2b; }
QDialog#infoCard QLabel { color: #e9dfe0; background: transparent; }
QLabel#infoName { font-size: 16px; font-weight: 700; }
QLabel#infoRarity { color: #ffd9de; background: #5c1824; border: 1px solid #a12a3b; border-radius: 8px; padding: 1px 8px; font-size: 11px; }
QLabel#infoOwner, QLabel#infoSource { color: #a8979a; font-size: 11px; }
QLabel#infoFlavor { color: #c9a9ae; font-style: italic; }
QLabel#infoOpinion { color: #d9c9ca; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }
"""
RARITY={'common':'#c08a55','uncommon':'#e8c252','rare':'#3fbf4a','very rare':'#b35cf0','ultra rare':'#ff4f86','event':'#f0a030'}
GAP=6
MAX_BODY=300


def place(anchor,size,bounds):
    """Top-left of a card under `anchor`, centred on it, or above it when below does not fit."""
    x=min(max(anchor.center().x()-size.width()//2,bounds.left()),bounds.right()-size.width()+1)
    below=anchor.bottom()+GAP+1;above=anchor.top()-GAP-size.height()
    if below+size.height()-1<=bounds.bottom():y=below
    elif above>=bounds.top():y=above
    else:y=max(bounds.top(),bounds.bottom()-size.height()+1)
    return QPoint(x,y)


class InfoCard(QDialog):
    WIDTH=340

    def __init__(self,parent=None):
        super().__init__(parent,Qt.Tool|Qt.FramelessWindowHint|Qt.WindowStaysOnTopHint|Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating);self.setObjectName('infoCard');self.setStyleSheet(STYLE);self.setFixedWidth(self.WIDTH)
        self.anchor=None
        layout=QVBoxLayout(self);layout.setContentsMargins(12,10,12,8);layout.setSpacing(6)
        top=QHBoxLayout();top.setSpacing(10);layout.addLayout(top)
        self.icon=QLabel();self.icon.setFixedSize(52,52);top.addWidget(self.icon)
        column=QVBoxLayout();column.setSpacing(3);top.addLayout(column,1)
        self.name=self._label('infoName');column.addWidget(self.name)
        self.rarity=QLabel();self.rarity.setObjectName('infoRarity')
        self.owner=self._label('infoOwner')
        line=QHBoxLayout();line.setSpacing(8);line.addWidget(self.rarity);line.addWidget(self.owner,1);column.addLayout(line)
        self.body=QWidget();self.text=QVBoxLayout(self.body);self.text.setContentsMargins(0,0,6,0);self.text.setSpacing(8)
        self.description=self._label('infoDescription');self.description.setTextFormat(Qt.PlainText);self.text.addWidget(self.description)
        self.flavor=self._label('infoFlavor');self.flavor.setTextFormat(Qt.PlainText);self.text.addWidget(self.flavor)
        self.opinion=self._label('infoOpinion');self.opinion.setTextFormat(Qt.PlainText);self.text.addWidget(self.opinion)
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);self.scroll.setWidget(self.body);layout.addWidget(self.scroll)
        self.source=self._label('infoSource');layout.addWidget(self.source)
        self.watch=QTimer(self);self.watch.setInterval(120);self.watch.timeout.connect(self.check_cursor)

    @staticmethod
    def _label(name):
        label=QLabel();label.setObjectName(name);label.setWordWrap(True);return label

    def show_item(self,image,name,rarity,owner,description,flavor,source,anchor=None,opinion=''):
        self.setWindowTitle(name);self.name.setText(name);self.owner.setText(owner)
        colour=RARITY.get(rarity.lower())
        self.name.setStyleSheet(f'color: {colour};' if colour else '')
        self.icon.setPixmap(QPixmap(str(image)).scaled(52,52,Qt.KeepAspectRatio,Qt.SmoothTransformation))
        self.rarity.setText(rarity);self.rarity.setVisible(bool(rarity))
        self.description.setText(description);self.flavor.setText(flavor);self.flavor.setVisible(bool(flavor))
        self.opinion.setText(opinion);self.opinion.setVisible(bool(opinion))
        self.source.setText(source);self.source.setVisible(bool(source))
        self.scroll.setFixedHeight(min(MAX_BODY,self.text.totalHeightForWidth(self.WIDTH-24-14)+2))
        self.adjustSize();self.anchor=anchor
        shown=anchor if anchor is not None else QRect(QCursor.pos(),QSize(1,1))
        screen=QGuiApplication.screenAt(shown.center()) or QGuiApplication.primaryScreen()
        bounds=screen.availableGeometry()
        owner_window=self.parentWidget()
        if owner_window is not None:bounds=bounds.intersected(owner_window.frameGeometry()) or owner_window.frameGeometry()
        self.move(place(shown,self.size(),bounds))
        self.show();self.raise_()
        if anchor is not None:QApplication.instance().installEventFilter(self);self.watch.start()

    def check_cursor(self):
        """Closes once the cursor has left both the icon and the card (and the small gap between them)."""
        point=QCursor.pos();card=self.frameGeometry()
        if self.anchor is None or not (self.anchor.adjusted(-8,-8,8,8).united(card.adjusted(-8,-8,8,8))).contains(point):self.hide()

    def eventFilter(self,watched,event):
        kind=event.type()
        if kind in (QEvent.ShortcutOverride,QEvent.KeyPress) and event.key()==Qt.Key_Escape:
            self.hide();event.accept();return True
        if kind==QEvent.MouseButtonPress and not self.frameGeometry().contains(event.globalPosition().toPoint()):self.hide()
        return False

    def hideEvent(self,event):
        self.watch.stop();self.anchor=None
        app=QApplication.instance()
        if app:app.removeEventFilter(self)
        super().hideEvent(event)
