"""Read-only session check. No Mouse or purchase worker is constructed."""
import time
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel
from src.core.geometry import purchasable
from src.core.models import ScreenState
from src.core.screen_manager import ScreenError
from src.gui.widgets import BloodButton


class SessionCheckDialog(QDialog):
    def __init__(self,owner,auto_start=False):
        super().__init__(owner)
        self.owner=owner;self.result=None;self.worker=None;self.waiting=False;self.receipt=None;self.closed=False
        self.auto_start=auto_start;self.succeeded=False;self.counting=False
        owner.session_check=self;owner.emergency_tested_signature=None
        self.setWindowTitle(owner.locale.text('session_check'));self.setModal(True)
        layout=QVBoxLayout(self)
        self.status=QLabel(owner.locale.text('session_instructions',shortcut=owner.prefs.settings['hotkeys']['stop']))
        self.status.setWordWrap(True);layout.addWidget(self.status)
        self.scan=BloodButton(owner.locale.text('scan'));self.scan.clicked.connect(self.scan_window);layout.addWidget(self.scan)
        self.close_button=BloodButton(owner.locale.text('stop'));self.close_button.clicked.connect(self.close);layout.addWidget(self.close_button)
        self.timer=QTimer(self);self.timer.setInterval(200);self.timer.timeout.connect(self.tick)
        self.resize(470,190)

    def begin(self):
        self.owner.set_busy(True);self.counting=True
        self.countdown_end=time.monotonic()+self.owner.startup_delay.value()
        self.focus_deadline=self.countdown_end+20;self.timer.start();self.tick()

    def show_status(self,key,**values):
        text=self.owner.locale.text(key,**values);self.status.setText(text)
        if self.auto_start:
            self.owner.status_key='scanning';self.owner.action_stage_message=key
            self.owner.error_message=text;self.owner.refresh_status()

    def scan_window(self):
        if self.closed or self.worker or self.owner.auto_worker or not self.owner.hotkeys.ready:return
        from src.gui.main_window import AnalysisWorker
        self.waiting=False;self.timer.stop();self.scan.setEnabled(False)
        if self.auto_start:self.show_status('auto_start_scanning')
        self.worker=AnalysisWorker(self.owner.library,self.owner.current_role,None,self,
            character=self.owner.character.currentData() or 'blight',detailed=False)
        self.worker.completed.connect(self.checked_result)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.finished_scan);self.worker.start()

    def checked_result(self,result):
        if self.closed:return
        try:
            if not self.owner.hotkeys.ready:raise ScreenError('hotkey_registration_failed')
            frame=getattr(result,'live_frame',None)
            from src.core.modal_recovery import tutorial_target
            tutorial=tutorial_target(frame,result) if frame else None
            allowed=result.screen_state==ScreenState.BLOODWEB or (result.screen_state==ScreenState.PRESTIGE_READY and self.owner.auto_prestige.isChecked()) or tutorial is not None
            if not frame or not frame.live or frame.probe_only or not allowed:raise ScreenError('live_frame_required')
            target=next((n for n in result.nodes if purchasable(n,result.nodes,result.edges)),None)
            if target is None and result.screen_state==ScreenState.PRESTIGE_READY and result.details.get('prestige_ready'):
                target=result.nodes[0]
            if target is None and tutorial is None:raise ScreenError('no_confident_action')
            point=tutorial if tutorial is not None else (target.x,target.y)
            result.capture_manager.validate_click(frame,*point,require_focus=self.auto_start)
            if self.receipt:
                if frame.window.signature!=self.receipt['signature']:raise ScreenError('window_changed')
                if self.result and frame.captured_at<=self.result.live_frame.captured_at:raise ScreenError('stale_frame')
                if self.receipt.get('check_kind')=='registration' and not self.owner.hotkeys.native_health()['ready']:
                    raise ScreenError('hotkey_registration_failed')
                self.owner.calibration_signature=frame.window.signature
                self.owner.emergency_tested_signature=frame.window.signature
                self.owner.session_proof_at=time.monotonic()
                self.owner.current_session_check_kind=self.receipt.get('check_kind','physical')
                self.owner.commit_method_recovery()
                self.owner.hotkey_trace.add('session_check_accepted',analysis_id=result.analysis_id,
                    frame=frame.metadata(),native_event=self.receipt['native_event'],purchase=False,
                    check_kind=self.receipt.get('check_kind','physical'))
                self.status.setText(self.owner.locale.text('session_check_ok'));self.scan.setEnabled(False)
                self.close_button.setText(self.owner.locale.text('apply'));self.receipt=None;self.succeeded=True
                return
            if self.auto_start and hasattr(self.owner.hotkeys,'native_health'):
                health=self.owner.hotkeys.native_health()
                if not health['ready']:raise ScreenError('hotkey_registration_failed')
                self.result=result;self.waiting=False
                self.receipt=dict(signature=frame.window.signature,check_kind='registration',native_event=health)
                self.show_status('auto_start_registered')
                return  # finished_scan starts the second independent capture.
            self.result=result;self.waiting=True;self.deadline=time.monotonic()+15;self.timer.start()
            self.show_status('auto_start_f9' if self.auto_start else 'session_scan_ok',shortcut=self.owner.prefs.settings['hotkeys']['stop'])
        except ScreenError as exc:self.failed(exc.code)

    def receive_emergency_test(self,packet):
        if self.closed:return
        accepted=(self.waiting and time.monotonic()<self.deadline and self.owner.hotkeys.ready and
            packet.get('focus_matches') is True and packet.get('native_event',{}).get('message_verified') is True and
            packet.get('shortcut')==self.owner.prefs.settings['hotkeys']['stop'] and
            packet.get('signature')==self.result.live_frame.window.signature)
        self.owner.hotkey_trace.add('session_f9_received',accepted=accepted,**packet)
        if not accepted:return
        if self.auto_start:self.owner.preparation_hotkey_check_consumed=True
        self.receipt=packet;self.waiting=False;self.timer.stop()
        # Reacquire after the physical key, never trust the old ROI or target.
        self.scan_window()

    def tick(self):
        if self.auto_start and self.succeeded and not self.closed:
            if getattr(self.owner.hotkeys,'stop_pressed',lambda:False)():
                if time.monotonic()>=self.release_deadline:self.failed('session_emergency_timeout')
                return
            if not self.owner.hotkeys.ready:self.failed('hotkey_registration_failed');return
            self.cleanup();self.owner.set_busy(False)
            self.owner._start_prepared=True;self.owner.start_automation();self.deleteLater();return
        if self.auto_start and self.counting:
            remaining=self.countdown_end-time.monotonic()
            self.show_status('auto_start_switch');self.owner.countdown.setText(str(max(0,__import__('math').ceil(remaining))));self.owner.countdown.show()
            if remaining>0:return
            self.owner.countdown.hide()
            from src.core.screen_manager import WindowsBackend
            try:
                check=getattr(self.owner,'preparation_window_check',None)
                window=check() if check else WindowsBackend().info()
                if not self.owner.hotkeys.ready:raise ScreenError('hotkey_registration_failed')
                if not window.foreground:
                    if time.monotonic()>=self.focus_deadline:raise ScreenError('focus_wait_timeout')
                    return
                self.counting=False;self.scan_window()
            except ScreenError as exc:self.failed(exc.code)
            return
        if self.waiting and time.monotonic()>=self.deadline:self.failed('session_emergency_timeout')

    def failed(self,reason):
        if self.closed:return
        self.waiting=False;self.receipt=None;self.timer.stop();self.owner.emergency_tested_signature=None
        self.status.setText(self.owner.locale.text(reason));self.scan.setEnabled(True)
        if self.auto_start:
            self.owner.status_key='paused';self.owner.error_message=self.owner.locale.text(reason);self.owner.refresh_status()
            self.cleanup();self.owner.countdown.hide();self.owner.set_busy(False);self.deleteLater()

    def finished_scan(self):
        worker=self.worker;self.worker=None
        if worker:worker.deleteLater()
        if self.auto_start and self.succeeded and not self.closed:
            self.release_deadline=time.monotonic()+15;self.timer.start();self.tick();return
        if not self.closed and self.receipt:
            self.scan_window();return
        self.scan.setEnabled(not self.waiting and self.owner.emergency_tested_signature is None)

    def cleanup(self):
        if self.closed:return
        self.closed=True;self.waiting=False;self.receipt=None
        self.timer.stop()
        if self.worker:self.worker.requestInterruption();self.worker.wait()
        self.owner.session_check=None
        self.owner.cancel_method_recovery()

    def done(self,result):
        self.cleanup();super().done(result)

    def closeEvent(self,event):
        self.cleanup()
        super().closeEvent(event)
