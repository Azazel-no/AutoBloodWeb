"""A searchable combo popup; filtering never changes the selected killer ID."""
from PySide6.QtCore import Qt,QPoint,QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QComboBox,QFrame,QVBoxLayout,QLineEdit,QListWidget,QListWidgetItem
from src.gui.selector import ElidedCombo


class KillerSelector(ElidedCombo):
    def __init__(self,root,parent=None):
        super().__init__(parent);self.root=root;self.search_tokens={};self.popup=None;self.search_hint=''

    def set_search_tokens(self,index,tokens):
        self.search_tokens[index]=' '.join(tokens).casefold().replace('ё','е')

    def showPopup(self):
        if self.popup:self.popup.close()
        self.popup=QFrame(self,Qt.Popup);layout=QVBoxLayout(self.popup)
        self.popup.setObjectName('card')
        self.search_line=QLineEdit();self.search_line.setPlaceholderText(self.search_hint or self.toolTip())
        self.search_line.addAction(QIcon(str(self.root/'assets/ui/search.svg')),QLineEdit.LeadingPosition)
        layout.addWidget(self.search_line);self.search_results=QListWidget();layout.addWidget(self.search_results)
        self.search_results.setIconSize(QSize(36,36))
        for index in range(self.count()):
            item=QListWidgetItem(self.itemIcon(index),self.itemText(index));item.setData(Qt.UserRole,index)
            self.search_results.addItem(item)
        self.search_results.itemClicked.connect(self.choose_item)
        self.search_line.textChanged.connect(self.filter_results)
        self.search_line.returnPressed.connect(self.choose_first)
        self.popup.resize(max(280,self.width()),360)
        position=self.mapToGlobal(QPoint(0,self.height()));available=self.screen().availableGeometry()
        position.setY(min(position.y(),available.bottom()-self.popup.height()+1))
        position.setX(min(position.x(),available.right()-self.popup.width()+1))
        self.popup.move(position);self.popup.show();self.search_line.setFocus()

    def filter_results(self,text):
        query=text.strip().casefold().replace('ё','е')
        for index in range(self.search_results.count()):
            self.search_results.item(index).setHidden(query not in self.search_tokens.get(index,self.itemText(index).casefold()))

    def choose_item(self,item):
        self.setCurrentIndex(item.data(Qt.UserRole));self.popup.close()

    def choose_first(self):
        item=next((self.search_results.item(i) for i in range(self.search_results.count()) if not self.search_results.item(i).isHidden()),None)
        if item:self.choose_item(item)
