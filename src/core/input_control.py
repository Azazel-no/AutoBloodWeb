"""Windows input and registered global shortcuts, with unconditional release."""
import ctypes
from ctypes import wintypes as W
import threading
import time
from datetime import datetime,timezone
from src.core.screen_manager import ScreenError
from src.core.hotkey_config import DEFAULT_BINDINGS,validate_bindings,parse_hotkey


class Mouse:
    operation_lock=threading.Lock()
    def __init__(self,audit=None):
        self.audit=audit or (lambda *args,**kwargs:None)
        self.u=ctypes.WinDLL('user32',use_last_error=True)
        class MouseInput(ctypes.Structure):
            _fields_=[('dx',W.LONG),('dy',W.LONG),('data',W.DWORD),('flags',W.DWORD),('time',W.DWORD),('extra',ctypes.c_size_t)]
        class Payload(ctypes.Union):
            _fields_=[('mouse',MouseInput),('padding',ctypes.c_byte*(32 if ctypes.sizeof(ctypes.c_void_p)==8 else 24))]
        class Input(ctypes.Structure):
            _fields_=[('type',W.DWORD),('payload',Payload)]
        self.Input=Input
        self.u.SendInput.argtypes=[W.UINT,ctypes.POINTER(Input),ctypes.c_int]
        self.u.SetPhysicalCursorPos.argtypes=[ctypes.c_int,ctypes.c_int]
        self.u.SetPhysicalCursorPos.restype=W.BOOL
        self.u.GetPhysicalCursorPos.argtypes=[ctypes.POINTER(W.POINT)]
        self.u.GetPhysicalCursorPos.restype=W.BOOL
        self.lock=threading.Lock(); self.held=False;self.down_attempt_sequence=0
        self.lifecycle=None
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateMutexW.argtypes=[ctypes.c_void_p,W.BOOL,W.LPCWSTR];self.kernel.CreateMutexW.restype=W.HANDLE
        self.kernel.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD];self.kernel.WaitForSingleObject.restype=W.DWORD
        self.kernel.ReleaseMutex.argtypes=[W.HANDLE]
        self.owner_mutex=self.kernel.CreateMutexW(None,False,'Local\\AutoBloodWeb.PhysicalMouse')

    def lifecycle_event(self,stage,**details):
        if self.lifecycle:self.lifecycle.input_event(stage,operation=getattr(self,'active_input',None),**details)

    def own_input(self):
        if not self.operation_lock.acquire(blocking=False):raise ScreenError('mouse_operation_busy')
        if not self.owner_mutex or self.kernel.WaitForSingleObject(self.owner_mutex,0) not in (0,0x80):
            self.operation_lock.release();raise ScreenError('mouse_operation_busy')

    def unown_input(self):
        self.kernel.ReleaseMutex(self.owner_mutex);self.operation_lock.release()

    def note(self,stage,**details):
        try: self.audit(stage,**details)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Mouse audit failed; input state is still tracked')

    def environment(self):
        """Read-only desktop diagnostics; never change desktop or privileges."""
        try:
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            self.u.GetThreadDesktop.argtypes=[W.DWORD]; self.u.GetThreadDesktop.restype=W.HANDLE
            self.u.OpenInputDesktop.argtypes=[W.DWORD,W.BOOL,W.DWORD]; self.u.OpenInputDesktop.restype=W.HANDLE
            self.u.CloseDesktop.argtypes=[W.HANDLE]
            self.u.GetUserObjectInformationW.argtypes=[W.HANDLE,ctypes.c_int,ctypes.c_void_p,W.DWORD,ctypes.POINTER(W.DWORD)]
            def name(handle):
                value=ctypes.create_unicode_buffer(256); needed=W.DWORD()
                ok=self.u.GetUserObjectInformationW(handle,2,value,ctypes.sizeof(value),ctypes.byref(needed))
                return value.value if ok else None
            current=self.u.GetThreadDesktop(kernel.GetCurrentThreadId())
            ctypes.set_last_error(0); input_desktop=self.u.OpenInputDesktop(0,False,1); error=ctypes.get_last_error()
            try: return dict(thread_desktop=name(current),input_desktop=name(input_desktop) if input_desktop else None,
                input_desktop_access=bool(input_desktop),win32_error=error)
            finally:
                if input_desktop: self.u.CloseDesktop(input_desktop)
        except Exception as exc: return dict(diagnostic_error=str(exc))

    def send(self,flag):
        event=self.Input(); event.payload.mouse.flags=flag
        ctypes.set_last_error(0)
        started=time.perf_counter()
        if flag==2:self.down_attempt_sequence+=1
        result=self.u.SendInput(1,ctypes.byref(event),ctypes.sizeof(event))
        seconds=time.perf_counter()-started
        error=ctypes.get_last_error()
        if result==1:
            self.held=(flag==2)
            if flag==2:self.last_down_at=time.monotonic()
        if result==1:self.lifecycle_event('MOUSE_DOWN' if flag==2 else 'MOUSE_UP',api_result=result,api_started_at=started,api_seconds=seconds)
        else:self.lifecycle_event('INPUT_API_FAILED',flag=flag,api_result=result,win32_error=error,api_started_at=started)
        self.note('SendInput',flag=flag,result=result,win32_error=error,size=ctypes.sizeof(event),seconds=seconds)
        if result!=1:
            raise ScreenError('sendinput_failed',f'SendInput flag={flag}, result={result}, Win32={error}')

    def cursor_call(self,name,*args):
        started=time.perf_counter()
        result=getattr(self.u,name)(*args)
        error=ctypes.get_last_error()
        self.note('cursor_api',api=name,result=result,win32_error=error,seconds=time.perf_counter()-started)
        ctypes.set_last_error(error)
        return result

    def release(self):
        with self.lock:
            if self.held:
                self.send(4); self.held=False

    def position(self):
        point=W.POINT()
        if not self.cursor_call('GetPhysicalCursorPos',ctypes.byref(point)):raise ScreenError('cursor_read_failed')
        return (point.x,point.y)

    def purchase(self,point,duration,stop,check):
        from contextlib import nullcontext
        journal=self.lifecycle
        parent=journal.current.get() if journal else None
        target=dict(parent.get('target') or {}) if parent else {}
        target['point']=point
        intent='MODAL_DISMISS' if parent and parent['intent']=='MODAL_DISMISS' else 'ACTUAL_PURCHASE'
        context=journal.operation(intent,target,kind='physical_input') if journal else nullcontext()
        with context:
            self.own_input()
            self.active_input=journal.current.get() if journal else None
            down_before=self.down_attempt_sequence
            cancelled=False
            try:
                self.note('input_commit_begin')
                self.lifecycle_event('PREPARED',point=point,duration=duration)
                if journal and journal.error:raise ScreenError('click_journal_failed',journal.error)
                self._purchase(point,duration,stop,check)
            except BaseException as exc:
                cancelled=True
                self.lifecycle_event('input_cancelled',cancel_reason=getattr(exc,'code',type(exc).__name__),
                    cancelled_stage='COMMIT_INPUT' if self.down_attempt_sequence!=down_before else 'AIM',
                    next_recovery_action='OBSERVE_WITHOUT_RETRY' if self.down_attempt_sequence!=down_before else 'TARGET_REVALIDATION')
                raise
            finally:
                try:
                    self.release()
                    if self.down_attempt_sequence==down_before and not cancelled:
                        self.lifecycle_event('input_cancelled',cancel_reason='STOP' if stop.is_set() else 'NO_DOWN',
                            cancelled_stage='AIM',next_recovery_action='STOP' if stop.is_set() else 'TARGET_REVALIDATION')
                finally:
                    self.active_input=None;self.unown_input()
                    self.note('input_commit_end')

    def _purchase(self,point,duration,stop,check):
        check()
        if stop.is_set(): return
        with self.lock:
            self.lifecycle_event('AIM',point=point)
            ctypes.set_last_error(0)
            moved=self.cursor_call('SetPhysicalCursorPos',*point); error=ctypes.get_last_error()
            self.note('SetPhysicalCursorPos',requested=point,result=moved,win32_error=error)
            movement_api='SetPhysicalCursorPos'
            if not moved and error==0 and hasattr(self.u,'SetThreadDpiAwarenessContext'):
                # Ordinary SetCursorPos in a physical pixel context. No desktop,
                # process privilege or input restriction is changed. Keep the
                # original failure and the fallback result in the same report.
                check()
                if stop.is_set(): return
                self.u.SetThreadDpiAwarenessContext.argtypes=[ctypes.c_void_p]
                self.u.SetThreadDpiAwarenessContext.restype=ctypes.c_void_p
                self.u.SetCursorPos.argtypes=[ctypes.c_int,ctypes.c_int]
                self.u.SetCursorPos.restype=W.BOOL
                previous=self.u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
                if previous:
                    try:
                        ctypes.set_last_error(0)
                        moved=self.cursor_call('SetCursorPos',*point); error=ctypes.get_last_error()
                        movement_api='SetCursorPos_physical_context'
                        self.note('SetCursorPos_physical_context',requested=point,result=moved,win32_error=error)
                    finally: self.u.SetThreadDpiAwarenessContext(previous)
            if not moved:
                desktop=self.environment(); self.note('input_desktop_diagnostic',**desktop)
                raise ScreenError('cursor_move_failed',f'{movement_api} result={moved}, Win32={error}; desktop={desktop}')
            actual=W.POINT()
            ctypes.set_last_error(0)
            read=self.cursor_call('GetPhysicalCursorPos',ctypes.byref(actual)); error=ctypes.get_last_error()
            self.note('GetPhysicalCursorPos',actual=(actual.x,actual.y),requested=point,result=read,win32_error=error)
            if not read: raise ScreenError('cursor_read_failed',f'GetPhysicalCursorPos Win32={error}')
            if (actual.x,actual.y)!=point:
                raise ScreenError('cursor_position_mismatch',f'requested={point}, actual={(actual.x,actual.y)}')
            self.lifecycle_event('CURSOR_ARRIVED',point=point,actual=(actual.x,actual.y))
            region=getattr(self,'target_region',None)
            if region:
                import math
                if math.dist((actual.x,actual.y),region[0])>=region[1]:raise ScreenError('cursor_outside_node')
                self.note('cursor_inside_node',actual=(actual.x,actual.y),center=region[0],inner_radius=region[1])
            # Let the game observe the new pointer position before pressing.
            # Stop interrupts this wait; the position and live guard below are
            # checked again afterwards. Hover-only park() never gains a click.
            settled=time.perf_counter()
            settle=getattr(self,'settle_seconds',.1)
            if settle not in (0,.1):raise ValueError('invalid_cursor_settle')
            if stop.wait(settle):return
            self.note('cursor_settle',requested_seconds=settle,seconds=time.perf_counter()-settled,point=point)
            check()
            if stop.is_set(): return
            # Re-read immediately after the potentially expensive ownership
            # guard, so movement during that guard cannot lead to a down event.
            actual=W.POINT()
            read=self.cursor_call('GetPhysicalCursorPos',ctypes.byref(actual))
            self.note('cursor_before_down',actual=(actual.x,actual.y),requested=point,result=read)
            if not read:raise ScreenError('cursor_read_failed')
            if (actual.x,actual.y)!=point:raise ScreenError('cursor_position_mismatch')
            if region and math.dist((actual.x,actual.y),region[0])>=region[1]:raise ScreenError('cursor_outside_node')
            if self.lifecycle and self.lifecycle.error:raise ScreenError('click_journal_failed',self.lifecycle.error)
            self.lifecycle_event('INPUT_READY',point=point,actual=(actual.x,actual.y))
            if self.lifecycle and self.lifecycle.error:raise ScreenError('click_journal_failed',self.lifecycle.error)
            self.send(2); self.held=True
        try:
            deadline=time.monotonic()+duration
            while time.monotonic()<deadline:
                if stop.wait(min(.02,max(0,deadline-time.monotonic()))): break
                check()
        finally: self.release()

    def park(self,point,stop,check):
        from contextlib import nullcontext
        journal=self.lifecycle;op=journal.current.get() if journal else None
        targets=(op.get('target') or {}) if op else {}
        points=targets.get('points',[targets.get('point')])
        probing=bool(op and op['intent'] in ('PRICE_PROBE','CHAIN_PREVIEW_PROBE') and list(point) in [list(p) for p in points if p])
        context=journal.operation('CURSOR_PARKING',dict(point=point)) if journal and not probing else nullcontext()
        with context:
            self.own_input()
            try:
                self.lifecycle_event('MOVE',point=point)
                value=self._park(point,stop,check)
                if journal and not probing:journal.current.get()['result']='Parked'
                return value
            finally:self.unown_input()

    def _park(self,point,stop,check):
        """Move off the web after release, never send another button event."""
        check()
        if stop.is_set(): raise InterruptedError
        with self.lock:
            if self.held: raise ScreenError('mouse_still_held')
            check()
            if stop.is_set(): raise InterruptedError
            ctypes.set_last_error(0)
            result=self.cursor_call('SetPhysicalCursorPos',*point); error=ctypes.get_last_error()
            self.note('cursor_park',requested=point,result=result,win32_error=error,clicks=0)
            if not result: raise ScreenError('cursor_move_failed',f'park Win32={error}')
            actual=W.POINT()
            read=self.cursor_call('GetPhysicalCursorPos',ctypes.byref(actual))
            self.note('cursor_after_park',actual=(actual.x,actual.y),requested=point,result=read)
            if read and (actual.x,actual.y)!=point:
                # The game can restore the pointer just after button release.
                # Retry this hover-only move once, with a fresh ownership guard.
                # A failed park never authorizes another button event.
                if stop.wait(.05):raise InterruptedError
                check()
                if stop.is_set():raise InterruptedError
                result=self.cursor_call('SetPhysicalCursorPos',*point)
                self.note('cursor_park_retry',requested=point,result=result,clicks=0)
                if not result:raise ScreenError('cursor_move_failed')
                if stop.wait(.02):raise InterruptedError
                check()
                read=self.cursor_call('GetPhysicalCursorPos',ctypes.byref(actual))
                self.note('cursor_after_park_retry',actual=(actual.x,actual.y),requested=point,result=read)
            if not read or (actual.x,actual.y)!=point:
                raise ScreenError('cursor_position_mismatch',f'requested={point}, actual={(actual.x,actual.y)}, read={read}')


