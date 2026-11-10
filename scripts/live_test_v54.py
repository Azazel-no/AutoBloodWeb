"""Explicit renewed Animatronic trial: native prices, reviewed complete maps, capped centre.

The ordinary Start/worker/F9/input/evidence path is reused. No prestige/chain,
no guessed prices or IDs. Review certificates contain original pixels/SHA/boxes.
"""
import copy,hashlib,json,math,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.core.runtime_paths import project_root
ROOT=project_root()
from src.core.models import NodeState,ScreenState
from src.core.screen_manager import ScreenError
from src.utils.images import write_image,read_image
from src.core.click_lifecycle import price_preparation


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_authorization(data):
    if (data.get('version')!='v54' or data.get('approved') is not True or
        any(type(data.get(k)) is not int or not 1<=data[k]<=cap for k,cap in
            [('authorized_limit',48),('max_nodes',48),('max_bp',240000),('max_seconds',600)]) or
        data.get('character')!='animatronic' or data.get('role')!='killer' or
        data.get('allow_central_unlock') is not True or data.get('allow_prestige') is not False or
        data.get('allow_chain') is not False or not isinstance(data.get('priorities'),list)):
        raise ValueError('invalid_v54_authorization')
    return data


class OperationBudget:
    def __init__(self,path,authorization,deadline):
        self.path=Path(path);self.a=validate_authorization(authorization);self.deadline=deadline
        self.sha=hashlib.sha256(json.dumps(authorization,sort_keys=True).encode()).hexdigest()
        if self.path.is_file():
            try:self.data=json.loads(self.path.read_text(encoding='utf-8'))
            except (ValueError,OSError) as exc:raise ScreenError('invalid_chain_ledger') from exc
            if self.data.get('authorization_sha256')!=self.sha:raise ScreenError('chain_ledger_scope_mismatch')
            for key,cap in [('commands',self.a['authorized_limit']),('nodes',self.a['max_nodes']),('bp',self.a['max_bp'])]:
                if type(self.data.get(key)) is not int or not 0<=self.data[key]<=cap:raise ScreenError('invalid_chain_ledger')
        else:self.data=dict(authorization_sha256=self.sha,commands=0,nodes=0,bp=0,initial_bp=None,reservations=[])

    def check(self,bp,nodes,price):
        if time.monotonic()>=self.deadline:raise ScreenError('runtime_limit')
        if type(bp) is not int or bp<0:raise ScreenError('chain_bp_unproven')
        if type(nodes) is not int or nodes<1 or type(price) is not int or price<1:raise ScreenError('chain_price_unproven')
        d=self.data
        if d['commands']+1>self.a['authorized_limit'] or d['nodes']+nodes>self.a['max_nodes']:raise ScreenError('action_limit')
        if d['bp']+price>self.a['max_bp'] or bp<price:raise ScreenError('chain_budget_limit')
        if d['initial_bp'] is not None and d['initial_bp']-bp>d['bp']:raise ScreenError('chain_bp_exceeded')

    def reserve(self,bp,points,price,kind,evidence):
        self.check(bp,len(points),price);d=self.data
        if d['initial_bp'] is None:d['initial_bp']=bp
        d['commands']+=1;d['nodes']+=len(points);d['bp']+=price
        d['reservations'].append(dict(points=points,price_upper_bound=price,kind=kind,evidence=evidence,bp_before=bp,at=time.monotonic()))
        temp=self.path.with_suffix('.tmp')
        with temp.open('w',encoding='utf-8') as stream:
            json.dump(d,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,self.path)


def scene_key(frame,result):
    return dict(signature=dict(hwnd=frame.window.hwnd,pid=frame.window.pid,client=vars(frame.window.client),monitor=vars(frame.window.monitor),dpi=frame.window.dpi),roi=vars(frame.roi),level=result.level,
        nodes=[dict(point=list(frame.to_screen(n.x,n.y)),state=n.state.value,icon=n.icon_id,
            radius=n.radius*frame.roi.width/frame.image.shape[1]) for n in result.nodes])


from src.core.performance import measured,phase


