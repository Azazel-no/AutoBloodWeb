"""Physical user shortcut test. No injected input and no automation commands."""
import json
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QTableWidget,QTableWidgetItem,QApplication
from src.gui.widgets import BloodButton


class HotkeyDiagnosticsDialog(QDialog):
    def __init__(self,owner):
        super().__init__(owner); self.owner=owner; self.samples=[]; self.closed=False
        self.setWindowTitle(owner.locale.text('hotkey_diagnostics')); self.resize(620,500)
        self.setStyleSheet(owner.styleSheet()); layout=QVBoxLayout(self)
        text=QLabel(owner.locale.text('hotkey_diagnostics_steps',shortcut=owner.prefs.settings['hotkeys']['stop']))
        text.setWordWrap(True); layout.addWidget(text)
        self.health=QLabel(); self.health.setWordWrap(True); layout.addWidget(self.health)
        self.table=QTableWidget(0,4); self.table.setHorizontalHeaderLabels([
            owner.locale.text(k) for k in ('hotkey_context','hotkey_key_state','hotkey_wm','hotkey_focus')])
        layout.addWidget(self.table)
        self.copy=BloodButton(owner.locale.text('copy_report')); self.copy.clicked.connect(self.copy_report); layout.addWidget(self.copy)
        from src.core.screen_manager import WindowsBackend,ScreenError
        from src.core.hotkey_diagnostics import window_metadata
        from src.core.hotkey_config import parse_hotkey
        self.stop_vk=parse_hotkey(owner.prefs.settings['hotkeys']['stop'])[1]
        self.input_access={}
        try:
            window=WindowsBackend().info(); owner.hotkey_test_game=window_metadata(window.hwnd)
            self.input_access=WindowsBackend().access_status(window)
        except ScreenError: owner.hotkey_test_game={}
        from src.core.process_access import process_integrity
        self.app_integrity=process_integrity(__import__('os').getpid())
        owner.hotkey_trace.add('diagnostic_input_access',input_access=self.input_access,app_integrity=self.app_integrity,
            changes_to_privileges=False,synthetic_input_used=False)
        owner.hotkey_diagnostic_only=True; owner.hotkeys.observe_keys=True
        owner.native_hotkey_event.connect(self.received)
        self.timer=QTimer(self); self.timer.setInterval(500); self.timer.timeout.connect(self.update_health); self.timer.start()
        self.update_health()

    def received(self,packet):
        if self.closed: return
        self.samples.append(packet)
        contexts={}
        for event in self.samples:
            info=event.get('foreground_info',{}); process=info.get('process',''); title=info.get('title','')
            group='AutoBloodWeb' if info.get('pid')==__import__('os').getpid() else 'Notepad' if process.lower()=='notepad.exe' else 'DBD' if process.lower().startswith('deadbydaylight') else process or title
            if not group: continue
            row=contexts.setdefault(group,dict(state=0,wm=0,hwnd=info.get('hwnd')))
            if event['stage']=='diagnostic_key_state' and event.get('vk')==self.stop_vk: row['state']+=1
            if event['stage']=='A_wm_hotkey' and event.get('shortcut')==self.owner.prefs.settings['hotkeys']['stop']: row['wm']+=1
        self.table.setRowCount(len(contexts))
        for row,(group,values) in enumerate(contexts.items()):
            for col,value in enumerate((group,values['state'],values['wm'],values['hwnd'])):
                self.table.setItem(row,col,QTableWidgetItem(str(value)))
        self.table.resizeColumnsToContents()

    def update_health(self):
        self.health.setText(self.owner.locale.text('hotkey_registration_state',state=str(self.owner.hotkeys.ready))+
            '\n'+getattr(self.owner.hotkeys,'error_detail','')+
            ('\n'+self.owner.locale.text(self.input_access['reason']) if self.input_access and not self.input_access['allowed'] else '')+
            ('\nAutoBloodWeb: '+self.input_access.get('app',{}).get('level','?')+' · DBD: '+self.input_access.get('game',{}).get('level','?') if self.input_access else '')+
            '\n'+str(self.owner.hotkey_trace.path))

    def copy_report(self):
        QApplication.clipboard().setText(json.dumps(dict(test='manual hardware key in three programs',
            synthetic_input_used=False,registered=self.owner.hotkeys.ready,bindings=self.owner.prefs.settings['hotkeys'],
            input_access=self.input_access,
            events=self.samples,session=self.owner.hotkey_trace.entries),ensure_ascii=False,indent=2))

    def closeEvent(self,event):
        self.closed=True; self.timer.stop(); self.owner.hotkeys.observe_keys=False
        self.owner.hotkey_diagnostic_only=False
        try: self.owner.native_hotkey_event.disconnect(self.received)
        except (RuntimeError,TypeError): pass
        super().closeEvent(event)
