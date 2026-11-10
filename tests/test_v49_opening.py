"""Fixed real pixels exercise compact vision; injected cycles are not live purchases."""
import copy
import time
from dataclasses import replace
from types import SimpleNamespace
import pytest
from src.core.fast_opening import FastOpening, OpeningFallback
from src.core.bloodweb_detector import BloodwebDetector
from src.core.screen_manager import CapturedFrame, Rect, WindowInfo
from src.core.models import NodeState, ScreenState
from src.utils.images import read_image


def opening_scene(root,library):
    pixels=read_image(root/'references/bloodweb/v48_4585d2d0/raw_0.png')
    h,w=pixels.shape[:2]
    window=WindowInfo(1,'injected fixed pixels',Rect(0,0,w,h),Rect(0,0,w,h),96,True,1)
    frame=CapturedFrame(pixels,window,Rect(0,0,w,h),time.monotonic(),level=1,level_confidence=1)
    detector=BloodwebDetector(library,'killer','singularity')
    result=detector.analyze_live(frame)
    opening=FastOpening()
    assert opening.begin(frame,result,detector,[], 'strict',0)
    return opening,frame,result,detector


def test_real_pixels_local_check_never_searches_circles_library_or_replans(root,library,monkeypatch):
    opening,frame,result,detector=opening_scene(root,library)
    def forbidden(*args,**kwargs):raise AssertionError('full work in compact path')
    monkeypatch.setattr(detector,'analyze_live',forbidden)
    monkeypatch.setattr(detector.matcher,'match',forbidden)
    monkeypatch.setattr('cv2.HoughCircles',forbidden)
    monkeypatch.setattr('src.core.fast_opening.plan_route',forbidden)
    fresh=replace(frame,captured_at=frame.captured_at+.3)
    compact=opening.analyze(fresh,detector,[],'strict',0)
    plan=opening.plan(compact,0,())
    assert compact.details['analysis_mode']=='FAST_OPENING'
    assert compact.edges==result.edges
    assert [(n.id,n.state,n.icon_id) for n in compact.nodes]==[(n.id,n.state,n.icon_id) for n in result.nodes]
    assert plan.next_node==opening.queue[0].next_node
    assert all(r['capture_stamp']==fresh.captured_at for r in detector.memory.records)
    assert opening.quick_checks==1 and opening.identity_reuses==4


def test_real_level49_uncertain_category_requires_fresh_id_proof_and_fire_is_not_an_edge(root,library,monkeypatch):
    import json
    from scripts.inspect_opening_failure_v49 import stored_frame
    folder=root/'debug/v49/live_session_bdf45c36'
    records={r['analysis_id']:r for r in json.loads((folder/'frames.json').read_text(encoding='utf-8'))}
    ids=['1791995966526131800_8a4c2a78','1791995968026885100_ddb2e932','1791995969246015200_3cb0d543']
    frames=[stored_frame(records[i]) for i in ids]
    priorities=json.loads((folder/'session.json').read_text(encoding='utf-8'))['authorization']['priorities']
    detector=BloodwebDetector(library,'killer','unknown')
    result=detector.analyze_live(frames[0]);opening=FastOpening()
    assert opening.begin(frames[0],result,detector,priorities,'smart',0)
    initial=next(n for n in result.nodes if n.id==11)
    assert initial.category_hint=='offering' and initial.icon_id=='killer_offering_040'
    def forbidden(*args,**kwargs):raise AssertionError('full work in category uncertainty check')
    monkeypatch.setattr(detector,'analyze_live',forbidden)
    monkeypatch.setattr(detector.matcher,'match',forbidden)
    monkeypatch.setattr('cv2.HoughCircles',forbidden)
    monkeypatch.setattr('src.core.fast_opening.plan_route',forbidden)
    compact=opening.analyze(frames[1],detector,priorities,'smart',0)
    node=next(n for n in compact.nodes if n.id==11)
    proof=next(c for c in compact.details['local_certificates'] if c['node']==11)
    assert node.category_hint=='unknown' and node.icon_id==initial.icon_id
    assert proof['fresh_category_uncertain'] and proof['local_best']>=.86
    assert proof['global_margin_lower']>=.035 and proof['contour']>=.68
    assert (20,21) not in result.edges  # No line between these nodes in raw pixels.
    third=opening.analyze(frames[2],detector,priorities,'smart',0)
    assert third.edges==compact.edges and opening.quick_checks==2
    # A confident incompatible category must still discard the cached queue.
    original=FastOpening.parts
    def contradiction(frame,node,detector):
        feature,key,category,patch=original(frame,node,detector)
        if node.id==11:
            category.category='perk';category.candidates=['perk'];category.confidence=.95
            key=key[:2]+('perk',('perk',),True,False)
        return feature,key,category,patch
    monkeypatch.setattr(FastOpening,'parts',staticmethod(contradiction))
    with pytest.raises(OpeningFallback,match='glyph_category_or_rarity_changed'):
        opening.analyze(replace(frames[1],captured_at=frames[2].captured_at+.3),detector,priorities,'smart',0)