@measured('price_reobserve')
def reobserve_price(engine,frame,decode,expected,folder,node):
    """Two consecutive independent current readings; Unknown has a short bound."""
    values=[];stamps=[];observations=[]
    for _ in range(4):
        engine.wait(.3)
        with phase(engine,'overlay_capture','capture'):fresh=engine.screen.capture_overlay(frame)
        with phase(engine,'price_ocr','compute'):value=decode(fresh.image)
        observations.append((fresh,value))
        if value==expected and (not stamps or fresh.captured_at>stamps[-1]):
            values.append(value);stamps.append(fresh.captured_at)
            if len(stamps)==2:return values,stamps
        else:
            values=[];stamps=[]
            if value is not None:break
    for i,(observation,value) in enumerate(observations):
        write_image(folder/f'current_{node}_{i}.png',observation.image)
    (folder/'current_failure.json').write_text(json.dumps([dict(stamp=f.captured_at,value=v) for f,v in observations]),encoding='utf-8')
    raise ScreenError('chain_price_unproven')


@measured('price_clean_confirmation')
def wait_price_scene(engine,scene,point,single_scope,center_scope=False):
    """Require two clean maps after hover; never retry the purchase/hover."""
    from scripts.native_price_v55 import single_scene_valid
    until=min(time.monotonic()+min(5,engine.settings.purchase_timeout),getattr(engine,'runtime_deadline',float('inf')))
    previous=None;refreshed=False;replacement=None
    for attempt in range(12 if center_scope else 4):
        engine.gate()
        if time.monotonic()>=until:break
        current=engine.recover_read(until,retry_stale=True);f,r,p=current
        from scripts.native_price_v55 import center_scene_valid
        valid=single_scene_valid(scene,f,r,p,point) if single_scope else center_scene_valid(scene,f,r) if center_scope else scene_key(f,r)==scene
        if type(scene.get('level')) is int and (r.level!=scene['level'] or r.level_confidence<.95):valid=False
        if (single_scope and not valid and f.live and not f.probe_only and f.window.foreground
                and scene_key(f,r)['signature']==scene['signature']
                and r.level==scene.get('level') and r.level_confidence>=.95
                and p is not None and p.reason in ('priority','smart_priority','PERSISTENT:priority','PERSISTENT:smart_priority')):
            from src.core.geometry import purchasable
            higher=next((n for n in r.nodes if n.id==p.next_node),None)
            if higher and purchasable(higher,r.nodes,r.edges) and math.dist(f.to_screen(higher.x,higher.y),point)>5:
                if (replacement and replacement[0].captured_at<f.captured_at
                        and engine.stable_scene((*replacement[:2],None),(*current[:2],None))):
                    engine.preparation_changed_without_input=True;engine.preparation_replacement=current
                    engine.trace('preparation_target_changed',reason='higher_priority_observed',previous_point=point,
                        selected_point=f.to_screen(higher.x,higher.y),stamps=[replacement[0].captured_at,f.captured_at],paid_inputs=0)
                    raise ScreenError('purchase_preparation_changed','higher_priority_observed')
                replacement=current
            else:replacement=None
        else:replacement=None
        if valid and single_scope:
            selected=next(n for n in r.nodes if math.dist(f.to_screen(n.x,n.y),point)<=5)
            if p.next_node!=selected.id:
                engine.trace('native_price_equivalent_filler',point=point,known_id=selected.icon_id,
                    fresh_next=p.next_node,reason=p.reason,stamp=f.captured_at,paid_inputs=0)
        if valid and previous and previous[0].captured_at<f.captured_at:
            def target(value):
                frame,result,plan=value
                return next((n for n in result.nodes if math.dist(frame.to_screen(n.x,n.y),point)<=5),None) if single_scope else None
            if engine.stable_scene((*previous[:2],target(previous)),(*current[:2],target(current))):return previous,current
        previous=current if valid else None
        if center_scope and not valid and not refreshed and len(scene['nodes'])!=len(r.nodes):
            # A fading card can seed an incomplete compact map. Re-read full
            # geometry once; never fill the missing points from the old map.
            engine.opening.cancel('native_price_clean_incomplete');refreshed=True
        engine.trace('native_price_clean_wait',observation=attempt+1,valid=valid,paid_inputs=0)
        engine.poll()
    raise ScreenError('chain_map_changed')


