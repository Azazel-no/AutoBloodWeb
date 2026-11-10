"""User-authorized Dark Lord optimization run, durable 2M BP aggregate ceiling.

The regular engine retains focus, freshness, direct availability, Stop and
per-node evidence. NativeReview supplies prices for early automatic unlock.
Restart this runner with the same authorization and ledger, never a fresh cap.
"""
import copy,json,math,os,subprocess,sys,time,traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.core.screen_manager import ScreenError
from src.core.models import NodeState,ScreenState
from src.core.series_budget import SeriesBudget
from scripts.live_test_v54 import NativeReview,scene_key
from scripts.live_test_v53 import standard_target


def save(path,value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
    temporary.replace(path)


def save_scene(path,scene,character='dark_lord',role='killer'):
    # Merge only the controller's scene into the latest on-disk profile. UI or
    # IDE edits to priorities/settings during a test must survive its teardown.
    data=json.loads(path.read_text(encoding='utf-8'));scenes=data.setdefault('purchase_scenes',{})
    key=role+'/'+(character if role=='killer' else '')
    if scene:scenes[key]=scene
    else:scenes.pop(key,None)
    save(path,data)


def close_idle_product(pid,hwnd,observed_idle=False):
    """Gracefully close the explicitly observed idle product window only."""
    import ctypes
    from ctypes import wintypes as W
    u=ctypes.WinDLL('user32',use_last_error=True);k=ctypes.WinDLL('kernel32',use_last_error=True)
    u.IsWindow.argtypes=[W.HWND];u.GetWindowTextW.argtypes=[W.HWND,W.LPWSTR,ctypes.c_int]
    u.GetWindowThreadProcessId.argtypes=[W.HWND,ctypes.POINTER(W.DWORD)]
    u.PostMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM]
    if not u.IsWindow(hwnd):return
    owner=W.DWORD();u.GetWindowThreadProcessId(hwnd,ctypes.byref(owner));title=ctypes.create_unicode_buffer(256)
    u.GetWindowTextW(hwnd,title,256)
    if owner.value!=pid or title.value!='AutoBloodWeb':raise RuntimeError('idle_product_window_changed')
    k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];k.OpenProcess.restype=W.HANDLE
    k.QueryFullProcessImageNameW.argtypes=[W.HANDLE,W.DWORD,W.LPWSTR,ctypes.POINTER(W.DWORD)]
    k.CloseHandle.argtypes=[W.HANDLE]
    handle=k.OpenProcess(0x1000,False,pid)
    if not handle:raise RuntimeError('idle_product_unverified')
    try:
        name=ctypes.create_unicode_buffer(32768);length=W.DWORD(len(name))
        allowed={(ROOT/'.venv/Scripts/pythonw.exe').resolve(),Path(sys._base_executable).with_name('pythonw.exe').resolve()}
        if not k.QueryFullProcessImageNameW(handle,0,name,ctypes.byref(length)) or Path(name.value).resolve() not in allowed:
            raise RuntimeError('idle_product_unverified')
    finally:k.CloseHandle(handle)
    if not u.PostMessageW(hwnd,0x10,0,0):raise RuntimeError('idle_product_close_failed')
    until=time.monotonic()+3
    while u.IsWindow(hwnd) and time.monotonic()<until:time.sleep(.1)
    if u.IsWindow(hwnd):
        if observed_idle is not True:raise RuntimeError('idle_product_close_refused')
        # Only the just-spawned, independently observed "Before use" GUI.
        # Its modal may ignore WM_CLOSE; it has no worker or unsaved edits.
        k.TerminateProcess.argtypes=[W.HANDLE,W.UINT]
        handle=k.OpenProcess(0x1001,False,pid)
        if not handle:raise RuntimeError('idle_product_close_refused')
        try:
            if not k.TerminateProcess(handle,0):raise RuntimeError('idle_product_close_refused')
        finally:k.CloseHandle(handle)


