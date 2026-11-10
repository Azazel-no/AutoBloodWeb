"""Bounded native GUI test. Requires a new explicit chat authorization file.

At most20 Mouse calls /300 seconds, persisted across workers and restarts.
No keyboard injection, focus activation, calibration purchase or prestige.
The central game function may unlock several nodes with one input.
"""
import sys,json,time,uuid,hashlib,logging,os
from pathlib import Path
from dataclasses import asdict
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.live_test_v47 import InputBudget
from src.core.screen_manager import ScreenError

def read_authorization(path):
    if not path.is_file():raise ValueError('new_v48_authorization_required')
    data=json.loads(path.read_text(encoding='utf-8'))
    if data.get('version')=='v54':
        from scripts.live_test_v54 import validate_authorization
        return validate_authorization(data)
    if data.get('version')=='v53':
        from scripts.live_test_v53 import validate_authorization
        return validate_authorization(data)
    if (data.get('approved') is not True or type(data.get('authorized_limit')) is not int or
            not 1<=data['authorized_limit']<=20 or type(data.get('max_seconds')) is not int or
            not 1<=data['max_seconds']<=300 or data.get('allow_prestige') is not False or
            type(data.get('allow_central_unlock')) is not bool or data.get('role') not in ('survivor','killer') or
            data['role']=='killer' and not isinstance(data.get('character'),str)):
        raise ValueError('invalid_v48_authorization')
    return data

def acquire_scope(path):
    import msvcrt
    handle=path.with_suffix('.lock').open('a+b');handle.seek(0,2)
    if handle.tell()==0:handle.write(b'0');handle.flush()
    handle.seek(0)
    try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:handle.close();raise ValueError('permission_scope_already_running')
    return handle

def local_point(frame,point):
    h,w=frame.image.shape[:2]
    return ((point[0]-frame.window.client.x-frame.roi.x)*w/frame.roi.width,
            (point[1]-frame.window.client.y-frame.roi.y)*h/frame.roi.height)

def check_opening_scope(authorization,engine):
    if authorization.get('require_one_full') and engine.opening.full_scans!=1:
        raise ScreenError('one_full_required','trial stops before another input after full fallback')
    if not authorization.get('require_fast_opening'):return
    if not engine.opening.active:
        raise ScreenError('fast_opening_required','test stops before input after opening fallback')

def opening_scoped_buy(authorization,engine,buy):
    def checked(*args,**kwargs):
        check_opening_scope(authorization,engine)
        return buy(*args,**kwargs)
    return checked

def main(path):
    authorization=read_authorization(path)
    lock=acquire_scope(path)
    try:return run_authorized(path,authorization)
    finally:lock.close()