class NativeReview:
    def __init__(self,engine,folder):self.engine=engine;self.folder=folder;self.serial=len(list(folder.glob('price_review_*')));self.permit=None

    @price_preparation
    @measured('price_preparation')
    def prepare(self,frame,result,plan,targets,kind):
        engine=self.engine
        selected_frame,selected_result=frame,result
        original_points=[frame.to_screen(n.x,n.y) for n in targets]
        engine.event(engine.state,'hide_for_dialog',None)
        engine.wait(getattr(engine,'native_review_ui_settle',.4))
        frame,result,plan=engine.recover_read(retry_stale=True)
        if kind=='early_center':
            targets=[n for n in result.nodes if n.state in (NodeState.AVAILABLE,NodeState.LOCKED)]
        else:
            if (kind=='single' and getattr(getattr(engine,'settings',None),'run_mode',None)=='continuous' and not getattr(engine,'calibrating',False)
                    and plan is not None and plan.next_node is not None):
                from src.core.geometry import purchasable
                selected=next((n for n in result.nodes if n.id==plan.next_node),None)
                if len(targets)==1 and hasattr(engine,'single_after_probe'):
                    _,_,selected=engine.single_after_probe(selected_frame,selected_result,targets[0],(frame,result,plan))
                if selected is None or not purchasable(selected,result.nodes,result.edges):raise ScreenError('chain_map_changed')
                selected_point=frame.to_screen(selected.x,selected.y)
                if math.dist(selected_point,original_points[0])>5:
                    engine.trace('native_price_target_refreshed',previous_point=original_points[0],
                        selected_point=selected_point,reason='fresh_worker_plan',paid_inputs=0)
                original_points=[selected_point]
            matches=[n for n in result.nodes if n.state==NodeState.AVAILABLE and math.dist(frame.to_screen(n.x,n.y),original_points[0])<5]
            if len(matches)!=1:raise ScreenError('chain_map_changed')
            targets=matches
        self.serial+=1;folder=self.folder/f'price_review_{self.serial:03d}';folder.mkdir()
        write_image(folder/'clean.png',frame.raw_image)
        request=dict(kind=kind,level=result.level,scene=scene_key(frame,result),clean=str((folder/'clean.png').relative_to(ROOT)),
            clean_sha256=digest(folder/'clean.png'),targets=[],status='collecting',deadline=engine.runtime_deadline)
        if (kind=='single' and getattr(engine,'native_single_price_scope',False)
                and getattr(getattr(engine,'settings',None),'run_mode',None)=='continuous'
                and not getattr(engine,'calibrating',False) and getattr(plan,'reason',None) in ('filler','PERSISTENT:filler')):
            request['scene']['single_filler_preparation']=True
        standard_bounds=set();native_observations={}
        if kind=='early_center' and getattr(engine,'native_center_catalog_bounds',False):
            from scripts.live_test_v53 import standard_target
            for node in targets:
                candidate=copy.deepcopy(node);candidate.state=NodeState.AVAILABLE
                try:standard_target(engine,candidate)
                except ScreenError:continue
                standard_bounds.add(node.id)
        locator=getattr(engine,'native_price_locator',None)
        total_locator=getattr(engine,'native_review_total_locator',None)
        def locate_price(image,point):
            # An explicitly labelled total is also a conservative bound for
            # this item. Never substitute its rarity or the HUD balance.
            with phase(engine,'price_ocr','compute'):
                total=total_locator.locate(image,point) if total_locator else None
                return total or (locator.locate(image,point) if locator else None)
        for node in targets:
            if node.id in standard_bounds:
                request['targets'].append(dict(node=node.id,point=list(frame.to_screen(node.x,node.y)),frames=[],standard_bound=5000))
                continue
            engine.gate();point=engine.check_held(frame,node.x,node.y)
            engine.mouse.park(point,engine.stop,lambda:engine.check_held(frame,node.x,node.y))
            frames=[]
            for i in range(4 if locator else 2):
                engine.wait(.35);engine.gate()
                before=engine.mouse.position() if hasattr(engine.mouse,'position') else point
                with phase(engine,'overlay_capture','capture'):fresh=engine.screen.capture_overlay(frame)
                after=engine.mouse.position() if hasattr(engine.mouse,'position') else point
                if math.dist(before,point)>2 or math.dist(after,point)>2:raise ScreenError('cursor_missed_node')
                dest=folder/f'node_{node.id}_{i}.png'
                with phase(engine,'proof_io','io'):write_image(dest,fresh.image)
                if locator:
                    found=locate_price(fresh.image,point)
                    # Unknown animation/scenery is no positive contradiction.
                    # Keep independent readable proofs of the same value while
                    # the guarded pointer remains on this exact target.
                    if not found:continue
                    if frames and found['value']!=frames[-1]['native_value']:frames=[]
                frames.append(dict(source=str(dest.relative_to(ROOT)),sha256=digest(dest),stamp=fresh.captured_at))
                if locator:
                    frames[-1]['native_value']=found['value']
                    native_observations[frames[-1]['source']]=dict(found=copy.deepcopy(found),stamp=fresh.captured_at,
                        sha256=frames[-1]['sha256'],point=list(point),signature=fresh.window.signature)
                if len(frames)==2:break
            if len(frames)!=2:
                engine.mouse.release();parked=engine.screen.parking_point(frame)
                if parked:engine.mouse.park(parked,engine.stop,lambda:engine.check_held(frame,node.x,node.y))
                raise ScreenError('chain_price_unproven')
            request['targets'].append(dict(node=node.id,point=list(point),frames=frames))
        engine.mouse.release();parked=engine.screen.parking_point(frame)
        if parked:engine.mouse.park(parked,engine.stop,lambda:engine.check_held(frame,result.nodes[0].x,result.nodes[0].y))
        request['status']='awaiting_visual_review'
        (folder/'request.json').write_text(json.dumps(request,ensure_ascii=False,indent=2),encoding='utf-8')
        (self.folder/'review_request.json').write_text(json.dumps(dict(folder=str(folder.relative_to(ROOT)),**request),ensure_ascii=False,indent=2),encoding='utf-8')
        engine.trace('native_price_review_required',folder=str(folder),targets=len(targets),purchase=False)
        engine.event(engine.state,'native_price_review_required',dict(folder=str(folder),targets=len(targets)))
        locator=getattr(engine,'native_price_locator',None)
        if kind in ('single','early_center') and locator:
            prices=[]
            for target in request['targets']:
                if target.get('standard_bound')==5000:
                    prices.append(dict(node=target['node'],source_sha256=request['clean_sha256'],value=5000,standard_catalog_bound=True))
                    continue
                located=[native_observations[source['source']]['found'] if kind in ('single','early_center') and source['source'] in native_observations
                    else locate_price(read_image(ROOT/source['source']),target['point']) for source in target['frames']]
                if not all(located) or located[0]['value']!=located[1]['value']:break
                prices.append(dict(node=target['node'],source_sha256=target['frames'][0]['sha256'],
                    box=located[0]['box'],value=located[0]['value'],native_currency_locator=True))
            if len(prices)==len(request['targets']):
                automatic=dict(approved=True,complete_unowned_count=sum(n.state in (NodeState.AVAILABLE,NodeState.LOCKED) for n in result.nodes),
                    clean_sha256=request['clean_sha256'],prices=prices)
                (folder/'review.json').write_text(json.dumps(automatic,indent=2),encoding='utf-8')
        until=min(time.monotonic()+getattr(engine,'native_price_review_timeout',120),engine.runtime_deadline)
        while not (folder/'review.json').is_file():
            engine.gate()
            if time.monotonic()>=until:raise ScreenError('native_price_review_timeout')
            engine.wait(.2)
        review=json.loads((folder/'review.json').read_text(encoding='utf-8'))
        if review.get('approved') is not True or review.get('complete_unowned_count')!=sum(n.state in (NodeState.AVAILABLE,NodeState.LOCKED) for n in result.nodes):
            raise ScreenError('native_price_review_rejected')
        if review.get('clean_sha256')!=request['clean_sha256']:raise ScreenError('native_price_review_rejected')
        costs=[];proofs=[]
        for target,node in zip(request['targets'],targets):
            record=next((r for r in review['prices'] if r['node']==node.id),None)
            if record and record.get('standard_catalog_bound'):
                if node.id not in standard_bounds or record.get('value')!=5000 or record.get('source_sha256')!=request['clean_sha256']:
                    raise ScreenError('native_price_review_rejected')
                costs.append(5000);proofs.append(dict(node=node.id,price_upper_bound=5000,kind='existing_standard_catalog_ceiling',source_sha256=request['clean_sha256']))
                continue
            if not record or record['source_sha256']!=target['frames'][0]['sha256']:raise ScreenError('native_price_review_rejected')
            x,y,w,h=record['box']
            if any(type(v) is not int for v in (x,y,w,h)) or min(x,y)<0 or min(w,h)<1:raise ScreenError('native_price_review_rejected')
            # Validate the reviewed box on both original independent frames.
            values=[]
            originals=[]
            for source in target['frames']:
                if digest(ROOT/source['source'])!=source['sha256']:raise ScreenError('native_price_review_rejected')
                native=native_observations.get(source['source']) if kind in ('single','early_center') else None
                if (native and native['sha256']==source['sha256'] and native['point']==target['point']
                        and native['signature']==frame.window.signature and list(native['found']['box'])==list(record['box'])):
                    values.append(native['found']['value']);originals.append(native)
                    continue
                pixels=read_image(ROOT/source['source'])
                if y+h>pixels.shape[0] or x+w>pixels.shape[1]:raise ScreenError('native_price_review_rejected')
                located=locate_price(pixels,target['point']) if record.get('native_currency_locator') and locator else None
                values.append((located['value'] if located else None) if record.get('native_currency_locator') else engine.screen.resources.decode(pixels[y:y+h,x:x+w]))
            if values!=[record['value'],record['value']] or type(record['value']) is not int or record['value']<=0:raise ScreenError('chain_price_unproven')
            proof_age=5 if kind=='early_center' else 2
            if (len(originals)==2 and originals[0]['stamp']<originals[1]['stamp']
                    and 0<=time.monotonic()-originals[0]['stamp']<proof_age):
                # These are two captures made by this invocation under one
                # continuously verified hover, not a cached price from memory
                # or a previous action. The two clean-map checks below remain.
                engine.gate();costs.append(values[0]);proofs.append(dict(node=node.id,price=values[0],
                    stamps=[o['stamp'] for o in originals],source_sha256=record['source_sha256'],box=record['box'],
                    same_hover_native_proof=True))
                engine.trace('native_price_same_hover_reused',node=node.id,observations=2,paid_inputs=0)
                continue
            # Reobserve the same native label twice now; old review is location,
            # never a current price. A full path price is a conservative upper
            # bound for this one node, explicitly not its standalone cost.
            engine.mouse.park(frame.to_screen(node.x,node.y),engine.stop,lambda:engine.check_held(frame,node.x,node.y))
            def decode(image):
                if record.get('native_currency_locator'):
                    found=locate_price(image,target['point']) if locator else None
                    return found['value'] if found else locator.reobserve_box(image,record['box']) if locator else None
                return engine.screen.resources.decode(image[y:y+h,x:x+w])
            values,stamps=reobserve_price(engine,frame,decode,record['value'],folder,node.id)
            costs.append(values[0]);proofs.append(dict(node=node.id,price=values[0],stamps=stamps,source_sha256=record['source_sha256'],box=record['box']))
        if parked:engine.mouse.park(parked,engine.stop,lambda:engine.check_held(frame,result.nodes[0].x,result.nodes[0].y))
        single_scope=kind=='single' and getattr(engine,'native_single_price_scope',False)
        first,second=wait_price_scene(engine,request['scene'],original_points[0],single_scope,kind=='early_center')
        self.permit=dict(kind=kind,key=scene_key(second[0],second[1]) if single_scope or kind=='early_center' else request['scene'],points=[list(frame.to_screen(n.x,n.y)) for n in targets],
            price=sum(costs),proofs=proofs,created=time.monotonic())
        if single_scope:
            from scripts.native_price_v55 import prepared_single_key
            self.permit['key']=prepared_single_key(second[0],second[1],request['scene'])
        return second


