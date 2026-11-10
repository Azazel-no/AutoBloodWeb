import json,time
from types import SimpleNamespace
from PySide6.QtCore import Qt,QThread,Signal,QPointF
from tests.test_gui import app,isolate_preferences_and_hotkeys
from tests.test_v44 import receipt,spin_until
from tests.test_automation import Device,web
from src.gui.main_window import MainWindow
from src.core.models import NodeState
from src.core.automation import Automation

def test_start_automatically_runs_with_registered_stop_without_physical_check(app,library,monkeypatch):
    class Capture(Device):
        def validate_click(self,frame,x,y,**kwargs):return super().validate_click(frame,x,y)
        def analyze(self,*args):return web(NodeState.PURCHASED if self.presses else NodeState.AVAILABLE)
    d=Capture([web()]);d.backend=SimpleNamespace(info=lambda:d.window);captures=[];created=[]
    class Worker(QThread):
        completed=Signal(object);failed=Signal(str)
        def __init__(self,*a,**k):super().__init__()
        def run(self):
            r=web();r.live_frame=d.capture();r.capture_manager=d;captures.append(r);self.completed.emit(r)
    def factory(library,role,priorities,settings,approved,hotkeys):
        assert settings.startup_delay==3 and approved and hotkeys
        settings.startup_delay=0  # Countdown is covered with a virtual clock.
        settings.max_actions=1;settings.poll_interval=.01
        runner=Automation(d,d,d,priorities,settings,approved=approved,hotkeys_ready=hotkeys)
        created.append(runner);return runner
    monkeypatch.setattr('src.gui.main_window.AnalysisWorker',Worker);monkeypatch.setattr('src.gui.main_window.make_engine',factory)
    w=MainWindow(library);w.calibration=receipt();w.dry_run.setChecked(False);w.preparation_window_check=lambda:d.window
    w.hotkeys.native_health=lambda:dict(ready=True)
    w.buttons['start'].click()
    # This checks asynchronous completion, not the production countdown (which
    # the injected factory already disables). A busy full suite may take >3s
    # to drain Qt's queued updates; keep a bounded independent test deadline.
    spin_until(lambda:created and w.auto_worker is None,app,timeout=10)
    assert not captures and len(d.presses)==1 and not w.session_check
    assert sum(e['stage']=='purchase_observation' for e in created[0].report.entries)>=2
    w.close()

def test_start_stop_cancels_preparation_without_input(app,library,monkeypatch):
    w=MainWindow(library);w.calibration=receipt();w.dry_run.setChecked(False)
    from tests.test_automation import engine
    d=Device([web()]);d.backend=SimpleNamespace(info=lambda:d.window)
    runner=engine(d,dry_run=False);w.hotkeys.native_health=lambda:dict(ready=True)
    monkeypatch.setattr('src.gui.main_window.make_engine',lambda *a:runner)
    monkeypatch.setattr('src.gui.main_window.AutomationWorker.start',lambda self:None)
    w.start_automation();assert w.auto_worker and not w.session_check
    w.stop_analysis();w.automation_finished()  # Complete the injected, unstarted worker.
    assert w.session_check is None and w.auto_worker is None and not w.countdown.isVisible()
    assert runner.stop.is_set() and d.releases and not d.presses
    w.close()

def test_equal_selectors_and_wrapped_priority_rows_persist_drop(app,library):
    w=MainWindow(library);w.role.setCurrentIndex(1);w.resize(420,700);w.show();app.processEvents()
    assert abs(w.role.width()-w.character.width())<=1 and w.role.height()==w.character.height()
    ids=[e.id for e in library.filter('killer',character=w.character.currentData())][:14]
    for ident in ids:w.toggle_icon(ident)
    app.processEvents();lst=w.priority_list
    assert lst.isWrapping() and lst.horizontalScrollBarPolicy()==Qt.ScrollBarAlwaysOff
    assert len({lst.visualItemRect(lst.item(i)).top() for i in range(lst.count())})>=2
    lst.setCurrentRow(0);point=lst.visualItemRect(lst.item(3)).bottomRight()
    class Drop:
        def source(self):return lst
        def position(self):return QPointF(point)
        def acceptProposedAction(self):self.accepted=True
    event=Drop();lst.dropEvent(event)
    expected=ids[1:4]+ids[:1]+ids[4:]
    assert w.active_selected==expected and event.accepted
    saved=json.loads(w.preferences_path.read_text(encoding='utf-8'))
    assert saved['killer_priorities'][w.character.currentData()]==expected
    w.language.setCurrentIndex(1);app.processEvents();assert w.active_selected==expected
    w.close()
