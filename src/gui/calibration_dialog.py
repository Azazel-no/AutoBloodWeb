"""Single-purchase workflow: inspect, choose, test emergency stop, then opt in."""
import json,uuid,logging,time
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QSpinBox,QCheckBox,QApplication,QScrollArea,QWidget,QSizePolicy
from src.gui.widgets import BloodButton,Preview
from src.core.geometry import purchasable
from src.core.models import ScreenState
from src.core.path_planner import RoutePlan
from src.core.action_report import ActionReport


class CalibrationDialog(QDialog):
    def __init__(self,owner):
        super().__init__(owner); self.owner=owner; self.result=None; self.target=None
        self.scan_error=''; self.scan_error_detail=''; self.previous_gate=None; self.attempt_finished=False; self.attempt_error=''
        self.emergency_waiting=False; self.emergency_error=''; self.emergency_deadline=0
        self.emergency_receipts=[]; self.coordinates_valid=False; self.emergency_wrong_focus=False
        self.capture_checks={}
        self.rechecked=False
        self.recheck_error=''
        self.input_access=dict(allowed=True,reason='not_checked')
        owner.emergency_tested_signature=None
        report_dir=(owner.root/'debug/actions' if owner.preferences_path==owner.root/'data/preferences.json'
            else owner.preferences_path.parent/'calibration_reports')
        self.gate_report=ActionReport(report_dir/f'calibration_gate_{uuid.uuid4().hex[:8]}.json')
        self.setWindowTitle(owner.locale.text('calibration_wizard'))
        self.setStyleSheet(owner.styleSheet()); self.resize(min(owner.width(),600),min(760,owner.height()-80))
        self.move(owner.x(),owner.y()+40)
        outer=QVBoxLayout(self); scroll=QScrollArea(); scroll.setWidgetResizable(True)
        content=QWidget(); layout=QVBoxLayout(content); scroll.setWidget(content); outer.addWidget(scroll); t=owner.locale.text
        self.mode_notice=QLabel(t('repeat_calibration_notice') if owner.purchase_mode.currentData()=='repeat' else '')
        self.mode_notice.setWordWrap(True);self.mode_notice.setVisible(bool(self.mode_notice.text()));layout.addWidget(self.mode_notice)
        self.scan=BloodButton(t('wizard_scan')); self.scan.clicked.connect(self.check_game); layout.addWidget(self.scan)
        self.conditions=QLabel(t('wizard_conditions')); self.conditions.setWordWrap(True); layout.addWidget(self.conditions)
        layout.addWidget(QLabel(t('wizard_target')))
        self.nodes=QComboBox(); self.nodes.currentIndexChanged.connect(self.choose_node); layout.addWidget(self.nodes)
        self.preview=Preview(); self.preview.setFixedHeight(220)
        self.preview.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed); layout.addWidget(self.preview)
        self.coordinates=QLabel(); layout.addWidget(self.coordinates)
        self.emergency=QLabel(t('wizard_emergency',shortcut=owner.prefs.settings['hotkeys']['stop']))
        self.emergency.setWordWrap(True); layout.addWidget(self.emergency)
        self.test_emergency=BloodButton(t('wizard_test_emergency')); self.test_emergency.clicked.connect(self.start_emergency_test)
        layout.addWidget(self.test_emergency)
        self.key_stages=QLabel(); self.key_stages.setWordWrap(True); layout.addWidget(self.key_stages)
        self.key_diagnostics=BloodButton(t('hotkey_diagnostics')); self.key_diagnostics.clicked.connect(owner.open_hotkey_diagnostics)
        layout.addWidget(self.key_diagnostics)
        self.emergency_timer=QTimer(self); self.emergency_timer.setInterval(250); self.emergency_timer.timeout.connect(self.emergency_tick)
        self.ack=QCheckBox(t('wizard_ack')); self.ack.setChecked(False)
        self.ack.toggled.connect(lambda checked:owner.dry_run.setChecked(not checked)); layout.addWidget(self.ack)
        row=QHBoxLayout(); row.addWidget(QLabel(t('wizard_prepare')))
        self.delay=QSpinBox(); self.delay.setRange(5,15); self.delay.setValue(int(owner.prefs.settings.get('startup_delay',8)))
        row.addWidget(self.delay); layout.addLayout(row)
        self.begin=BloodButton(t('wizard_begin')); self.begin.clicked.connect(self.start_purchase); self.begin.setEnabled(False); layout.addWidget(self.begin)
        self.status=QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.confirm=BloodButton(t('confirm_calibration')); self.confirm.clicked.connect(self.confirm_purchase); self.confirm.setEnabled(False); layout.addWidget(self.confirm)
        from src.core.calibration_recheck import previous_attempt
        self.recheck_context=previous_attempt(report_dir)
        self.recheck=BloodButton(t('recheck_purchase')); self.recheck.clicked.connect(self.recheck_purchase)
        self.recheck.setEnabled(self.recheck_context is not None); layout.addWidget(self.recheck)
        feedback=QHBoxLayout()
        for value,key in ((True,'wizard_observed_yes'),(False,'wizard_observed_no')):
            button=BloodButton(t(key)); button.clicked.connect(lambda checked=False,v=value:self.observed_purchase(v)); feedback.addWidget(button)
        layout.addLayout(feedback)
        self.copy=BloodButton(t('copy_report')); self.copy.clicked.connect(self.copy_report); layout.addWidget(self.copy)
        self.ack.toggled.connect(self.refresh_gate)
        owner.dry_run.toggled.connect(self.refresh_gate)
        owner.calibration_wizard=self
        self.refresh_gate()

    def check_game(self):
        if self.owner.worker or self.owner.auto_worker: return
        self.rechecked=False
        self.scan_error=''; self.scan_error_detail=''; self.attempt_finished=False; self.attempt_error=''
        self.capture_checks={}
        self.confirm.setEnabled(False)
        self.result=None; self.nodes.clear(); self.begin.setEnabled(False); self.scan.setEnabled(False)
        self.conditions.setText(self.owner.locale.text('wizard_scanning'))
        self.owner.last_action_report=None
        self.owner.start_analysis(None)
        self.owner.worker.completed.connect(self.checked_result)
        self.owner.worker.failed.connect(self.failed)
        self.owner.worker.failed_detail.connect(self.failed_detail)
        self.owner.worker.capture_checked.connect(self.capture_checked)
        self.owner.worker.finished.connect(lambda:(self.scan.setEnabled(True),self.refresh_gate()))
        self.refresh_gate()

    def failed(self,reason):
        self.result=None; self.target=None; self.scan_error=reason
        self.nodes.clear()
        self.conditions.setText(self.owner.locale.text(reason)); self.status.setText(self.conditions.text()); self.begin.setEnabled(False)
        self.refresh_gate()

    def failed_detail(self,code,detail):
        self.scan_error=code; self.scan_error_detail=detail; self.refresh_gate()

    def capture_checked(self,checks):
        self.capture_checks=checks; self.refresh_gate()

    def checked_result(self,result):
        if not result.live_frame or result.screen_state!=ScreenState.BLOODWEB:
            self.failed('no_calibration_node'); return
        self.result=result; self.nodes.blockSignals(True); self.nodes.clear()
        backend=getattr(getattr(result,'capture_manager',None),'backend',None)
        if hasattr(backend,'access_status'): self.input_access=backend.access_status(result.live_frame.window)
        self.scan_error=''; self.scan_error_detail=''
        eligible=sorted((n for n in result.nodes if purchasable(n,result.nodes,result.edges)),
                        key=lambda n:(n.icon_id is None,-n.confidence,n.id))
        for node in eligible:
            self.nodes.addItem(f'{node.id} · {node.icon_id or self.owner.locale.text("unknown_content")}',node)
        self.nodes.blockSignals(False)
        self.conditions.setText(self.owner.locale.text('wizard_game_ok'))
        self.choose_node()

    def choose_node(self):
        self.target=self.nodes.currentData()
        self.coordinates_valid=False
        if self.target and self.result:
            node=self.target; frame=self.result.live_frame
            try:
                point=frame.to_screen(node.x,node.y); self.coordinates_valid=True
                self.coordinates.setText(f'({node.x}, {node.y}) → {point}')
            except Exception as exc: self.coordinates.setText(str(exc))
            plan=RoutePlan(node.id,[node.id],node.icon_id,'priority')
            path=self.owner.root/'debug/v4/calibration_preview.png'
            self.result.renderer.save_debug(self.result.raw_image,self.result,path,plan)
            self.preview.set_image(path)
        self.refresh_gate()

    def emergency_observed(self,signature):
        self.owner.emergency_tested_signature=signature
        self.emergency_waiting=False; self.emergency_error=''; self.emergency_timer.stop()
        self.emergency.setText(self.owner.locale.text('wizard_emergency_ok')); self.refresh_gate()

    def start_emergency_test(self):
        if not self.result or not self.result.live_frame or not self.owner.hotkeys.ready or self.owner.auto_worker:
            self.refresh_gate(); return
        self.emergency_error=''; self.emergency_wrong_focus=False; self.emergency_waiting=True; self.emergency_deadline=time.monotonic()+30
        self.emergency_timer.start(); self.emergency_tick(); self.refresh_gate()

    def receive_emergency_test(self,packet):
        self.emergency_receipts.append(packet)
        self.gate_report.add('emergency_key_received',**packet)
        focus=packet.get('focus_matches',packet['foreground']==packet['expected_game'])
        primary=packet.get('shortcut',self.owner.prefs.settings['hotkeys']['stop'])==self.owner.prefs.settings['hotkeys']['stop']
        message_valid=packet.get('native_event',{}).get('message_verified',True)
        accepted=self.emergency_waiting and focus and primary and message_valid
        reason='accepted' if accepted else 'not_waiting' if not self.emergency_waiting else 'invalid_message' if not message_valid else 'wrong_focus' if not focus else 'backup_only'
        self.gate_report.add('C_calibration_hotkey',accepted=bool(accepted),reason=reason,**packet)
        self.owner.hotkey_trace.add('C_calibration_hotkey',accepted=bool(accepted),reason=reason,**packet)
        self.key_stages.setText(self.owner.locale.text('hotkey_abc',a='✓',b='✓' if focus else '○',c='✓' if accepted else '○')+
            '\n'+self.owner.locale.text('hotkey_result_'+reason))
        if not self.emergency_waiting: return
        if accepted:
            self.emergency_observed(packet['signature'])
        else:
            self.emergency_wrong_focus=True
            self.emergency.setText(self.owner.locale.text('hotkey_result_backup_only' if not primary else 'wizard_emergency_wrong_focus'))

    def emergency_tick(self):
        if not self.emergency_waiting: return
        remaining=max(0,int(self.emergency_deadline-time.monotonic()+.999))
        if remaining==0:
            self.emergency_waiting=False; self.emergency_error='wizard_emergency_timeout'; self.emergency_timer.stop()
            self.emergency.setText(self.owner.locale.text(self.emergency_error)); self.refresh_gate(); return
        if not self.emergency_wrong_focus:
            self.emergency.setText(self.owner.locale.text('wizard_emergency_waiting',seconds=remaining,
                shortcut=self.owner.prefs.settings['hotkeys']['stop']))

    def observed_purchase(self,value):
        self.gate_report.add('user_purchase_observation',purchased=value,objective_verified=bool(
            self.owner.calibration and self.owner.calibration.get('verified')))
        self.status.setText(self.owner.locale.text('wizard_observation_saved'))

    def refresh_gate(self,*args):
        gate=self.gate_snapshot(); reason=gate['reason']
        if self.owner.calibration and self.owner.calibration.get('verified'):
            self.confirm.setEnabled(gate['emergency_tested'] and gate['hotkeys_registered'] and self.input_access['allowed'])
        self.recheck.setEnabled(self.recheck_context is not None and not gate['analysis_busy'] and not gate['automation_busy'])
        self.begin.setEnabled(reason=='wizard_armed' and not self.rechecked)
        self.test_emergency.setEnabled(gate['live_frame'] and gate['hotkeys_registered'] and not gate['automation_busy'] and not self.emergency_waiting)
        if not self.owner.worker:
            checks=[('wizard_check_window',gate['live_frame'] or self.capture_checks.get('window_found',False)),
                    ('wizard_check_frame',gate['live_frame'] or self.capture_checks.get('frame_received',False)),
                    ('wizard_check_web',gate['live_frame']),('wizard_check_target',bool(self.target)),
                    ('wizard_check_coords',self.coordinates_valid),('wizard_check_hotkey',gate['hotkeys_registered']),
                    ('wizard_check_access',gate['live_frame'] and self.input_access['allowed'])]
            self.conditions.setText('\n'.join(('✓ ' if ok else '○ ')+self.owner.locale.text(key) for key,ok in checks))
        if gate!=self.previous_gate:
            self.gate_report.add('calibration_gate',**gate); self.previous_gate=gate
            logging.getLogger(__name__).info('Calibration gate %s',gate)
        if not self.owner.auto_worker:
            if (self.attempt_finished and not self.result and not self.scan_error and self.owner.calibration
                    and self.owner.calibration.get('verified')):
                reason='calibration_verified'
            text=self.owner.locale.text(reason,shortcut=self.owner.prefs.settings['hotkeys']['stop'])
            if reason=='wizard_attempt_failed': text=self.attempt_error
            if reason==self.scan_error and self.scan_error_detail: text+='\n'+self.scan_error_detail
            if reason=='hotkey_registration_failed': text+='\n'+getattr(self.owner.hotkeys,'error_detail','')
            if len(gate['unmet_conditions'])>1:
                text+='\n'+self.owner.locale.text('wizard_unmet')+'\n'+'\n'.join('• '+self.owner.locale.text(key,
                    shortcut=self.owner.prefs.settings['hotkeys']['stop']) for key in gate['unmet_conditions'] if key!=reason)
            self.status.setText(text)

    def gate_snapshot(self):
        live=bool(self.result and self.result.live_frame)
        emergency=bool(live and self.owner.emergency_tested_signature==self.result.live_frame.window.signature)
        if self.owner.auto_worker: reason='wizard_busy'
        elif self.scan_error: reason=self.scan_error
        elif self.owner.worker: reason='wizard_scanning'
        elif self.attempt_error and not live: reason='wizard_attempt_failed'
        elif not live: reason='wizard_scan_required'
        elif not self.target: reason='no_calibration_node'
        elif not self.coordinates_valid: reason='invalid_coordinates'
        elif not self.input_access['allowed']: reason=self.input_access['reason']
        elif not self.owner.hotkeys.ready: reason='hotkey_registration_failed'
        elif self.rechecked and self.owner.calibration and self.owner.calibration.get('verified'):
            reason='recheck_confirm_ready' if emergency else 'recheck_emergency_required'
        elif not self.ack.isChecked(): reason='wizard_opt_in_required'
        elif self.owner.dry_run.isChecked(): reason='wizard_dry_active'
        elif not emergency: reason='wizard_emergency_waiting_gate' if self.emergency_waiting else self.emergency_error or 'wizard_emergency'
        else: reason='wizard_armed'
        unmet=[]
        if live:
            if not self.target: unmet.append('no_calibration_node')
            if not self.coordinates_valid: unmet.append('invalid_coordinates')
            if not self.input_access['allowed']: unmet.append(self.input_access['reason'])
            if not self.owner.hotkeys.ready: unmet.append('hotkey_registration_failed')
            if not emergency: unmet.append('wizard_emergency')
            if not self.ack.isChecked(): unmet.append('wizard_opt_in_required')
            if self.owner.dry_run.isChecked(): unmet.append('wizard_dry_active')
        return dict(reason=reason,unmet_conditions=unmet,live_frame=live,target=self.target.id if self.target else None,
            opt_in=self.ack.isChecked(),dry_run=self.owner.dry_run.isChecked(),
            hotkeys_registered=self.owner.hotkeys.ready,emergency_tested=emergency,
            coordinates_valid=self.coordinates_valid,emergency_waiting=self.emergency_waiting,
            input_access=self.input_access,
            analysis_busy=bool(self.owner.worker),automation_busy=bool(self.owner.auto_worker),scan_error=self.scan_error,
            attempt_error=self.attempt_error,
            hotkey_error=getattr(self.owner.hotkeys,'error_detail',''))

    def start_purchase(self):
        self.refresh_gate()
        if not self.begin.isEnabled(): return
        self.owner.prefs.settings['startup_delay']=self.delay.value()
        self.owner.calibration_selection=(self.target.x,self.target.y,self.result.live_frame.window.signature,self.result.live_frame.roi)
        self.owner.start_automation(calibrate=True)
        self.begin.setEnabled(False); self.scan.setEnabled(False)

    def recheck_purchase(self):
        if not self.recheck_context or self.owner.worker or self.owner.auto_worker: return
        self.recheck_error=''; self.owner.calibration=None; self.confirm.setEnabled(False)
        from src.gui.automation_worker import PurchaseRecheckWorker
        from src.core.models import NodeType
        expected=self.recheck_context['target']['icon']
        if not expected.startswith(self.owner.current_role+'_'):
            self.status.setText(self.owner.locale.text('recheck_role_changed')); return
        worker=PurchaseRecheckWorker(self.recheck_context,self.owner.library,self.owner.current_role,
            self.owner.character.currentData(),self.owner.purchase_mode.currentData(),self.owner.hold_duration.value(),self.gate_report,self)
        self.owner.worker=worker; self.recheck.setEnabled(False); self.scan.setEnabled(False)
        self.status.setText(self.owner.locale.text('recheck_running'))
        def completed(receipt,frame,result):
            self.rechecked=True
            self.owner.calibration=receipt; self.owner.calibration_signature=frame.window.signature
            self.checked_result(result); self.owner.save_preferences()
            self.coordinates.setText(self.owner.locale.text('recheck_target',icon=self.recheck_context['target']['icon'],point=receipt['point']))
            self.status.setText(self.owner.locale.text('recheck_success'))
        def finished():
            self.owner.worker=None; self.scan.setEnabled(True); self.recheck.setEnabled(True); self.refresh_gate()
            if self.owner.calibration and self.owner.calibration.get('verified'):
                self.status.setText(self.owner.locale.text('recheck_success'))
            elif self.recheck_error: self.status.setText(self.recheck_error)
        def failed(code,detail):
            self.recheck_error=self.owner.locale.text(code)+'\n'+detail
            self.status.setText(self.recheck_error)
        worker.completed.connect(completed)
        worker.failed.connect(failed)
        worker.finished.connect(finished); worker.start()

    def update_status(self,text):
        self.status.setText(text)

    def finished(self):
        # Every subsequent attempt requires a fresh scan and explicit selection.
        self.result=None; self.attempt_finished=True
        self.attempt_error='' if self.owner.calibration and self.owner.calibration.get('verified') else self.owner.error_message
        self.scan.setEnabled(True); self.refresh_gate()
        self.confirm.setEnabled(bool(self.owner.calibration and self.owner.calibration.get('verified')))

    def confirm_purchase(self):
        self.owner.confirm_calibration(); self.confirm.setEnabled(False)
        self.status.setText(self.owner.locale.text('ready'))
        self.close()

    def copy_report(self):
        report=self.owner.auto_worker.engine.report if self.owner.auto_worker else self.owner.last_action_report
        packet=dict(gate=self.gate_snapshot(),scan_error_detail=self.scan_error_detail,
            capture_checks=self.capture_checks,
            emergency_receipts=self.emergency_receipts,
            calibration=self.owner.calibration,
            wizard_entries=self.gate_report.entries,hotkey_entries=self.owner.hotkey_trace.entries,
            hotkey_thread_alive=bool(getattr(self.owner.hotkeys,'thread',None) and self.owner.hotkeys.thread.is_alive()),
            hotkey_last_pump=getattr(self.owner.hotkeys,'last_pump',None),entries=report.entries if report else [])
        QApplication.clipboard().setText(json.dumps(packet,ensure_ascii=False,indent=2,default=str))

    def closeEvent(self,event):
        self.emergency_timer.stop()
        if self.owner.worker and hasattr(self.owner.worker,'stop'): self.owner.worker.requestInterruption()
        try: self.owner.dry_run.toggled.disconnect(self.refresh_gate)
        except (RuntimeError,TypeError): pass
        if self.owner.auto_worker and self.owner.auto_worker.calibrate: self.owner.auto_worker.engine.halt()
        self.owner.calibration_wizard=None
        super().closeEvent(event)
