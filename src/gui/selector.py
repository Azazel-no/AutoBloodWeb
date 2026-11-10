from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox,QStyle,QStyleOptionComboBox,QStylePainter,QSizePolicy

class ElidedCombo(QComboBox):
    def __init__(self,parent=None):
        super().__init__(parent);self.setMinimumWidth(0);self.setMinimumHeight(54)
        self.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed)
        self.currentIndexChanged.connect(lambda:self.setToolTip(self.currentText()))

    def paintEvent(self,event):
        painter=QStylePainter(self);option=QStyleOptionComboBox();self.initStyleOption(option)
        rect=self.style().subControlRect(QStyle.CC_ComboBox,option,QStyle.SC_ComboBoxEditField,self)
        width=rect.width()-8-(self.iconSize().width()+5 if not option.currentIcon.isNull() else 0)
        option.currentText=self.fontMetrics().elidedText(option.currentText,Qt.ElideRight,max(0,width))
        painter.drawComplexControl(QStyle.CC_ComboBox,option);painter.drawControl(QStyle.CE_ComboBoxLabel,option)