class GlobalHotkeys:
    """Configurable toggle/stop shortcuts, plus reserved Ctrl+Alt+F9 stop."""
    def __init__(self,callback,bindings=None):
        self.callback=callback; self.ready=False; self.error=''; self.done=threading.Event()
        self.bindings=dict(bindings or DEFAULT_BINDINGS)
        self.keys=validate_bindings(self.bindings)
        self.error_detail=''
        self.events=[]; self.diagnostic=None; self.current_event=None; self.last_pump=0
        self.observe_keys=False
        self.initialized=threading.Event()
        self.registered_shortcuts=set()
        self.thread=threading.Thread(target=self.run,name='GlobalHotkeys',daemon=True)
        self.thread.start(); self.initialized.wait(2)

    def record(self,stage,**details):
        packet=dict(stage=stage,time=datetime.now(timezone.utc).isoformat(),monotonic=time.monotonic(),
            thread_id=threading.get_native_id(),**details)
        self.events.append(packet); self.events=self.events[-200:]
        if self.diagnostic:
            try: self.diagnostic(packet)
            except Exception:
                import logging
                logging.getLogger(__name__).exception('Hotkey diagnostics failed')
        return packet

    def stop_pressed(self):
        """Read key release only; this cannot establish a physical F9 proof."""
        u=ctypes.WinDLL('user32',use_last_error=True)
        u.GetAsyncKeyState.argtypes=[ctypes.c_int];u.GetAsyncKeyState.restype=W.SHORT
        return bool(u.GetAsyncKeyState(self.keys['stop'][1])&0x8000)

    def native_health(self):
        """Registration/receiver liveness only; never claims a physical press."""
        healthy=bool(self.ready and self.initialized.is_set() and self.thread.is_alive()
                     and not self.done.is_set() and time.monotonic()-self.last_pump<1
                     and self.bindings['stop'] in self.registered_shortcuts)
        return dict(ready=healthy,stop_registered=self.bindings['stop'] in self.registered_shortcuts,
                    heartbeat_age=time.monotonic()-self.last_pump,shortcut=self.bindings['stop'],
                    physical_key_verified=False,source='RegisterHotKey and live native receiver')

    def run(self):
        u=ctypes.WinDLL('user32',use_last_error=True)
        u.GetForegroundWindow.restype=W.HWND
        u.RegisterHotKey.argtypes=[W.HWND,ctypes.c_int,W.UINT,W.UINT]
        u.UnregisterHotKey.argtypes=[W.HWND,ctypes.c_int]
        u.PeekMessageW.argtypes=[ctypes.POINTER(W.MSG),W.HWND,W.UINT,W.UINT,W.UINT]
        u.GetAsyncKeyState.argtypes=[ctypes.c_int]; u.GetAsyncKeyState.restype=W.SHORT
        registered=[]
        try:
            specs=[(1,'stop',self.keys['stop']),(2,'toggle',self.keys['toggle'])]
            backup=parse_hotkey('Ctrl+Alt+F9')
            if backup!=self.keys['stop']: specs.append((3,'stop',backup))
            actions={ident:action for ident,action,_ in specs}
            shortcuts={1:self.bindings['stop'],2:self.bindings['toggle'],3:'Ctrl+Alt+F9'}
            spec_by_id={ident:key for ident,action,key in specs}
            msg=W.MSG(); u.PeekMessageW(ctypes.byref(msg),None,0,0,0)  # Create this thread's queue.
            for ident,action,(modifiers,key) in specs:
                ctypes.set_last_error(0); ok=u.RegisterHotKey(None,ident,modifiers|0x4000,key)
                error=ctypes.get_last_error()
                self.record('hotkey_registration',registered=bool(ok),registered_id=ident,vk=key,
                    modifiers=modifiers,shortcut=shortcuts[ident],win32_error=error,receiver_hwnd=0)
                if not ok:
                    if ident==3: continue  # Optional reserve must not disable the two main shortcuts.
                    self.error='hotkey_registration_failed'
                    self.error_detail=f'{action}: {shortcuts[ident]}, VK={key}, modifiers={modifiers}, Win32={error}'
                    return
                registered.append(ident)
                self.registered_shortcuts.add(shortcuts[ident])
            self.ready=True; self.initialized.set()
            down={}; last_foreground=None
            while not self.done.is_set():
                self.last_pump=time.monotonic()
                while u.PeekMessageW(ctypes.byref(msg),None,0,0,1):
                    if msg.message==0x312 and msg.wParam in registered:
                        ident=int(msg.wParam); modifiers,key=spec_by_id[ident]
                        hwnd=u.GetForegroundWindow()
                        # Focus is sampled at native receipt, before any queued Qt event.
                        from src.core.hotkey_diagnostics import window_metadata
                        packet=self.record('A_wm_hotkey',registered_id=ident,vk=key,modifiers=modifiers,
                            shortcut=shortcuts[ident],message_vk=(msg.lParam>>16)&0xffff,
                            message_modifiers=msg.lParam&0xffff,foreground_info=window_metadata(hwnd),
                            message_verified=((msg.lParam>>16)&0xffff)==key and (msg.lParam&0xffff)==modifiers,
                            source='WM_HOTKEY; physical/injected origin cannot be inferred from this message')
                        self.current_event=packet
                        if actions[ident]=='stop' or packet['message_verified']: self.callback(actions[ident])
                if self.observe_keys:
                    from src.core.hotkey_diagnostics import window_metadata
                    hwnd=u.GetForegroundWindow()
                    if hwnd!=last_foreground:
                        self.record('diagnostic_focus',foreground_info=window_metadata(hwnd)); last_foreground=hwnd
                    # Diagnostic only: no callback, no buying, no safety proof.
                    for key in {vk for modifiers,vk in self.keys.values()}:
                        pressed=bool(u.GetAsyncKeyState(key)&0x8000)
                        if pressed and not down.get(key):
                            self.record('diagnostic_key_state',vk=key,foreground_info=window_metadata(hwnd),
                                source='GetAsyncKeyState high bit; observation only, not WM_HOTKEY proof')
                        down[key]=pressed
                else: down.clear(); last_foreground=None
                self.done.wait(.02)
        except Exception as exc:
            self.error=str(exc); self.error_detail=str(exc); self.ready=False
            try: self.callback('unavailable')
            finally: self.record('hotkey_thread_error',error=str(exc))
        finally:
            self.initialized.set(); self.ready=False
            self.registered_shortcuts.clear()
            for ident in registered: u.UnregisterHotKey(None,ident)
            self.record('hotkey_thread_closed')

    def close(self):
        self.done.set(); self.thread.join(2)