def run_authorized(path,authorization):
    from PySide6.QtCore import QTimer,Qt
    from PySide6.QtWidgets import QApplication,QLabel
    from src.core.icon_library import IconLibrary
    from src.utils.images import write_image
    import src.gui.main_window as gui
    state_path=path.with_suffix('.state.json');budget_path=path.with_suffix('.budget.json')
    if authorization.get('version') in ('v49','v52','v53') and authorization.get('budget_file'):
        budget_path=(ROOT/authorization['budget_file']).resolve()
        if not budget_path.is_relative_to(ROOT/('debug/'+authorization['version'])) or not budget_path.name.endswith('.budget.json'):raise ValueError('invalid_shared_budget_path')
    if state_path.is_file():state=json.loads(state_path.read_text(encoding='utf-8'))
    else:
        state=dict(started_monotonic=None if authorization.get('version')=='v54' else time.monotonic(),authorization_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        state_path.write_text(json.dumps(state,indent=2),encoding='utf-8')
    if state['authorization_sha256']!=hashlib.sha256(path.read_bytes()).hexdigest():raise ValueError('authorization_changed')
    deadline=state['started_monotonic']+authorization['max_seconds'] if state['started_monotonic'] is not None else float('inf')
    if state['started_monotonic'] is not None and (time.monotonic()<state['started_monotonic'] or time.monotonic()>=deadline):raise ScreenError('runtime_limit')
    budget=InputBudget(authorization['authorized_limit'],budget_path)
    if budget.used>=budget.limit:raise ScreenError('action_limit')
    version=authorization.get('version','v48')
    if version not in ('v48','v49','v52','v53','v54'):raise ValueError('invalid_test_version')
    folder=ROOT/('debug/'+version)/('live_session_'+uuid.uuid4().hex[:8]);folder.mkdir();(folder/'frames').mkdir()
    source=ROOT/'data/preferences.json';original=source.read_bytes();(folder/'preferences_before.json').write_bytes(original)
    data=json.loads(original);data['role']=authorization['role']
    seed_path=path.with_suffix('.scene_seed.json')
    if version in ('v53','v54') and path.with_suffix('.profile_seed.json').is_file():
        data=json.loads(path.with_suffix('.profile_seed.json').read_text(encoding='utf-8'))
        data['role']=authorization['role']
    if version=='v49' and seed_path.is_file():
        seed=json.loads(seed_path.read_text(encoding='utf-8'))
        key=authorization['role']+'/'+(authorization.get('character','') if authorization['role']=='killer' else '')
        if seed['key']!=key or seed['scene'].get('pending') is not None or not seed.get('two_current_red_frames'):
            raise ValueError('invalid_scene_seed')
        for evidence in seed['evidence']:
            if hashlib.sha256((ROOT/evidence['source']).read_bytes()).hexdigest()!=evidence['sha256']:raise ValueError('scene_seed_pixels_changed')
        (folder/'scene_seed.json').write_bytes(seed_path.read_bytes())
        data.setdefault('purchase_scenes',{})[key]=seed['scene']
    priorities=authorization.get('priorities',['any_perk'])
    if version in ('v53','v54'):priorities=data.get('killer_priorities',{}).get(authorization['character'],priorities)
    if data['role']=='killer':data['character']=authorization['character'];data.setdefault('killer_priorities',{})[data['character']]=list(priorities)
    data.setdefault('priorities',{})[data['role']]=list(priorities);data['selected']=list(priorities)
    data.setdefault('settings',{}).update(run_mode='continuous',fast_early_levels=authorization['allow_central_unlock'],fast_opening=version in ('v49','v52','v53','v54'))
    if version=='v52':
        data['settings'].update(experimental_persistent=authorization.get('persistent',False),
            physical_f9_check=True,auto_prestige=False,shadow_planner=False)
    if version in ('v53','v54'):
        data['settings'].update(persistent_route=True,experimental_persistent=False,
            physical_f9_check=True,auto_prestige=False,shadow_planner=False)
    preferences=folder/'test_preferences.json';preferences.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    logging.basicConfig(level=logging.INFO,handlers=[logging.FileHandler(folder/'app.log',encoding='utf-8')])
    sys.excepthook=lambda kind,value,traceback:logging.getLogger(__name__).error('Qt callback exception',exc_info=(kind,value,traceback))
    engines=[];saved=set();manifest=[];original_factory=gui.make_engine
    native=(folder/'native_input.jsonl').open('a',encoding='utf-8')
    def evidence(engine,reason):
        for frame,result,plan in list(engine.recent_frames)[-2:]:
            if result.analysis_id in saved or frame.raw_image is None:continue
            dest=folder/'frames'/(result.analysis_id+'.png')
            with engine.performance.span('live_evidence','io',analysis_id=result.analysis_id):
                write_image(dest,frame.raw_image);saved.add(result.analysis_id)
                manifest.append(dict(source=dest.relative_to(ROOT).as_posix(),sha256=hashlib.sha256(dest.read_bytes()).hexdigest(),analysis_id=result.analysis_id,frame=frame.metadata(),reason=reason))
                (folder/'frames.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    def factory(library,role,priorities,settings,approved,hotkeys):
        nonlocal deadline
        if version=='v54' and state['started_monotonic'] is None:
            state['started_monotonic']=time.monotonic();deadline=state['started_monotonic']+authorization['max_seconds']
            state_path.write_text(json.dumps(state,indent=2),encoding='utf-8')
        remaining=deadline-time.monotonic()
        if remaining<=0:raise ScreenError('runtime_limit')
        if budget.used>=budget.limit:raise ScreenError('action_limit')
        if role!=authorization['role'] or role=='killer' and settings.character!=authorization['character']:raise ScreenError('window_changed','authorized_character')
        settings.max_actions=min(settings.max_actions or budget.limit-budget.used,budget.limit-budget.used)
        settings.max_runtime=min(settings.max_runtime or remaining,remaining);settings.auto_prestige=False
        settings.fast_early_levels=settings.fast_early_levels and authorization['allow_central_unlock']
        engine=original_factory(library,role,priorities,settings,approved,hotkeys)
        if version=='v53':
            from scripts.live_test_v53 import install_limits
            install_limits(engine,authorization,path,deadline)
        if version=='v54':
            from scripts.live_test_v54 import install_limits
            install_limits(engine,authorization,path,deadline,folder)
        engine.buy=opening_scoped_buy(authorization,engine,engine.buy)
        purchase=engine.mouse.purchase;original_trace=engine.trace
        def trace(stage,**details):
            original_trace(stage,**details)
            if stage in ('SendInput','cursor_api','cursor_before_down'):
                with engine.performance.span('native_audit','io'):
                    native.write(json.dumps(dict(stage=stage,monotonic=time.monotonic(),**details),ensure_ascii=False)+'\n');native.flush()
            if (stage=='purchase_observation' and details.get('after')=='purchased' or
                    stage in ('new_level_confirmed','automatic_node_confirmed','failure')):evidence(engine,stage)
        engine.trace=trace;engine.mouse.audit=trace
        def bounded_purchase(point,duration,stop,check):
            if time.monotonic()>=deadline:raise ScreenError('runtime_limit')
            check_opening_scope(authorization,engine)
            evidence(engine,'before_input');engine.gate()
            # Evidence IO never extends freshness: revalidate the same physical
            # point against the newest frame before entering the native device.
            frame=engine.recent_frames[-1][0];local=local_point(frame,point)
            engine.check(frame,*local)
            return budget.invoke(purchase,point,duration,stop,check)
        engine.mouse.purchase=bounded_purchase;engines.append(engine);return engine
    gui.make_engine=factory
    class Window(gui.MainWindow):
        def open_calibration(self,*args):
            self.error_message='Покупочная калибровка требует отдельного разрешения.';self.status_key='paused';self.refresh_status()
        def start_automation(self,*args,**kwargs):
            if kwargs.get('calibrate') or len(args)>1 and args[1] is True:return self.open_calibration()
            if budget.used>=budget.limit or time.monotonic()>=deadline:
                self.error_message=self.locale.text('action_limit' if budget.used>=budget.limit else 'runtime_limit');self.status_key='paused';self.refresh_status();return
            return super().start_automation(*args,**kwargs)
    app=QApplication([]);window=Window(IconLibrary(ROOT).build(),preferences)
    window.setWindowTitle('AutoBloodWeb '+version+' — bounded live test');window.setAttribute(Qt.WA_ShowWithoutActivating,True)
    label=QLabel();label.setWordWrap(True);window.action_panel.layout().insertWidget(0,label)
    def progress():
        label.setText(f'Тест: {budget.used}/{budget.limit} вводов, до {authorization["max_seconds"]} с. F9 — Stop. Престиж вручную.')
        preparation=window.session_check
        session=dict(pid=os.getpid(),hwnd=int(window.winId()),window_visible=window.isVisible(),window_title=window.windowTitle(),authorization=authorization,input_calls=budget.used,remaining_seconds=max(0,deadline-time.monotonic()),status=window.status_key,reason=window.error_message,
            waiting_physical_f9=bool(window.session_check and window.session_check.waiting),
            preparation=dict(automatic=preparation.auto_start,closed=preparation.closed,counting=preparation.counting,waiting=preparation.waiting,succeeded=preparation.succeeded,receipt=bool(preparation.receipt),worker_running=bool(preparation.worker and preparation.worker.isRunning())) if preparation else None,
            engines=[dict(attempts=e.attempts,actions=e.actions,entity_absorptions=e.entity_actions,transitions=e.transitions,pending=e.pending_action,report=str(e.report.path),settings=asdict(e.settings),opening=e.opening.summary()) for e in engines])
        (folder/'session.json').write_text(json.dumps(session,ensure_ascii=False,indent=2),encoding='utf-8')
        if time.monotonic()>=deadline:
            for engine in engines:engine.halt()
    timer=QTimer(window);timer.setInterval(200);timer.timeout.connect(progress);timer.start()
    if version in ('v53','v54'):window.setGeometry(1300,150,620,850)  # Keep the real BP HUD visible.
    window.show();progress();print(folder,flush=True);QTimer.singleShot(700,window.start_automation)
    try:return app.exec()
    finally:progress();native.close()
if __name__=='__main__':raise SystemExit(main(Path(sys.argv[1])))