def make_status_callback(runner,budget,folder):
    last=[0]
    def event(state,message,payload):
        if getattr(budget,'authorization',{}).get('frame_comparison') and isinstance(payload,tuple) and len(payload)>=2:
            level=getattr(payload[1],'level',None)
            if type(level) is int:
                # Alternate complete nearby levels for comparison. The first
                # boundary observation can belong to the previous policy;
                # every frame records its actual policy in metadata.
                runner.screen.pipeline_enabled=not(10<=level<=20 and level%2==0)
        if time.monotonic()-last[0]>.3 or state.value in ('paused','error','stopped'):
            last[0]=time.monotonic()
            try:
                save(folder/'status.json',dict(pid=os.getpid(),state=state.value,message=message,
                    confirmed=runner.actions,commands=runner.attempts,pending=runner.pending_action or bool((runner.persisted_scene or {}).get('pending')),
                    spent=budget.data['spent'],action_report=str(runner.report.path),updated_at=time.time()))
            except OSError as exc:
                # A Windows reader may briefly deny rename. Status is an
                # observer; the durable pre-input ledger still fails closed.
                runner.trace('status_write_error',detail=str(exc),input_interrupted=False)
    return event


def install_limits(engine,authorization,folder):
    engine.native_center_catalog_bounds=authorization.get('allow_turbo_burst') is True
    engine.native_price_review_timeout=authorization.get('price_review_timeout',600)
    engine.native_review_ui_settle=0  # This headless stand has no window to hide.
    budget=SeriesBudget(folder/'ledger.json',authorization);review=NativeReview(engine,folder)
    calibration=folder/'native_price_locator.json'
    if not calibration.is_file() and hasattr(engine.screen,'resources'):calibration=ROOT/'data/native_price_locator.json'
    if calibration.is_file():
        from scripts.native_price_v55 import NativePriceLocator
        engine.native_price_locator=NativePriceLocator(ROOT,json.loads(calibration.read_text(encoding='utf-8')),engine.screen.resources)
        engine.native_single_price_scope=True
        total=ROOT/'data/native_total_price_locator.json'
        if total.is_file():
            from scripts.native_price_v55 import NativeTotalPriceLocator
            engine.native_review_total_locator=NativeTotalPriceLocator(ROOT,json.loads(total.read_text(encoding='utf-8')),engine.native_price_locator)
            if authorization.get('ordinary_chain_runtime') and not (folder/total.name).exists():
                (folder/total.name).write_bytes(total.read_bytes())
    # A prior visual review supplies box locations only. NativeReview still
    # decodes both new source frames and two new current hover frames.
    import threading
    def reuse_review_locations():
        seen=set()
        while not engine.stop.wait(.1):
            for request_path in folder.glob('price_review_*/request.json'):
                if request_path in seen:continue
                seen.add(request_path);destination=request_path.parent/'review.json'
                if destination.is_file():continue
                current=json.loads(request_path.read_text(encoding='utf-8'))
                for previous_path in sorted(folder.glob('price_review_*/review.json')):
                    previous_request=json.loads((previous_path.parent/'request.json').read_text(encoding='utf-8'))
                    before=previous_request['scene'];after=current['scene']
                    if any(before[k]!=after[k] for k in ('signature','roi','level')):continue
                    if len(before['nodes'])!=len(after['nodes']):continue
                    mapping={}
                    for node in after['nodes']:
                        matches=[old for old in before['nodes'] if math.dist(old['point'],node['point'])<=5 and
                                 old['icon']==node['icon'] and
                                 (old['state']==node['state'] or {old['state'],node['state']}=={'available','locked'})]
                        if len(matches)!=1:break
                        mapping[tuple(node['point'])]=matches[0]['point']
                    if len(mapping)!=len(after['nodes']):continue
                    previous=json.loads(previous_path.read_text(encoding='utf-8'))
                    updated=copy.deepcopy(previous);updated['clean_sha256']=current['clean_sha256']
                    for price in updated['prices']:
                        old=next(t for t in previous_request['targets'] if t['node']==price['node'])
                        source=next(t for t in current['targets'] if mapping.get(tuple(t['point']))==old['point'])
                        price.update(node=source['node'],source_sha256=source['frames'][0]['sha256'])
                    save(destination,updated);break
    if not calibration.is_file():threading.Thread(target=reuse_review_locations,daemon=True).start()
    original_buy=engine.buy;original_mouse=engine.mouse.purchase
    import src.core.early_unlock as early
    original_level=early.purchase_level
    active=dict(permit=None,index=None)
    def scope():
        if (engine.settings.character!=authorization['character'] or engine.priorities!=authorization['priorities'] or
                engine.settings.auto_prestige and not authorization['allow_prestige']):raise ScreenError('chain_scope_changed')
        if time.time()>=authorization['expires_at']:raise ScreenError('runtime_limit')
        if (folder/'finish.json').exists():raise ScreenError('validation_complete')
        if (folder/'validation_pause.json').exists() and not engine.pending_action:
            raise ScreenError('validation_pause')
    original_safety=engine.live_safety
    def safety():scope();original_safety()
    engine.live_safety=safety
    def automatic(frame,result,plan):
        scope()
        if authorization.get('allow_early_center') is not True:raise ScreenError('automatic_unlock_unproven')
        targets=[n for n in result.nodes if n.state in (NodeState.AVAILABLE,NodeState.LOCKED)]
        value=review.prepare(frame,result,plan,targets,'early_center')
        active.update(permit=review.permit,index=None)
        return value
    def settle(frame):
        if active['index'] is not None:budget.settle(active['index'],frame.bloodpoints)
        active.update(permit=None,index=None)
    group_done=[False]
    def reserve_burst(group):
        scope()
        if authorization.get('allow_turbo_burst') is not True:raise ScreenError('group_session_not_ready')
        points=[list(group.frame.to_screen(n.x,n.y)) for n in group.targets]
        price=getattr(group,'price_upper_bound',25000)
        active.update(permit=dict(kind='batch',points=points,created=time.monotonic(),price=price,operation_id=group.ident),
            index=budget.reserve(group.frame.bloodpoints,price,'batch',points,operation_id=group.ident))
        engine.trace('series_operation_reserved',reservation=active['index'],price_upper_bound=price,kind='direct_burst')
    engine.reserve_burst=reserve_burst
    if (folder/'events.jsonl').exists():
        group_done[0]=any(json.loads(line).get('stage')=='group_inputs_complete' and json.loads(line).get('commands')==3
            for line in (folder/'events.jsonl').read_text(encoding='utf-8').splitlines() if line.strip())
    def buy(frame,result,node=None,point=None,**kwargs):
        scope()
        if point is not None or node is None:raise ScreenError('prestige_manual')
        phase=('single','group','chain')[(budget.data['commands']//6)%3] if authorization.get('compare_groups') else 'chain'
        comparison=folder/'turbo_opening_first.json'
        if (comparison.is_file() and not (folder/'turbo_comparison_used.json').exists() and
                authorization.get('allow_turbo_burst') and result.level==json.loads(comparison.read_text())['level']
                and not engine.attempted_nodes(frame,result)):
            phase='turbo'
            save(folder/'turbo_comparison_used.json',dict(level=result.level,paid_inputs_before=budget.data['commands'],
                reason='authorized_comparison_of_five_separate_inputs',frame=frame.metadata()))
        if authorization.get('compare_groups') and not group_done[0]:phase='group'
        engine.trace('comparison_phase',phase=phase,aggregate_commands=budget.data['commands'])
        if phase in ('chain','turbo'):
            burst=engine.try_turbo(frame,result,node,kwargs.get('strict',False))
            if burst:settle(burst[0]);return burst
        if phase=='group' and authorization.get('allow_groups'):
            from src.core.grouped_purchase import candidates,execute_group
            targets=candidates(engine,frame,result,standard_target)
            if targets and budget.max_commands-budget.data['commands']>=3 and budget.max_nodes-sum(len(r['points']) for r in budget.data['reservations'])>=3:
                def reserve(group):
                    active.update(permit=dict(kind='batch',points=[list(frame.to_screen(n.x,n.y)) for n in targets],
                        created=time.monotonic(),price=15000,operation_id=group.ident),index=budget.reserve(frame.bloodpoints,15000,'batch',
                            [frame.to_screen(n.x,n.y) for n in targets],operation_id=group.ident))
                    engine.trace('series_operation_reserved',reservation=active['index'],price_upper_bound=15000,kind='batch')
                outcome=execute_group(engine,frame,result,targets,reserve)
                group_done[0]=getattr(engine,'last_group_commands',0)==3
                settle(outcome[0]);return outcome
            engine.trace('group_skipped',reason='three_direct_priority_targets_not_proven')
        from scripts.native_chain_v55 import probe_if_requested
        from src.core.direct_buy import chain_probe_allowed,reject_chain,known_price,remember_price,preparation_key,prefer_direct_single
        if phase=='chain' and prefer_direct_single(engine,frame,result,node):
            engine.trace('chain_skipped',reason='known_single_target_direct_buy',paid_inputs=0);phase='direct'
        if phase=='chain' and not chain_probe_allowed(engine,frame,result):phase='direct'
        if phase=='chain' and authorization.get('chain_continuous') and not (folder/'chain_probe_requested.json').exists():
            from src.core.chain_plan import native_candidate_route
            route=native_candidate_route(result,engine.priorities,engine.settings.priority_mode)
            if len(route.route)>=2:
                save(folder/'chain_probe_requested.json',dict(execute=True,continuous=True,approved=True,
                    acknowledge_queue_cancellation_unverified=authorization.get('acknowledge_queue_cancellation_unverified') is True,
                    scope_id=authorization['scope_id'],ledger='chain_ledger.json',max_commands=authorization['max_commands'],
                    max_nodes=authorization['max_nodes'],max_bp=authorization['max_bp'],expires_at=authorization['expires_at'],
                    max_chain_items=5 if authorization.get('allow_turbo_burst') else 3))
        try:
            chained=probe_if_requested(engine,folder,frame,result) if phase=='chain' else None
            if chained:return chained
        except ScreenError as exc:
            if not authorization.get('chain_continuous') or engine.pending_action or engine.stop.is_set() or exc.code not in (
                    'chain_preview_or_price_unproven','chain_map_changed','chain_geometry_changed','chain_two_clean_frames_required','unsafe_node'):
                raise
            engine.trace('chain_skipped',reason=exc.code,paid_inputs=0)
            fresh=engine.recover_read(retry_stale=True)
            reject_chain(engine,*fresh[:2])
            frame,result,node=engine.single_after_probe(frame,result,node,fresh)
        bound=known_price(engine,frame,node)
        if bound:
            price=bound.value;proofs=[]
            engine.trace('direct_buy_prepared',node=node.id,point=frame.to_screen(node.x,node.y),
                price_upper_bound=price,price_source=bound.source,price_probes=0,source='bounded_ordinary_start')
        else:
            engine.trace('direct_buy_fallback',node=node.id,reason='price_identity_or_current_native_observation_unproven')
            position=frame.to_screen(node.x,node.y)
            before_sequence=getattr(engine.mouse,'down_attempt_sequence',None)
            try:frame,result,_=review.prepare(frame,result,None,[node],'single')
            except ScreenError as exc:
                if (exc.code in ('chain_map_changed','chain_price_unproven') and type(before_sequence) is int and
                        engine.mouse.down_attempt_sequence==before_sequence and not engine.pending_action):
                    engine.preparation_changed_without_input=True
                    engine.trace('native_price_preparation_changed',reason=exc.code,paid_inputs=0)
                    raise ScreenError('purchase_preparation_changed') from exc
                raise
            prepared_point=review.permit.get('points',[list(position)])[0]
            node=next(n for n in result.nodes if math.dist(frame.to_screen(n.x,n.y),prepared_point)<5)
            price=review.permit['price'];proofs=review.permit['proofs']
            remember_price(engine,frame,node,review.permit)
        active.update(permit=dict(kind='single',key=scene_key(frame,result),points=[list(frame.to_screen(node.x,node.y))],
                                  price=price,proofs=proofs,created=time.monotonic()),index=None)
        if proofs:
            from scripts.native_price_v55 import prepared_single_key
            active['permit']['key']=prepared_single_key(frame,result,review.permit['key'])
        elif getattr(engine,'retained_single_target',None)==(frame.window.signature,result.level,frame.to_screen(node.x,node.y),frame.captured_at):
            active['permit']['key']['single_filler_preparation']=True
        engine.direct_prepared_single=dict(key=preparation_key(frame,node),price=price)
        value=original_buy(frame,result,node=node,**kwargs);settle(value[0]);return value
    def level(*args,**kwargs):
        value=original_level(*args,**kwargs);settle(value[0]);return value
    def mouse(point,duration,stop,check):
        scope();frame,result,_=engine.recent_frames[-1]
        # CONTINUE is a separately detected free UI action, never a paid permit.
        if result.screen_state in (ScreenState.TUTORIAL,ScreenState.REWARD):
            check();budget.command();return original_mouse(point,duration,stop,check)
        permit=active['permit']
        if not permit:raise ScreenError('chain_map_changed')
        age_limit=max(5,min(8,engine.settings.purchase_timeout)) if permit['kind']=='early_center' else min(8,engine.settings.purchase_timeout) if permit['kind']=='batch' else 5
        if time.monotonic()-permit['created']>age_limit:raise ScreenError('chain_map_changed')
        if permit['kind']=='batch':
            if (not (authorization.get('allow_groups') or authorization.get('allow_turbo_burst') and engine.burst_inputs_active) or engine.active_group is None or
                    engine.active_group.ident!=permit['operation_id'] or list(point) not in permit['points']):
                raise ScreenError('group_session_not_ready')
        elif permit['kind']=='chain':
            if not authorization['allow_chain'] or list(point)!=permit['points'][-1]:raise ScreenError('chain_scope_changed')
            if permit['key']!=scene_key(frame,result):raise ScreenError('chain_map_changed')
        elif permit['kind']=='single' and getattr(engine,'native_single_price_scope',False):
            from scripts.native_price_v55 import single_scene_valid
            if not single_scene_valid(permit['key'],*engine.recent_frames[-1],point):raise ScreenError('chain_map_changed')
        elif permit['kind']=='early_center':
            from scripts.native_price_v55 import center_scene_valid
            if not center_scene_valid(permit['key'],frame,result):raise ScreenError('chain_map_changed')
        elif permit['key']!=scene_key(frame,result):raise ScreenError('chain_map_changed')
        if permit['kind']=='prestige':
            if not authorization['allow_prestige'] or result.screen_state!=ScreenState.PRESTIGE_READY:
                raise ScreenError('prestige_manual')
        elif permit['kind']=='early_center':
            if not 1<=result.level<=9 or not engine.scene_record['pending'].get('automatic'):raise ScreenError('automatic_unlock_unproven')
            center=next(n for n in result.nodes if n.state==NodeState.CENTER)
            if math.dist(point,frame.to_screen(center.x,center.y))>5:raise ScreenError('unsafe_node')
        elif permit['kind'] not in ('chain','batch') and list(point)!=permit['points'][0]:raise ScreenError('unsafe_node')
        check()
        if active['index'] is None:
            from src.core.action_outcomes import operation_id
            active['index']=budget.reserve(frame.bloodpoints,permit['price'],permit['kind'],permit['points'],
                operation_id=operation_id(engine.scene_record) if engine.scene_record else None)
            engine.trace('series_operation_reserved',reservation=active['index'],price_upper_bound=permit['price'],kind=permit['kind'])
        budget.command()
        return original_mouse(point,duration,stop,check)
    def chain(plan,permit,previous,latest):
        scope()
        if authorization.get('allow_chain') is not True:raise ScreenError('chain_explicit_permission_required')
        from src.core.chain_trial import execute_short_chain
        frame,result=latest
        active.update(permit=dict(kind='chain',key=scene_key(frame,result),points=[list(p) for p in plan.physical_points],
                                 price=plan.total_bp,created=time.monotonic()),index=None)
        old_mode=engine.settings.run_mode
        production=authorization.get('ordinary_chain_runtime') is True
        if production:
            engine.settings.native_chain=True;engine.native_chain_permit=permit
        else:engine.settings.run_mode='test'
        try:
            outcome=execute_short_chain(engine,plan,permit,previous,latest)
            if not engine.pending_action:
                if outcome.get('unconfirmed'):
                    # A proven new map is not proof of each old item. Keep
                    # the whole reservation charged even when input retires.
                    budget.observe(engine.recent_frames[-1][0].bloodpoints)
                    active.update(permit=None,index=None)
                else:settle(engine.recent_frames[-1][0])
            return outcome
        finally:
            engine.settings.run_mode=old_mode;engine.native_chain_permit=None;active.update(permit=None,index=None)
    engine.execute_native_chain=chain
    if authorization['allow_prestige']:
        import src.core.prestige as prestige
        original_prestige=prestige.activate_ready
        def activate(frame,result):
            scope();proof=getattr(engine,'prestige_ui_proof',None)
            if not proof:raise ScreenError('prestige_price_unproven')
            active.update(permit=dict(kind='prestige',key=scene_key(frame,result),points=[list(proof['point'])],
                price=proof['price'],created=time.monotonic()),index=None)
            value=original_prestige(engine,frame,result);settle(value[0]);return value
        prestige.activate_ready=lambda current,frame,result:activate(frame,result) if current is engine else original_prestige(current,frame,result)
    engine.prepare_automatic=automatic;engine.buy=buy;engine.mouse.purchase=mouse;early.purchase_level=level
    return budget


def enable_crash_trace(stream):
    import faulthandler
    # The live minidump located the access violation in the periodic native
    # traceback watchdog, not the purchase thread. Keep fatal diagnostics;
    # normal progress is recorded by intervals and level checkpoints.
    faulthandler.cancel_dump_traceback_later()
    faulthandler.enable(stream)


def main(folder):
    import faulthandler
    stack_log=(folder/'native_stall_stacks.txt').open('a',encoding='utf-8')
    enable_crash_trace(stack_log)
    from src.core.icon_library import IconLibrary
    from src.core.preferences import Preferences
    from src.core.automation import Settings
    from src.core.input_control import GlobalHotkeys
    from src.gui.automation_worker import make_engine
    from scripts.live_test_v48 import acquire_scope
    folder=folder.resolve();authorization=json.loads((folder/'authorization.json').read_text(encoding='utf-8'))
    lock=acquire_scope(folder/'authorization.json');runner=None;keys=None;prefs=None
    history=folder/'runs';history.mkdir(exist_ok=True)
    previous=folder/'session.json'
    if previous.is_file():
        old=json.loads(previous.read_text(encoding='utf-8'));label=str(old['pid'])+'_'+str(int(old['started_at']))
        for name in ('session.json','result.json'):
            if (folder/name).is_file(): (history/(label+'_'+name)).write_bytes((folder/name).read_bytes())
    result=dict(pid=os.getpid(),started_at=time.time(),authorization=authorization);user_stop=[False]
    import hashlib
    result['input_source_sha256']={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
        'src/core/input_control.py','src/core/automation.py','src/core/click_lifecycle.py',
        'src/core/chain_trial.py','src/core/direct_buy.py','src/core/turbo_burst.py','src/core/grouped_purchase.py',
        'scripts/live_test_v54.py','scripts/native_price_v55.py','scripts/live_optimize_v55.py')}
    try:
        if time.time()>=authorization['expires_at']:raise ScreenError('runtime_limit')
        close_request=folder/'close_idle.json'
        if close_request.is_file():
            idle=json.loads(close_request.read_text(encoding='utf-8'))
            for item in idle if isinstance(idle,list) else [idle]:close_idle_product(item['pid'],item['hwnd'],item.get('observed_idle',False))
            close_request.unlink()
        library=IconLibrary(ROOT).build();prefs=Preferences(ROOT/'data/preferences.json',library.entries)
        role=authorization['role']
        if ((role=='killer' and prefs.character!=authorization['character']) or
                list(prefs.priorities[role])!=authorization['priorities']):
            raise ScreenError('chain_scope_changed','Current selected profile differs from the stand snapshot')
        if not prefs.saved_calibration() or prefs.settings['purchase_mode']!='repeat':raise ScreenError('calibration_required')
        settings=Settings(dry_run=False,auto_prestige=authorization['allow_prestige'],purchase_mode='repeat',hold_seconds=.25,
            character=authorization['character'],fast_early_levels=authorization.get('allow_early_center',False),fast_opening=True,persistent_route=True,
            turbo_burst=authorization.get('allow_turbo_burst',False),
            priority_mode=prefs.settings.get('priority_mode','smart'),startup_delay=3,poll_interval=.1,
            focus_timeout=120,max_actions=authorization.get('control_phase_actions'),max_runtime=None,max_input_commands=authorization.get('max_commands',4000),
            run_mode='continuous',detailed_diagnostics=False)
        runner=make_engine(library,role,authorization['priorities'],settings,True,False)
        runner.prestige_approved=authorization['allow_prestige']
        original_add=runner.report.add;restored=[None]
        critical={'SendInput','before_purchase','purchase_observation','purchase_outcome','failure','state',
                  'new_level_confirmed','automatic_node_confirmed','automatic_level_outcome','run_settings',
                  'run_counters','series_operation_reserved','saved_scene_replaced','repeat_click','native_price_review_required',
                  'chain_before','chain_observation','chain_outcome','chain_unresolved','native_chain_trial_finished'}
        critical.update(('analysis','operation_reconciled','chain_skipped','target_confirmation','comparison_phase',
            'group_before','group_input_guard','group_inputs_complete','group_observation','group_outcome','group_skipped'))
        critical.update(('direct_buy_prepared','direct_buy_fallback','direct_burst_prepared','burst_eligibility',
            'burst_inputs_complete','burst_outcome','burst_interrupted','burst_prefix_stopped'))
        critical.update(('prestige_before','prestige_repeat','prestige_confirmed','modal_input','reward_dismissed','tutorial_dismissed'))
        critical.update(('burst_eligibility','burst_outcome','burst_interrupted','burst_inputs_complete','burst_prefix_stopped','burst_preflight_refresh','cursor_before_down','input_not_submitted','preinput_stale_recovery','preinput_guard_recovery','preparation_target_changed','chain_late_recheck','chain_late_reconciled'))
        critical.update(('chain_expected_purchases','chain_confirmation_recrop'))
        critical.update(('opening_fallback','opening_started','chain_native_probe_complete','native_price_clean_wait',
            'native_price_same_hover_reused','target_confirmation_fallback','native_price_preparation_changed'))
        critical.update(('turbo_opening_closed','proof_enqueued','proof_writer_finished','terminal_scene_observed','saved_chain_transition_retired','native_price_target_refreshed','transition_confirmation_grace','native_price_equivalent_filler','saved_single_transition_retry'))
        def audit(stage,**details):
            original_add(stage,**details)
            if stage=='opening_fallback' and details.get('reason')=='absorbed_state_contradiction':
                # Pin the actual contradictory pixels before normal diagnostic
                # retention can rotate them. At most two per observed level.
                level=getattr(getattr(runner.opening,'last_frame',None),'level',None)
                retained=getattr(runner,'retained_state_fallbacks',{})
                if retained.get(level,0)<2 and getattr(runner.screen,'last_raw',None) is not None:
                    from src.utils.images import write_image
                    import hashlib
                    with runner.performance.span('fault_evidence_io','io'):
                        retained[level]=retained.get(level,0)+1;runner.retained_state_fallbacks=retained
                        dest=folder/f'absorbed_fallback_{os.getpid()}_{level}_{retained[level]}';dest.mkdir(exist_ok=False)
                        write_image(dest/'raw.png',runner.screen.last_raw)
                        save(dest/'source.json',dict(reason=details['reason'],state_node=details.get('state_node'),
                            capture=runner.screen.last_capture,previous=runner.opening.last_frame.metadata(),
                            nodes=[dict(id=n.id,x=n.x,y=n.y,radius=n.radius,state=n.state.value) for n in runner.opening.nodes],
                            sha256=hashlib.sha256((dest/'raw.png').read_bytes()).hexdigest(),paid_inputs=0))
            if stage=='operation_reconciled':
                for index,reservation in enumerate(budget.data['reservations']):
                    if reservation.get('operation_id')==details['operation_id']:
                        reservation.update(observed_outcome_id=details['operation_id'],observed_confirmed_nodes=details['confirmed_nodes'],
                            observed_absorbed_nodes=details['absorbed_nodes'],observed_bp_delta=details['bp_delta'])
                        budget.save()
                        if (restored[0] and (restored[0].get('pending') or {}).get('operation_id')==details['operation_id']
                                and not reservation['settled']):
                            budget.settle(index,runner.recent_frames[-1][0].bloodpoints);restored[0]=None
                        break
            if (stage=='new_level_confirmed' and details.get('source')=='persisted_scene_geometry'
                    and restored[0] and (restored[0].get('pending') or {}).get('automatic')):
                ident=restored[0]['pending'].get('operation_id')
                for index,reservation in enumerate(budget.data['reservations']):
                    if reservation.get('operation_id')==ident and not reservation['settled']:
                        budget.settle(index,runner.recent_frames[-1][0].bloodpoints);break
                restored[0]=None
            if stage in critical:
                row=dict(session=os.getpid(),**runner.report.entries[-1])
                with (folder/'events.jsonl').open('a',encoding='utf-8') as stream:stream.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')
        runner.report.add=audit
        initial=runner.screen.backend.info()
        if (initial.hwnd,initial.pid)!=(authorization['hwnd'],authorization['pid']):raise ScreenError('window_changed')
        if not initial.foreground:
            import ctypes
            from ctypes import wintypes as W
            native=ctypes.WinDLL('user32',use_last_error=True)
            native.SetForegroundWindow.argtypes=[W.HWND];native.SetForegroundWindow.restype=W.BOOL
            runner.trace('focus_request',hwnd=initial.hwnd,result=bool(native.SetForegroundWindow(initial.hwnd)))
        def stop_from_hotkey(action):
            user_stop[0]=True;runner.halt()
        keys=GlobalHotkeys(stop_from_hotkey,prefs.settings['hotkeys'])
        runner.hotkeys_ready=keys.ready;runner.input_ready_check=lambda:keys.native_health()['ready']
        if not keys.native_health()['ready']:raise ScreenError('hotkey_registration_failed',keys.error_detail)
        result['registration']=keys.native_health();result['action_report']=str(runner.report.path)
        memory=prefs.data.get('purchase_scenes',{}).get(role+'/'+(authorization['character'] if role=='killer' else ''))
        if memory:
            restored[0]=copy.deepcopy(memory)
            runner.persisted_scene=copy.deepcopy(memory);runner.scene_record=copy.deepcopy(memory)
            runner.attempted=[tuple(p) for p in memory.get('attempted',[])]
        budget=install_limits(runner,authorization,folder)
        runner.event=make_status_callback(runner,budget,folder);save(folder/'session.json',result)
        runner.run()
        result.update(state=runner.state.value,commands=runner.attempts,confirmed=runner.actions,
                      pending=runner.pending_action or bool((runner.persisted_scene or {}).get('pending')),ledger=budget.data,opening=runner.opening.summary())
    except Exception as exc:
        result.update(error=str(exc),traceback=traceback.format_exc())
    finally:
        if runner:
            runner.mouse.release()
            if prefs:
                save(folder/'scene_record.json',dict(scene=runner.scene_record,attempted=runner.attempted,pending=runner.pending_action))
                save_scene(prefs.path,runner.scene_record,authorization['character'],authorization['role'])
        if keys:keys.close()
        result['finished_at']=time.time();save(folder/'result.json',result)
        save(folder/'status.json',dict(**result,finished=True))
        # Keep the already elevated test host available for a corrected product
        # restart under the same fixed scope. No arbitrary commands are accepted.
        resume=folder/'resume.json'
        restart=False
        if not (folder/'finish.json').exists() and (result.get('error') or (runner and (runner.pause.is_set() or runner.state.value in ('paused','error')) and not user_stop[0])):
            until=min(time.time()+900,authorization['expires_at'])
            while time.time()<until and not resume.is_file():time.sleep(.2)
            if resume.is_file():
                resume.unlink();restart=True
        lock.close()
        command=[str(Path(__file__)),str(folder)] if restart else ['-m','src.app']
        if not (folder/'finish.json').exists():subprocess.Popen([str(ROOT/'.venv/Scripts/pythonw.exe'),*command],cwd=ROOT)


if __name__=='__main__':
    selected=Path(sys.argv[1]).resolve()
    if '--await-start' in sys.argv:
        # One ordinary UAC consent now; the later child imports the final
        # checked sources with the same token. This host creates no Mouse.
        from src.core.process_access import process_integrity
        scope=json.loads((selected/'authorization.json').read_text(encoding='utf-8'))
        save(selected/'launcher_ready.json',dict(pid=os.getpid(),access=process_integrity(os.getpid()),paid_inputs=0))
        while time.time()<scope['expires_at']:
            if (selected/'start.json').is_file():
                (selected/'start.json').unlink()
                subprocess.Popen([sys.executable,str(Path(__file__).resolve()),str(selected)],cwd=ROOT)
                break
            time.sleep(.2)
    else:main(selected)
