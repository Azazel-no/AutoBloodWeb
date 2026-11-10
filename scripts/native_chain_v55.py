"""Existing native chain probe/executor, under an explicit bounded permit."""
import hashlib,json,time
from dataclasses import replace
from pathlib import Path
from src.core.chain_plan import propose_chain,read_preview,PriceRegion,native_candidate_route
from src.core.path_planner import plan_route,RoutePlan
from src.core.persistent_route import map_key
from src.core.preview_capture import capture_preview
from src.core.models import NodeState
from src.core.screen_manager import ScreenError
from src.utils.images import write_image
from src.core.performance import measured,phase
from src.core.click_lifecycle import probe as click_probe
from src.core.runtime_paths import project_root


@measured('native_chain_preparation')
def probe_if_requested(engine,folder,frame,result):
    request=folder/'chain_probe_requested.json'
    if not request.is_file():return
    options=json.loads(request.read_text(encoding='utf-8'))
    max_items=options.get('max_chain_items',3)
    if type(max_items) is not int or not 2<=max_items<=5:raise ScreenError('invalid_chain_caps')
    route=native_candidate_route(result,engine.priorities,engine.settings.priority_mode,max_nodes=max_items)
    if options.get('terminal_point'):
        import math
        candidates=[n for n in result.nodes if math.dist(frame.to_screen(n.x,n.y),options['terminal_point'])<=5]
        if len(candidates)!=1:return
        terminal=candidates[0];owned={n.id for n in result.nodes if n.state in (NodeState.CENTER,NodeState.PURCHASED)}
        graph={n.id:[] for n in result.nodes};nodes={n.id:n for n in result.nodes}
        for a,b in result.edges:graph[a].append(b);graph[b].append(a)
        pending=[[i] for i in owned];seen=set(owned);path=None
        while pending:
            current=pending.pop(0)
            if current[-1]==terminal.id:path=current[1:];break
            for other in graph[current[-1]]:
                if other not in seen and nodes[other].state in (NodeState.AVAILABLE,NodeState.LOCKED):
                    seen.add(other);pending.append(current+[other])
        if not path:return
        route=RoutePlan(path[0],path,reason='native_chain_trial_same_priority')
    generation=getattr(engine.detector.memory,'generation',0)
    proposal=propose_chain(frame,result,route,engine.priorities,map_key(frame,result,generation),max_nodes=max_items)
    if proposal is None or proposal.expected_items<2:return
    substantive=[b for b in proposal.blockers if b not in ('native_preview_required','price_unknown')]
    if substantive:
        engine.trace('chain_probe_skipped',blockers=substantive,route=route.route)
        return
    target=next(n for n in result.nodes if n.id==proposal.terminal)
    if target.geometry_confidence<.65:return
    request.unlink()
    destination=folder/f'chain_probe_{len(list(folder.glob("chain_probe_[0-9]*")))+1:03d}'
    destination.mkdir();previews=[];observed=[]
    point=engine.check(frame,target.x,target.y)
    def guard():return engine.check_held(frame,target.x,target.y)
    with click_probe(engine,'CHAIN_PREVIEW_PROBE',point):
        try:
            engine.mouse.release();engine.mouse.park(point,engine.stop,guard)
            # Probe pixels do not count as purchased nodes.
            for delay in (0,.02,.2,.2):
                engine.wait(delay);engine.gate();guard()
                before_cursor=engine.mouse.position()
                with phase(engine,'preview_capture','capture'):preview=capture_preview(engine.screen,frame)
                after_cursor=engine.mouse.position()
                previews.append(replace(preview,visibility=dict(cursor_before=list(before_cursor),cursor_after=list(after_cursor))))
        finally:
            engine.mouse.release()
            parked=engine.screen.parking_point(frame)
            if parked:engine.mouse.park(parked,engine.stop,guard)
    def saved(path,pixels):
        write_image(path,pixels)
        return dict(source=str(path.relative_to(project_root())),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    locator=getattr(engine,'native_price_locator',None)
    total_locator=None
    calibration=folder/'native_total_price_locator.json'
    if calibration.is_file() and locator:
        from scripts.native_price_v55 import NativeTotalPriceLocator
        total_locator=NativeTotalPriceLocator(project_root(),json.loads(calibration.read_text()),locator)
    valid=[]
    for index,preview in enumerate(previews):
        with phase(engine,'price_ocr','compute'):total=total_locator.locate(preview.raw_image,point) if total_locator else None
        region=PriceRegion(tuple(total['box']),total['provenance'],True,'client') if total else None
        with phase(engine,'native_preview_parse','compute'):
            parsed=read_preview(preview,result,target.id,proposal.map_id,engine.screen.resources,region,clean_frame=frame)
        if parsed.reason=='observed_preview':valid.append(parsed)
        observed.append(dict(frame=preview.metadata(),
                             preview=vars(parsed),native_price=None,
                             standalone_price_not_requested=True))
    proposal.bind_preview(valid[-2:])
    data=dict(kind='native_path_probe',paid_inputs=0,clean_frame=frame.metadata(),
              clean_analysis=result.to_dict(),proposal=proposal.summary(),observations=observed)
    @measured('proof_io','io')
    def persist(owner,outcome=None):
        import copy
        writer=getattr(owner,'evidence_writer',None)
        snapshot=copy.deepcopy(data)
        # Read-only captures can be retained. Mutable probe backends receive a
        # snapshot before the asynchronous writer is allowed to see them.
        images=[frame.raw_image,*[p.raw_image for p in previews]]
        images=[p.copy() if writer and p.flags.writeable else p for p in images]
        def write():
            snapshot['clean']=saved(destination/'clean.png',images[0])
            for i,pixels in enumerate(images[1:]):snapshot['observations'][i].update(saved(destination/f'preview_{i}.png',pixels))
            (destination/'probe.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
            (folder/'chain_probe_status.json').write_text(json.dumps(dict(folder=str(destination),proposal=snapshot['proposal'],
                observations=[o['preview'] for o in snapshot['observations']],paid_inputs=snapshot['paid_inputs']),indent=2),encoding='utf-8')
            if outcome is not None:(destination/'outcome.json').write_text(json.dumps(outcome,indent=2),encoding='utf-8')
        if writer:
            with phase(owner,'proof_queue_wait','wait'):writer.submit(str(destination),write)
            owner.trace('proof_enqueued',folder=str(destination),paid_inputs=0)
        else:write()
    engine.trace('chain_native_probe_complete',folder=str(destination),paid_inputs=0)
    if options.get('execute'):
        outcome=None
        before_sequence=getattr(engine.mouse,'down_attempt_sequence',0)
        command_boundary=engine.report.entries[-1]['time'] if engine.report.entries else ''
        try:
            if proposal.blockers:raise ScreenError('chain_preview_or_price_unproven',str(proposal.blockers))
            first=engine.recover_read(retry_stale=True);second=engine.recover_read(retry_stale=True)
            from src.core.chain_trial import ChainPermit
            permit=ChainPermit(options['scope_id'],folder/options['ledger'],options.get('max_commands',1),options['max_nodes'],options['max_bp'],
                options['expires_at'],frame.window.signature,tuple(engine.priorities),
                options.get('approved') is True,options.get('acknowledge_queue_cancellation_unverified') is True,max_chain_items=max_items)
            outcome=engine.execute_native_chain(proposal,permit,first[:2],second[:2])
        finally:
            # Encoding original proof PNGs must not postpone the first clean
            # post-input observation or stale an otherwise valid preview.
            attempts=getattr(engine.mouse,'down_attempt_sequence',0)-before_sequence
            sends=[e for e in engine.report.entries if e['time']>command_boundary and e['stage']=='SendInput' and e.get('flag')==2]
            data['paid_inputs']=sum(e.get('result')==1 for e in sends) if sends else None if attempts else 0
            persist(engine,outcome)
        engine.trace('native_chain_trial_finished',**outcome)
        if options.get('continuous'):
            if engine.pending_action:raise ScreenError('purchase_result_unknown','chain_partial_unresolved')
            return engine.recent_frames[-1]
        raise ScreenError('chain_trial_complete')
    persist(engine)
    raise ScreenError('chain_native_preview_review')
