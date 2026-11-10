"""Original failing chain plus two actual read-only captures; no game input."""
import copy,json,hashlib,time
from dataclasses import replace
import pytest
from src.core.models import Analysis,Node,NodeState,NodeType,ScreenState
from src.core.screen_manager import CapturedFrame,WindowInfo,Rect,ScreenError
from src.core.chain_plan import ChainEvidence,ChainPlan
from src.core.scene_memory import chain_ring_certificate,chain_state_signature
from src.core.action_outcomes import ActionOutcomes
from src.utils.images import read_image
from tests.test_automation import Device,engine,web


@pytest.fixture(scope='module')
def native(root):
    meta=json.loads((root/'tests/fixtures/rc1/executioner_ring.json').read_text(encoding='utf-8'))
    def load(path,sha):
        path=root/path;assert hashlib.sha256(path.read_bytes()).hexdigest()==sha
        return path
    probe=json.loads(load(meta['probe'],meta['probe_sha256']).read_text(encoding='utf-8'))
    def frame(pixels,m):
        w=m['window'];window=WindowInfo(w['hwnd'],w['title'],Rect(**w['client']),Rect(**w['monitor']),w['dpi'],w['foreground'],w['pid'])
        roi=Rect(**m['roi']);return CapturedFrame(pixels[roi.y:roi.y+roi.height,roi.x:roi.x+roi.width],window,roi,m['captured_at'],
            bloodpoints=m['bloodpoints'],raw_image=pixels,level=m['level'],level_confidence=m['level_confidence'])
    def analysis(data):
        nodes=[Node(n['id'],n['x'],n['y'],n['radius'],NodeState(n['state']),NodeType(n['type']),n.get('icon_id'),
            state_confidence=n['state_confidence'],geometry_confidence=n['geometry_confidence']) for n in data['nodes']]
        return Analysis(845,845,ScreenState.BLOODWEB,nodes,[],level=data['level'],level_confidence=data['level_confidence'])
    before=(frame(read_image(load(meta['before_source'],meta['before_sha256'])),probe['clean_frame']),analysis(probe['clean_analysis']))
    after=[(frame(read_image(load(row['source'],row['sha256'])),row['frame']),analysis(row)) for row in meta['after']]
    return meta,before,after


def evidence(native):
    meta,(f,r),after=native;chain=meta['scene']['pending']['chain']
    plan=ChainPlan('native',1,9,14,[9,14],physical_points=chain['physical_points'])
    return ChainEvidence(f,r,plan)


def test_original_fourteen_pixel_shift_has_two_real_positive_ring_observations(native):
    meta,before,after=native;e=evidence(native)
    assert not e.observe(*after[0]);assert e.observe(*after[1])
    assert e.summary()['confirmed']==[9,14] and not e.summary()['unexpected_purchases']
    assert e.summary()['red_observations']=={9:2,14:2}
    assert after[1][1].nodes[14].icon_id is None


@pytest.mark.parametrize('bad',['identity','size','shift','overlap','state_score','geometry','absorbed'])
def test_only_a_unique_contained_high_confidence_shrinking_red_ring_can_match(native,bad):
    meta,_,after=native;scene=copy.deepcopy(meta['scene']);f,r=after[-1]
    node=copy.deepcopy(next(n for n in r.nodes if n.id==14));point=scene['pending']['chain']['physical_points'][-1]
    if bad=='identity':
        next(n for n in scene['nodes'] if n['point']==point)['icon']='previous';node.icon_id='different'
    if bad=='size':node.radius=40
    if bad=='shift':node.x+=15
    if bad=='overlap':scene['nodes'].append(dict(point=[900,867],radius=40,state='locked',icon=None))
    if bad=='state_score':node.state_confidence=.89
    if bad=='geometry':node.geometry_confidence=.79
    if bad=='absorbed':node.state=NodeState.ABSORBED
    assert chain_ring_certificate(scene,f,node,point) is None


def test_real_extra_purchase_still_blocks_the_chain(native):
    e=evidence(native);f,r=native[2][0];r=copy.deepcopy(r)
    r.nodes.append(replace(r.nodes[14],id=99,x=10,y=10))
    with pytest.raises(ValueError,match='chain_unexpected_purchase'):e.observe(f,r)


def test_saved_signature_and_canonical_journal_share_the_same_geometry(native,tmp_path):
    meta,_,after=native;scene=copy.deepcopy(meta['scene']);scene['pending'].pop('chain_blocked_reason')
    assert chain_state_signature(scene,*after[0])==('purchased','purchased')
    row=ActionOutcomes(tmp_path/'journal.json').reconcile(scene,after,'native_fixture')
    assert row['confirmed_nodes']==2 and row['bp_delta']==7250
    assert not row['authorizes_input'] and not row['reservation_refunded']
    assert row['contained_ring_observations']


@pytest.mark.parametrize('bad',[None,'bp','window','extra','other_block'])
def test_block_review_requires_two_fresh_complete_observations_and_never_presses(native,tmp_path,monkeypatch,bad):
    meta,_,after=native;d=Device([web()]);e=engine(d,dry_run=False)
    e.persisted_scene=copy.deepcopy(meta['scene']);e.scene_record=e.persisted_scene
    e.outcome_journal=ActionOutcomes(tmp_path/'journal.json');frames=[(f,copy.deepcopy(r)) for f,r in after]
    if bad=='bp':frames=[(replace(f,bloodpoints=f.bloodpoints-1),r) for f,r in frames]
    if bad=='window':frames=[(replace(f,window=replace(f.window,dpi=192)),r) for f,r in frames]
    if bad=='extra':
        for f,r in frames:r.nodes.append(replace(r.nodes[14],id=99,x=10,y=10))
    if bad=='other_block':e.persisted_scene['pending']['chain_blocked_reason']='chain_bp_exceeded'
    clock=[frames[0][0].captured_at+.01];monkeypatch.setattr(time,'monotonic',lambda:clock[0])
    if bad:
        with pytest.raises(ScreenError):e.recheck_saved_scene(*frames[0])
        assert e.persisted_scene['pending']['chain_blocked_reason']
    else:
        assert not e.recheck_saved_scene(*frames[0])
        assert e.persisted_scene['pending']['chain_blocked_reason']=='chain_unexpected_purchase'
        clock[0]=frames[1][0].captured_at+.01
        assert e.recheck_saved_scene(*frames[1])
        assert e.scene_record['pending'] is None and e.persisted_scene is None
        assert e.scene_record['last_outcome']['chain_geometry_review']['original_reason']=='chain_unexpected_purchase'
        assert e.actions==0
    assert not d.presses


def test_cold_scan_stale_frame_is_discarded_without_clearing_the_block(native,tmp_path,monkeypatch):
    meta,_,after=native;d=Device([web()]);e=engine(d,dry_run=False)
    e.persisted_scene=copy.deepcopy(meta['scene']);e.outcome_journal=ActionOutcomes(tmp_path/'journal.json')
    f,r=after[0];monkeypatch.setattr(time,'monotonic',lambda:f.captured_at+2.1)
    assert not e.recheck_saved_scene(f,r)
    assert not e.recheck_saved_scene(f,r)
    with pytest.raises(ScreenError,match='stale_frame'):e.recheck_saved_scene(f,r)
    assert e.persisted_scene['pending']['chain_blocked_reason']=='chain_unexpected_purchase'
    assert not (tmp_path/'journal.json').exists() and not d.presses