@pytest.mark.parametrize('change,reason',[
    ('stamp','fresh_header_required'),('level','window_roi_level_or_role_changed'),
    ('roi','window_roi_level_or_role_changed'),('window','window_roi_level_or_role_changed'),
    ('role','window_roi_level_or_role_changed'),('priority','priorities_changed'),
    ('header','fresh_header_required'),('invalidate','detector_invalidated'),
    ('glyph','glyph_category_or_rarity_changed'),('edge','connection_changed'),
    ('end','opening_complete')])
def test_compact_contradictions_abort_without_input(root,library,monkeypatch,change,reason):
    opening,frame,result,detector=opening_scene(root,library)
    fresh=replace(frame,captured_at=frame.captured_at+.3)
    priorities=[];actions=0
    if change=='stamp':fresh.captured_at=frame.captured_at
    if change=='level':fresh.level=2
    if change=='roi':fresh.roi=replace(frame.roi,x=1)
    if change=='window':fresh.window=replace(frame.window,pid=2)
    if change=='role':detector.role='survivor'
    if change=='priority':priorities=['any_perk']
    if change=='header':fresh.level_confidence=.9
    if change=='invalidate':detector.invalidate()
    if change=='glyph':
        import numpy as np
        original=opening.parts
        def contradictory(*args):
            feature,key,category,patch=original(*args)
            return np.zeros_like(feature),key,category,patch
        monkeypatch.setattr(FastOpening,'parts',staticmethod(contradictory))
    if change=='edge':monkeypatch.setattr(detector,'_edges',lambda *a,**k:[])
    if change=='end':actions=len(opening.queue)
    with pytest.raises(OpeningFallback,match=reason):opening.analyze(fresh,detector,priorities,'strict',actions)
    opening.cancel(reason)
    assert not opening.active and opening.aborts=={reason:1}
    if change=='role':detector.role='killer'
    assert not opening.begin(frame,result,detector,[],'strict',0)


def test_no_opening_on_partial_entity_or_uncertain_scene(root,library):
    opening,frame,result,detector=opening_scene(root,library)
    for state in (NodeState.PURCHASED,NodeState.ABSORBED,NodeState.UNCERTAIN):
        web=copy.deepcopy(result);web.nodes[1].state=state
        assert not FastOpening().begin(frame,web,detector,[],'strict',0)
    assert not FastOpening().begin(replace(frame,probe_only=True),result,detector,[],'strict',0)


def test_native_center_remains_first_and_fast_opening_setting_validates(root,library):
    from src.core.automation import Automation,Settings
    from tests.test_automation import Device
    _,frame,result,detector=opening_scene(root,library)
    device=Device([result]);device.capture=lambda:replace(frame,captured_at=time.monotonic())
    device.capture_overlay=lambda frame:frame
    engine=Automation(device,detector,device,[],Settings(dry_run=False,startup_delay=0),approved=True,hotkeys_ready=True)
    current,observed,plan=engine.read()
    from src.core.early_unlock import center_mode,eligible
    # The compact map is now also used to observe a central automatic unlock.
    # Its existence must never switch the early-level policy to single clicks.
    assert engine.opening.active and engine.opening.persistent
    assert center_mode(engine,observed) and eligible(engine,observed,current)
    assert not device.presses
    with pytest.raises(ValueError,match='fast_opening'):Settings(fast_opening=1).validate()


