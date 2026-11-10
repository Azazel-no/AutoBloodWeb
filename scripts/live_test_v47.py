"""Authorized native GUI run: at most 50 Mouse calls including retries.
Uses the real Start/controller/worker/F9 checks. No injected keyboard or focus.
User authorization is recorded in this chat; this is not a general launcher.
"""
import sys,json,time,uuid,hashlib,logging
from pathlib import Path
from dataclasses import asdict
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from PySide6.QtCore import QTimer,Qt
from PySide6.QtWidgets import QApplication,QLabel
from src.core.icon_library import IconLibrary
from src.core.screen_manager import ScreenError
import src.gui.main_window as gui

class InputBudget:
    def __init__(self,limit=50,ledger=None):
        self.limit=limit;self.used=0;self.ledger=ledger
        if ledger is not None and ledger.exists():
            data=json.loads(ledger.read_text(encoding='utf-8'))
            if data.get('authorized_limit')!=limit or type(data.get('used')) is not int or not 0<=data['used']<=limit:
                raise ValueError('permission_budget_invalid')
            self.used=data['used']
    def invoke(self,purchase,point,duration,stop,check):
        if self.used>=self.limit:raise ScreenError('action_limit')
        self.used+=1
        if self.ledger is not None:
            temporary=self.ledger.with_suffix('.json.tmp')
            temporary.write_bytes(json.dumps(dict(authorized_limit=self.limit,used=self.used),indent=2).encode('utf-8'));temporary.replace(self.ledger)
        return purchase(point,duration,stop,check)

def main():
    folder=ROOT/'debug/v47'/('live_session_'+uuid.uuid4().hex[:8]);folder.mkdir()
    prefs=ROOT/'data/preferences.json';(folder/'preferences_before.json').write_bytes(prefs.read_bytes())
    logging.basicConfig(level=logging.INFO,handlers=[logging.FileHandler(folder/'app.log',encoding='utf-8')])
    budget=InputBudget(ledger=ROOT/'debug/v47/live_permission_20261013.json');engines=[];original_factory=gui.make_engine;native_events=[]
    def factory(library,role,priorities,settings,approved,hotkeys):
        remaining=budget.limit-budget.used
        if remaining<=0:raise ScreenError('action_limit')
        settings.max_actions=min(settings.max_actions or remaining,remaining)
        settings.max_runtime=min(settings.max_runtime or 600,600);settings.auto_prestige=False
        e=original_factory(library,role,priorities,settings,approved,hotkeys)
        original_purchase=e.mouse.purchase
        e.mouse.purchase=lambda point,duration,stop,check:budget.invoke(original_purchase,point,duration,stop,check)
        original_audit=e.mouse.audit
        def audit(stage,**details):
            original_audit(stage,**details)
            if stage in ('SendInput','SetPhysicalCursorPos','GetPhysicalCursorPos','cursor_before_down'):
                native_events.append(dict(stage=stage,monotonic=time.monotonic(),**details))
                (folder/'native_input.json').write_bytes(json.dumps(native_events,ensure_ascii=False,indent=2).encode('utf-8'))
        e.mouse.audit=audit
        engines.append(e);return e
    gui.make_engine=factory
    class Window(gui.MainWindow):
        def start_automation(self,*args,**kwargs):
            if budget.used>=budget.limit:
                self.status_key='paused';self.error_message=self.locale.text('action_limit');self.refresh_status();return
            return super().start_automation(*args,**kwargs)
    app=QApplication([]);window=Window(IconLibrary(ROOT).build())
    window.setWindowTitle('AutoBloodWeb — live test, maximum 50 inputs')
    window.setAttribute(Qt.WA_ShowWithoutActivating,True)
    label=QLabel('Живой тест: максимум 50 вводов, включая повторы; без престижа.')
    label.setWordWrap(True);window.action_panel.layout().insertWidget(0,label)
    session=dict(scope=__doc__,authorized_max_inputs=50,game_keyboard_input=False,programmatic_focus_change=False,
        preferences_before_sha256=hashlib.sha256((folder/'preferences_before.json').read_bytes()).hexdigest(),engines=[])
    def progress():
        session.update(input_calls=budget.used,status=window.status_key,reason=window.error_message,
            preparing=window.session_check is not None,waiting_physical_f9=bool(window.session_check and window.session_check.waiting),
            engines=[dict(actions=e.actions,unconfirmed_level_actions=e.unconfirmed_level_actions,pending=e.pending_action,
                outcome=e.last_purchase_outcome,state=e.state.value,report=str(e.report.path),settings=asdict(e.settings)) for e in engines])
        (folder/'session.json').write_bytes(json.dumps(session,ensure_ascii=False,indent=2).encode('utf-8'))
        label.setText(f'Живой тест: {budget.used}/50 вводов. F9 — немедленная остановка. Престиж вручную.')
    timer=QTimer(window);timer.setInterval(200);timer.timeout.connect(progress);timer.start()
    window.show();progress();print(folder,flush=True)
    QTimer.singleShot(700,window.start_automation)
    result=app.exec();progress();return result
if __name__=='__main__':raise SystemExit(main())