def install_limits(engine,authorization,path,deadline,folder):
    if engine.settings.purchase_mode=='repeat':raise ScreenError('repeat_scope_required')
    budget=OperationBudget(path.with_suffix('.resources.json'),authorization,deadline);review=NativeReview(engine,folder)
    engine.settings.max_input_commands=authorization['authorized_limit'];engine.settings.auto_prestige=False;engine.settings.fast_early_levels=True
    original_buy=engine.buy;original_mouse=engine.mouse.purchase
    def scope():
        if engine.settings.character!='animatronic':raise ScreenError('chain_scope_changed')
    def automatic(frame,result,plan):
        scope()
        targets=[n for n in result.nodes if n.state in (NodeState.AVAILABLE,NodeState.LOCKED)]
        return review.prepare(frame,result,plan,targets,'early_center')
    def buy(frame,result,node=None,point=None,**kwargs):
        scope()
        if point is not None or node is None:raise ScreenError('prestige_manual')
        from scripts.live_test_v53 import standard_target
        try:standard_target(engine,node);price=5000
        except ScreenError:
            frame,result,_=review.prepare(frame,result,None,[node],'single');node=next(n for n in result.nodes if n.id==node.id);price=review.permit['price']
        if review.permit is None or review.permit['kind']!='single':
            review.permit=dict(kind='single',key=scene_key(frame,result),points=[list(frame.to_screen(node.x,node.y))],price=price,proofs=[],created=time.monotonic())
        budget.check(frame.bloodpoints,1,price)
        return original_buy(frame,result,node=node,**kwargs)
    def mouse(point,duration,stop,check):
        scope();frame,result,_=engine.recent_frames[-1];permit=review.permit
        if not permit or time.monotonic()-permit['created']>5 or permit['key']!=scene_key(frame,result):raise ScreenError('chain_map_changed')
        if permit['kind']=='early_center':
            if not 1<=result.level<=9 or not engine.scene_record['pending'].get('automatic'):raise ScreenError('automatic_unlock_unproven')
            expected=next(n for n in result.nodes if n.state==NodeState.CENTER)
            if math.dist(point,frame.to_screen(expected.x,expected.y))>5:raise ScreenError('unsafe_node')
        elif list(point)!=permit['points'][0]:raise ScreenError('unsafe_node')
        check();budget.reserve(frame.bloodpoints,permit['points'],permit['price'],permit['kind'],permit['proofs'])
        engine.trace('operation_reserved',**{k:budget.data[k] for k in ('commands','nodes','bp')},kind=permit['kind'])
        review.permit=None
        return original_mouse(point,duration,stop,check)
    engine.prepare_automatic=automatic;engine.buy=buy;engine.mouse.purchase=mouse;engine.trial_resources=budget