def test_five_injected_purchases_use_one_full_scan_and_two_red_frames_each(monkeypatch):
    from src.core.automation import Automation,Settings,State
    from src.core.models import Analysis,Node,NodeType
    from tests.test_automation import Device
    center=Node(0,100,200,20,NodeState.CENTER,NodeType.CENTER,state_confidence=.95)
    nodes=[center]+[Node(i,200+(i%2)*80,50+i*55,20,NodeState.AVAILABLE,NodeType.ADDON,
                    state_confidence=.95,geometry_confidence=.95) for i in range(1,7)]
    web=Analysis(400,400,ScreenState.BLOODWEB,nodes,[(0,i) for i in range(1,7)],level=20,
                 level_confidence=1,details={'center':[100,200,20]})
    device=Device([web]);device.role='killer';device.character='unknown'
    device.memory=SimpleNamespace(generation=1)
    original_capture=device.capture
    def capture():
        frame=original_capture();frame.level=20;frame.level_confidence=1
        return frame
    device.capture=capture
    original_analysis=device.analyze;full=[]
    def analyze(image,source):
        full.append(1);return original_analysis(image,source)
    device.analyze=analyze
    engine=Automation(device,device,device,[],Settings(dry_run=False,startup_delay=0,
        fast_early_levels=False,max_actions=5,poll_interval=.01,purchase_timeout=1),approved=True,hotkeys_ready=True)
    monkeypatch.setattr(engine.opening,'evidence',lambda *args:b'injected evidence')
    monkeypatch.setattr(engine.opening,'parts',lambda *args:(None,(),None,None))
    def compact(frame,detector,priorities,mode,actions):
        assert tuple(priorities)==engine.opening.priorities
        result=copy.deepcopy(web)
        for n in result.nodes[1:]:
            if n.id<=len(device.presses):n.state=NodeState.PURCHASED
        engine.opening.quick_checks+=1
        return result
    monkeypatch.setattr(engine.opening,'analyze',compact)
    engine.run()
    assert engine.state==State.PAUSED and engine.actions==5 and len(device.presses)==5
    assert len(full)==engine.opening.full_scans==1
    assert engine.opening.quick_checks==15  # startup1 + confirmation10 + next checks4
    assert not engine.pending_action


def test_omitted_score_bound_covers_actual_fresh_template_scores():
    import numpy as np
    from src.core.fast_opening import outside_score_bound
    rng=np.random.default_rng(49)
    for scale in (.001,.01,.1,1.):
        for _ in range(16):
            old=rng.random(256).astype(np.float32)
            fresh=np.clip(old+rng.normal(0,scale,256),0,1).astype(np.float32)
            templates=rng.random((64,256)).astype(np.float32)
            norms=np.sum(templates*templates,axis=1)
            old_norm=float(old@old);new_norm=float(fresh@fresh)
            scores=2*(templates@old)/(norms+old_norm+1e-6)
            actual=2*(templates@fresh)/(norms+new_norm+1e-6)
            delta=fresh-old
            upper=outside_score_bound(norms,old_norm,new_norm,float(np.linalg.norm(delta)),
                scores+1e-5,float(old@delta))
            assert upper>=float(actual.max()), (scale,upper,float(actual.max()))
    assert outside_score_bound([1.],0.,1.,1.,[0.],0.)==1.
    assert outside_score_bound([float('nan')],1.,1.,0.,[0.],0.)==1.


def test_actual_opening_pixels_local_checks_and_safe_fallback(root,library,monkeypatch):
    import json
    from scripts.inspect_opening_failure_v49 import stored_frame
    from src.core.models import NodeType
    fixture=json.loads((root/'tests/fixtures/v49/opening.json').read_text(encoding='utf-8'))
    initial=stored_frame(fixture['initial'])
    detector=BloodwebDetector(library,'killer','unknown')
    result=detector.analyze_live(initial)
    opening=FastOpening()
    session=json.loads((root/'debug/v49/live_session_260641ab/session.json').read_text(encoding='utf-8'))
    priorities=session['authorization']['priorities']
    assert opening.begin(initial,result,detector,priorities,'smart',0)
    matcher=detector.matcher.match
    def forbidden(*args,**kwargs):raise AssertionError('full work in compact path')
    monkeypatch.setattr(detector.matcher,'match',forbidden)
    monkeypatch.setattr(detector,'analyze_live',forbidden)
    monkeypatch.setattr('cv2.HoughCircles',forbidden)
    monkeypatch.setattr('src.core.fast_opening.plan_route',forbidden)
    reds=0;actions=0
    for item in fixture['frames']:
        fresh=stored_frame(item)
        if item['analysis_id']==fixture['output'][-1]['analysis_id']:
            with pytest.raises(OpeningFallback,match='glyph_category_or_rarity_changed'):
                opening.analyze(fresh,detector,priorities,'smart',actions)
            break
        compact=opening.analyze(fresh,detector,priorities,'smart',actions)
        for node in compact.nodes:
            if node.state==NodeState.CENTER or node.type==NodeType.PERK:continue
            feature,key,category,patch=opening.parts(fresh,node,detector)
            ordinary=matcher(patch,node.content_radius or node.radius,prepared_feature=feature)
            if node.icon_id:assert node.icon_id==ordinary.icon_id
            elif node.state!=NodeState.PURCHASED:assert ordinary.icon_id is None
            original=next(n for n in result.nodes if n.id==node.id)
            if not original.icon_id:assert node.icon_id is None
        target=next(n for n in compact.nodes if n.id==opening.queue[actions].next_node)
        if target.state==NodeState.PURCHASED:
            reds+=1
            if reds==2:actions+=1;reds=0
        else:reds=0
    assert opening.quick_checks==len(fixture['frames'])-1 and actions==fixture['confirmed_by_replay']
    assert opening.last_checked_node==fixture['output'][-1]['checked_node']


