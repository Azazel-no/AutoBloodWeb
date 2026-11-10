from PySide6.QtCore import QThread, Signal
from src.core.automation import Automation,State
import threading


class PurchaseRecheckWorker(QThread):
    completed=Signal(object,object,object)
    failed=Signal(str,str)
    def __init__(self,context,library,role,character,mode,hold_seconds,report,parent=None):
        super().__init__(parent); self.stop=threading.Event()
        self.args=(context,library,role,character,mode,hold_seconds,report)
    def requestInterruption(self):
        self.stop.set(); super().requestInterruption()
    def run(self):
        from src.core.bloodweb_detector import BloodwebDetector
        from src.core.screen_manager import ScreenManager,ScreenError
        from src.core.calibration_recheck import verify_previous
        context,library,role,character,mode,duration,report=self.args
        detector=BloodwebDetector(library,role,character=character); screen=ScreenManager(detector=detector)
        try:
            receipt,frame,result=verify_previous(context,screen,detector,mode,duration,self.stop,report)
            result.live_frame=frame; result.raw_image=frame.image; result.renderer=detector; result.capture_manager=screen
            self.completed.emit(receipt,frame,result)
        except ScreenError as exc: self.failed.emit(exc.code,exc.detail)
        except InterruptedError: self.failed.emit('interrupted','')
        except Exception as exc: self.failed.emit('purchase_not_confirmed',str(exc))


class AutomationWorker(QThread):
    update=Signal(object,str,object)

    def __init__(self,engine,calibrate=False,parent=None):
        super().__init__(parent); self.engine=engine; self.calibrate=calibrate
        engine.event=self.update.emit

    def run(self):
        self.engine.run(self.calibrate)


def make_engine(library,role,priorities,settings,approved,hotkeys):
    from datetime import datetime
    import uuid
    from src.core.bloodweb_detector import BloodwebDetector
    from src.core.screen_manager import ScreenManager
    from src.core.input_control import Mouse
    detector=BloodwebDetector(library,role,character=settings.character)
    screen=ScreenManager(detector=detector)
    screen.read_bloodpoints=settings.bloodpoint_checks  # Only nodes matter; the BP counter is not read.
    engine=Automation(screen,detector,Mouse(),priorities,settings,
        approved=approved,hotkeys_ready=hotkeys,
        report_path=library.root/'debug/actions'/f'{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.json')
    engine.report.flush_interval=.2
    screen.probe_guard=engine.gate
    def probe_recovery(detail,visible_fallback):
        engine.trace('probe_capture_recovery',detail=detail,visible_fallback=visible_fallback,paid_inputs=0)
        engine.emit(State.RECOVERING,'probe_visible_fallback' if visible_fallback else 'probe_capture_recovery')
    screen.probe_recovery=probe_recovery
    from src.core.action_outcomes import ActionOutcomes
    engine.outcome_journal=ActionOutcomes(library.root/'data/action_outcomes.json')
    if settings.native_chain:
        from src.core.native_chain_runtime import install
        install(engine,library.root)
    from src.core.direct_buy import install as install_direct
    install_direct(engine,library.root)
    return engine
