"""Opt-in Windows desktop input test. Every mouse down is confined to this HWND.

Run: python -m src.gui.input_stand --auto
No game discovery, game focus, or game input is used here.
"""
import argparse
import ctypes
from ctypes import wintypes as W
import json
from pathlib import Path
import threading
import time

from PySide6.QtCore import QTimer, Signal, Qt
from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QLabel, QVBoxLayout, QHBoxLayout
from src.core.input_control import Mouse, GlobalHotkeys
from src.core.screen_manager import WindowsBackend, ScreenError

from src.core.runtime_paths import project_root
ROOT=project_root()


class Target(QPushButton):
    def __init__(self,stand,index):
        super().__init__(f'Target {index+1}'); self.stand=stand; self.index=index
        self.setMinimumSize(150,100)

    def mousePressEvent(self,event):
        self.stand.record('qt_mouse_down',target=self.index,button=str(event.button()))
        super().mousePressEvent(event)

    def mouseReleaseEvent(self,event):
        self.stand.record('qt_mouse_up',target=self.index,button=str(event.button()))
        super().mouseReleaseEvent(event)


class InputStand(QWidget):
    completed=Signal(object)
    def __init__(self,output,auto=False,register_hotkeys=True):
        super().__init__(); self.output=output; self.events=[]; self.stop=threading.Event()
        self.backend=WindowsBackend(); self.mouse=Mouse(self.record); self.thread=None; self.scenario='idle'
        self.result=None; self.auto_close=auto
        self.setWindowTitle('AutoBloodWeb — Native Input Stand'); self.setWindowFlag(Qt.WindowStaysOnTopHint)
        self.setStyleSheet('QWidget {font-family: "Segoe UI"; font-size: 13px;}')
        self.resize(570,360); self.move(80,80)
        layout=QVBoxLayout(self)
        self.label=QLabel('Native Windows input test — own targets only'); self.label.setWordWrap(True); layout.addWidget(self.label)
        row=QHBoxLayout(); self.targets=[Target(self,i) for i in range(3)]
        for target in self.targets: row.addWidget(target)
        layout.addLayout(row)
        self.start=QPushButton('Run native click / hold / F9 interruption'); layout.addWidget(self.start)
        self.start.clicked.connect(self.run_tests)
        self.hotkeys=GlobalHotkeys(self.hotkey) if register_hotkeys else None
        self.completed.connect(self.finish)
        self.guard=QTimer(self); self.guard.setSingleShot(True); self.guard.timeout.connect(self.timeout)
        if auto: QTimer.singleShot(1000,self.run_tests)

    def record(self,stage,**details):
        point=W.POINT(); self.backend.u.GetPhysicalCursorPos(ctypes.byref(point))
        self.events.append(dict(stage=stage,time=time.monotonic(),scenario=self.scenario,
                                cursor=[point.x,point.y],**details))

    def hotkey(self,action):
        self.record('wm_hotkey',action=action,foreground=self.backend.foreground_handle())
        if action=='stop': self.stop.set(); self.mouse.release()

    def point(self,target):
        # ClientToScreen and GetClientRect in a physical DPI context; Qt geometry is logical.
        with self.backend.pixels():
            rect=W.RECT(); origin=W.POINT(); hwnd=int(self.winId())
            if not self.backend.u.GetClientRect(hwnd,ctypes.byref(rect)) or not self.backend.u.ClientToScreen(hwnd,ctypes.byref(origin)):
                raise ScreenError('client_area_failed')
            p=target.mapTo(self,target.rect().center())
            return (round(origin.x+p.x()*rect.right/self.width()),round(origin.y+p.y()*rect.bottom/self.height()))

    def check(self,point):
        hwnd=self.hwnd
        if self.backend.foreground_handle()!=hwnd:
            raise ScreenError('stand_focus_lost',f'foreground={self.backend.foreground_handle()}, expected={hwnd}')
        if self.backend.u.GetAncestor(self.backend.u.WindowFromPoint(W.POINT(*point)),2)!=hwnd:
            raise ScreenError('stand_target_occluded')

    def key(self,vk):
        # Actual SendInput keyboard events, not PostThreadMessage or a callback invocation.
        class Keyboard(ctypes.Structure):
            _fields_=[('vk',W.WORD),('scan',W.WORD),('flags',W.DWORD),('time',W.DWORD),('extra',ctypes.c_size_t)]
        self.check(self.key_check_point)
        events=(self.mouse.Input*2)()
        for index,flag in enumerate((0,2)):
            events[index].type=1; keyboard=Keyboard(vk,0,flag,0,0)
            ctypes.memmove(ctypes.addressof(events[index].payload),ctypes.byref(keyboard),ctypes.sizeof(keyboard))
        ctypes.set_last_error(0)
        count=self.mouse.u.SendInput(2,events,ctypes.sizeof(self.mouse.Input))
        self.record('keyboard_sendinput',vk=vk,result=count,win32_error=ctypes.get_last_error())
        if count!=2: raise ScreenError('stand_keyboard_failed')

    def run_tests(self):
        if self.thread: return
        if not self.hotkeys.ready:
            self.finish(dict(passed=False,error=self.hotkeys.error_detail)); return
        self.start.setEnabled(False); self.show(); self.activateWindow(); self.raise_()
        self.hwnd=int(self.winId())
        self.backend.u.SetForegroundWindow.argtypes=[W.HWND]
        activated=self.backend.u.SetForegroundWindow(int(self.winId()))
        self.record('stand_activation',result=activated,expected=int(self.winId()),foreground=self.backend.foreground_handle())
        points=[self.point(t) for t in self.targets]
        self.key_check_point=points[2]
        self.label.setText('Physical targets: '+str(points)+'\nF9 stops and releases the mouse immediately.')
        self.guard.start(15000)
        def work():
            try:
                if self.backend.foreground_handle()!=self.hwnd:
                    # A normal activation click on our own target; never click another HWND.
                    self.scenario='activation_click'
                    def own_target():
                        if self.backend.u.GetAncestor(self.backend.u.WindowFromPoint(W.POINT(*points[0])),2)!=self.hwnd:
                            raise ScreenError('stand_target_occluded')
                    self.mouse.purchase(points[0],.05,self.stop,own_target); time.sleep(.15)
                self.scenario='toggle'; self.key(0x77); time.sleep(.15)
                for index,duration in enumerate((.05,.65,5.0)):
                    self.scenario=('click','hold','emergency')[index]; self.stop.clear()
                    before=time.monotonic()
                    timer=None
                    if index==2:
                        timer=threading.Timer(.35,lambda:self.key(0x78)); timer.start()
                    try: self.mouse.purchase(points[index],duration,self.stop,lambda:self.check(points[index]))
                    finally:
                        if timer: timer.join(1)
                    self.record('scenario_finished',duration=time.monotonic()-before,held=self.mouse.held,
                                os_left_down=bool(self.backend.u.GetAsyncKeyState(1)&0x8000))
                    time.sleep(.15)
                self.completed.emit(dict(passed=True))
            except Exception as exc: self.completed.emit(dict(passed=False,error=str(exc),detail=getattr(exc,'detail','')))
        self.thread=threading.Thread(target=work,daemon=True)
        QTimer.singleShot(350,self.thread.start)

    def finish(self,packet):
        self.guard.stop(); self.stop.set(); self.mouse.release()
        observed={}
        for name in ('click','hold','emergency'):
            entries=[e for e in self.events if e['scenario']==name]
            downs=[e for e in entries if e['stage']=='qt_mouse_down']
            ups=[e for e in entries if e['stage']=='qt_mouse_up']
            finished=next((e for e in entries if e['stage']=='scenario_finished'),{})
            expected=('click','hold','emergency').index(name)
            observed[name]=dict(qt_down=len(downs),qt_up=len(ups),correct_target=bool(downs and ups and
                all(e['target']==expected for e in downs+ups)),duration=finished.get('duration'),
                released=finished.get('held') is False and finished.get('os_left_down') is False)
        hotkeys={e['action'] for e in self.events if e['stage']=='wm_hotkey'}
        packet.update(observed=observed,hotkeys_received=sorted(hotkeys),events=self.events,
                      keyboard_source='native SendInput, not a hardware key press',game_input=False)
        packet['passed']=bool(packet.get('passed') and hotkeys=={'toggle','stop'} and
            all(v['correct_target'] and v['qt_down']==v['qt_up']==1 and v['released'] for v in observed.values()) and
            observed['emergency']['duration']<1.0)
        self.output.parent.mkdir(parents=True,exist_ok=True)
        self.output.write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
        self.label.setText(('PASS' if packet['passed'] else 'FAIL')+' — '+str(self.output))
        self.result=packet
        self.grab().save(str(self.output.with_suffix('.png')))
        print(json.dumps(dict(passed=packet['passed'],error=packet.get('error'),detail=packet.get('detail'),
            observed=observed,hotkeys=sorted(hotkeys),report=str(self.output)),indent=2),flush=True)
        self.start.setEnabled(False)
        if self.auto_close: QTimer.singleShot(700,self.close)

    def timeout(self):
        self.finish(dict(passed=False,error='stand_timeout'))

    def closeEvent(self,event):
        self.stop.set(); self.mouse.release()
        if self.hotkeys: self.hotkeys.close()
        if self.thread: self.thread.join(2)
        super().closeEvent(event)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--auto',action='store_true')
    parser.add_argument('--output',type=Path,default=ROOT/'debug/v4/input_stand.json'); args=parser.parse_args()
    app=QApplication([]); stand=InputStand(args.output,args.auto); stand.show()
    app.exec()
    return 0 if stand.result and stand.result['passed'] else 2


if __name__=='__main__': raise SystemExit(main())
