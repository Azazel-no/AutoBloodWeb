import json
from types import SimpleNamespace
import pytest
from PySide6.QtCore import Qt,QPointF,QPoint
from PySide6.QtTest import QTest
from tests.test_gui import app,isolate_preferences_and_hotkeys
from src.gui.main_window import MainWindow

def exercise(window,monkeypatch,action):
    row=window.priority_list;row.setCurrentRow(0);before=row.identities();stages=[]
    class Drag:
        def __init__(self,parent):self.row=parent
        def setMimeData(self,mime):self.mime=mime
        def setPixmap(self,pixmap):assert not pixmap.isNull()
        def setHotSpot(self,point):pass
        @staticmethod
        def cancel():stages.append('cancel')
        def exec(self,*args):
            assert self.row.identities()==before
            row._preview(QPoint(row.viewport().width()-10,30));assert row.identities()==before
            class Drop:
                accepted=False
                def source(self):return row
                def mimeData(event):return self.mime
                def position(self):return QPointF(row.viewport().width()-10,30)
                def setDropAction(self,action):assert action==Qt.MoveAction
                def accept(self):self.accepted=True
                def ignore(self):self.accepted=False
            event=Drop()
            if action=='escape':QTest.keyClick(row,Qt.Key_Escape);return Qt.IgnoreAction
            if action=='outside':event.position=lambda:QPointF(row.viewport().width()-10,row.viewport().height()+20)
            if action=='context':row.populate(before,window.entries_by_id,window.root,context=('killer','another_character'))
            row.dropEvent(event);stages.append(event.accepted)
            return Qt.MoveAction if event.accepted else Qt.IgnoreAction
    monkeypatch.setattr('src.gui.priority_list.QDrag',Drag)
    row.startDrag(Qt.MoveAction)
    return before,row.identities(),stages

@pytest.mark.parametrize('role',[0,1])
def test_whole_source_drag_lifecycle_preserves_and_persists_ids(app,library,monkeypatch,role):
    w=MainWindow(library);w.role.setCurrentIndex(role);w.resize(640,900);w.show();app.processEvents()
    ids=[e.id for e in library.filter(w.current_role,character=w.character.currentData())][:5]
    w.prefs.priorities[w.current_role]=ids[:];w.refresh_selected();app.processEvents()
    before,after,stages=exercise(w,monkeypatch,'drop')
    assert after==before[1:]+before[:1] and len(after)==len(set(after)) and stages==[True]
    saved=json.loads(w.preferences_path.read_text(encoding='utf-8'))
    assert saved['priorities'][w.current_role]==after
    if role:assert saved['killer_priorities'][w.character.currentData()]==after
    w.close();restored=MainWindow(library);assert restored.active_selected==after;restored.close()

@pytest.mark.parametrize('action',['outside','escape','context'])
def test_cancel_or_context_switch_changes_no_order(app,library,monkeypatch,action):
    w=MainWindow(library);w.resize(640,900);w.show();app.processEvents()
    ids=[e.id for e in library.filter(w.current_role)][:5];w.prefs.priorities[w.current_role]=ids;w.refresh_selected();app.processEvents()
    saved=w.preferences_path.read_bytes() if w.preferences_path.exists() else None
    before,after,stages=exercise(w,monkeypatch,action)
    assert before==after and (saved is None or w.preferences_path.read_bytes()==saved)
    if action=='escape':assert 'cancel' in stages
    w.close()

def test_drag_preview_reuses_same_items_on_gui_refresh(app,library):
    w=MainWindow(library);w.resize(420,700);w.show();app.processEvents()
    ids=[e.id for e in library.filter(w.current_role)][:14];w.prefs.priorities[w.current_role]=ids;w.refresh_selected();app.processEvents()
    row=w.priority_list;first=row.item(0);row._drag_id=ids[0];row._drag_snapshot=tuple(ids);row._drag_context=row._context
    row._preview(QPoint(row.viewport().width()-10,30));w.refresh_selected()
    assert row.item(0) is first and row.identities()==ids and row._inside
    row._drag_id=None;w.close()