def test_one_actual_live_purchase_is_compact_after_locked_background_fix(root,library,monkeypatch):
    import json
    from scripts.inspect_opening_failure_v49 import stored_frame
    from src.core.models import NodeType
    folder=root/'debug/v49/live_session_b0c39801'
    records=json.loads((folder/'frames.json').read_text(encoding='utf-8'));by_id={r['analysis_id']:r for r in records}
    session=json.loads((folder/'session.json').read_text(encoding='utf-8'))
    priorities=session['authorization']['priorities']
    initial=stored_frame(by_id['1791990044292251700_fb36ed11'])
    detector=BloodwebDetector(library,'killer','unknown');result=detector.analyze_live(initial)
    opening=FastOpening();assert opening.begin(initial,result,detector,priorities,'smart',0)
    assert opening.queue[0].next_node==2
    def forbidden(*a,**k):raise AssertionError('full work after the first analysis')
    monkeypatch.setattr(detector.matcher,'match',forbidden)
    monkeypatch.setattr(detector,'analyze_live',forbidden)
    monkeypatch.setattr('cv2.HoughCircles',forbidden)
    monkeypatch.setattr('src.core.fast_opening.plan_route',forbidden)
    states=[]
    for ident in ['1791990044684610100_2d8f042f','1791990046752724200_c52677f9','1791990047703085800_31f3b530']:
        compact=opening.analyze(stored_frame(by_id[ident]),detector,priorities,'smart',0)
        states.append(next(n for n in compact.nodes if n.id==2).state)
        anonymous=next(n for n in compact.nodes if n.id==9)
        assert anonymous.icon_id is None and anonymous.type==NodeType.UNKNOWN
        if ident!='1791990044684610100_2d8f042f':
            assert anonymous.state==NodeState.AVAILABLE and anonymous.radius==40
        assert all(n.icon_id is None for n in compact.nodes if n.type==NodeType.PERK)
    assert states==[NodeState.AVAILABLE,NodeState.PURCHASED,NodeState.PURCHASED]
    assert opening.quick_checks==3


def test_fresh_level48_gold_ring_is_not_absorbed(root,library):
    import json
    from scripts.inspect_opening_failure_v49 import stored_frame
    report=json.loads((root/'debug/v49/readonly_2cf6e391/report.json').read_text(encoding='utf-8'))
    detector=BloodwebDetector(library,'killer','unknown')
    for observation in report['observations']:
        frame=stored_frame(observation)
        result=detector.analyze_live(frame)
        node=next(n for n in result.nodes if (n.x,n.y)==(667,365))
        assert node.state==NodeState.AVAILABLE and node.radius==40
        assert result.details['gold_radius_corrections']==[dict(x=667,y=365,before=29,after=40)]
        assert not any(n.state==NodeState.ABSORBED for n in result.nodes)


def test_level48_initial_perk_crop_checks_fresh_shape_without_assigning_id(root,library,monkeypatch):
    import json
    from scripts.inspect_opening_failure_v49 import stored_frame
    from src.core.models import NodeType
    report=json.loads((root/'debug/v49/readonly_2cf6e391/report.json').read_text(encoding='utf-8'))
    frames=[stored_frame(o) for o in report['observations']]
    detector=BloodwebDetector(library,'killer','unknown');result=detector.analyze_live(frames[0])
    opening=FastOpening()
    assert opening.begin(frames[0],result,detector,[],'smart',0)
    node=next(n for n in opening.nodes if (n.x,n.y)==(248,732))
    assert node.type==NodeType.PERK and node.content_radius==36 and node.icon_id is None
    def forbidden(*args,**kwargs):raise AssertionError('full work during compact check')
    monkeypatch.setattr(detector.matcher,'match',forbidden)
    monkeypatch.setattr('cv2.HoughCircles',forbidden)
    compact=opening.analyze(frames[1],detector,[],'smart',0)
    assert compact.details['analysis_mode']=='FAST_OPENING'
    assert all(n.icon_id is None for n in compact.nodes if n.type==NodeType.PERK)