if __name__=='__main__':
    import traceback
    def error(kind,value,tb):(ROOT/'debug/v54/launcher_error.txt').write_text(''.join(traceback.format_exception(kind,value,tb)),encoding='utf-8')
    sys.excepthook=error
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('authorization',type=Path)
    parser.add_argument('--replace-session',type=Path)
    args=parser.parse_args()
    if args.replace_session:
        import ctypes
        from ctypes import wintypes as W
        session=args.replace_session.resolve()
        if not session.is_relative_to((ROOT/'debug/v54').resolve()):raise ValueError('invalid_replacement_scope')
        current=json.loads(session.read_text(encoding='utf-8'))
        if current['authorization']!=json.loads(args.authorization.read_text(encoding='utf-8')) or current['input_calls']!=0 or current['status'] not in ('paused','stopped','error'):
            raise ValueError('replacement_not_idle')
        if any(e['attempts'] or e['pending'] for e in current['engines']):raise ValueError('replacement_input_unresolved')
        u=ctypes.WinDLL('user32',use_last_error=True)
        u.IsWindow.argtypes=[W.HWND];u.GetWindowThreadProcessId.argtypes=[W.HWND,ctypes.POINTER(W.DWORD)]
        u.PostMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM]
        pid=W.DWORD();u.GetWindowThreadProcessId(current['hwnd'],ctypes.byref(pid))
        if pid.value!=current['pid']:raise ValueError('replacement_window_changed')
        if not u.PostMessageW(current['hwnd'],0x10,0,0):raise OSError(ctypes.get_last_error(),'close_owned_test')
        until=time.monotonic()+10
        while u.IsWindow(current['hwnd']) and time.monotonic()<until:time.sleep(.1)
        if u.IsWindow(current['hwnd']):raise ValueError('old_test_still_running')
        from scripts.live_test_v48 import acquire_scope
        while True:
            try:probe_lock=acquire_scope(args.authorization);probe_lock.close();break
            except ValueError:
                if time.monotonic()>=until:raise
                time.sleep(.1)
    from scripts.live_test_v48 import main
    raise SystemExit(main(args.authorization))
