"""Direct production preparation, price provenance and no-image batch contract."""
import copy,time
from types import SimpleNamespace
import pytest
from src.core.direct_buy import known_price,remember_price,chain_probe_allowed,reject_chain,install
from src.core.models import NodeState
from tests.test_v57_turbo import setup


def test_ordinary_buy_uses_one_move_and_no_price_probe_for_known_target(tmp_path,monkeypatch):
    from tests.test_automation import Device,web,engine
    from scripts.live_test_v54 import NativeReview
    r=web();node=r.nodes[1];node.rarity='brown';node.confidence=.96;node.contour_score=.9
    after=copy.deepcopy(r);after.nodes[1].state=NodeState.PURCHASED
    d=Device([after,after]);d.library=SimpleNamespace(event_metadata={});d.matcher=SimpleNamespace(by_id={node.icon_id:object()})
    e=engine(d,dry_run=False);e.report.path=tmp_path/'ordinary.json';install(e,tmp_path)
    def forbidden(*a,**k):raise AssertionError('known single must not hover or OCR')
    monkeypatch.setattr(NativeReview,'prepare',forbidden)
    e.native_chain_dispatch=forbidden
    f=d.capture();f.bloodpoints=50000
    e.buy(f,r,node)
    assert len(d.presses)==1 and e.actions==1
    assert any(x['stage']=='direct_buy_prepared' and x['price_probes']==0 for x in e.report.entries)


def test_direct_single_does_not_replace_a_remote_priority_chain():
    from src.core.direct_buy import prefer_direct_single
    e,d,f,r=setup();e.priorities=['item6']
    assert not prefer_direct_single(e,f,r,r.nodes[1])




def test_weak_opening_geometry_gets_only_one_read_before_any_price_probe(monkeypatch):
    import src.core.turbo_burst as turbo
    from src.core.path_planner import RoutePlan
    from src.core.screen_manager import ScreenError
    e,d,f,r=setup();fresh=copy.copy(f);fresh.captured_at+=.01;calls=[]
    def reject(*a):e._turbo_last_reason='target_geometry_unproven'
    monkeypatch.setattr(turbo,'dispatch',reject)
    e.recover_read=lambda **kw:(calls.append(kw) or (fresh,r,RoutePlan()))
    with pytest.raises(ScreenError,match='purchase_preparation_changed'):e.try_turbo(f,r,r.nodes[1])
    assert len(calls)==1 and e.preparation_replacement[0] is fresh and not d.presses
    assert e.try_turbo(fresh,r,r.nodes[1]) is None and len(calls)==1


def test_batch_counters_include_earlier_fallback_and_distinguish_level_from_global():
    from src.core.turbo_burst import dispatch
    from src.core.path_planner import RoutePlan
    e,d,f,r=setup();e.wait=lambda seconds:None
    e.opening.full_scans=123;e.performance.levels.confirm(f,r,transition=True)
    e.performance.levels.current['full']=2;e.price_probes_by_scene={(f.window.signature,r.level):3}
    def after(*a,**k):
        fresh=d.capture();web=copy.deepcopy(r)
        for n in web.nodes[1:6]:n.state=NodeState.PURCHASED
        return fresh,web,RoutePlan()
    e.recover_read=after
    dispatch(e,f,r)
    row=next(x for x in e.report.entries if x['stage']=='burst_outcome')
    assert row['full_scans_before']==2 and row['full_scans_total_before']==123 and row['price_probes_before']==3
    assert len(d.presses)==5 and row['confirmed_purchases_after_burst']==5


def native_case():
    e,d,f,r=setup();n=r.nodes[1];n.orange_background=True;n.icon_margin=.1
    p=dict(price=3250,proofs=[dict(node=n.id,price=3250,stamps=[f.captured_at-.2,f.captured_at-.1],
        source_sha256='a'*64,box=[0,0,10,10])])
    return e,d,f,r,n,p


@pytest.mark.parametrize('fault',['none','unknown','margin','one_read','hash','price'])
def test_native_cache_never_promotes_unknown_or_single_observation(fault):
    e,d,f,r,n,p=native_case()
    if fault=='unknown':n.icon_id=None
    if fault=='margin':n.icon_margin=.01
    if fault=='one_read':p['proofs'][0]['stamps'].pop()
    if fault=='hash':p['proofs'][0]['source_sha256']=''
    if fault=='price':p['price']=4000
    assert known_price(e,f,n) is None
    remember_price(e,f,n,p);bound=known_price(e,f,n)
    assert bool(bound)==(fault=='none')
    if bound:assert bound.value==3250 and bound.source=='native_observed_upper_bound'


@pytest.mark.parametrize('fault',['expired','window','rarity','type','identity'])
def test_observed_price_is_scoped_to_current_client_and_identity(fault):
    from dataclasses import replace
    from src.core.models import NodeType
    e,d,f,r,n,p=native_case();remember_price(e,f,n,p)
    if fault=='expired':e.direct_price_observations[n.icon_id]['observed_at']-=601
    if fault=='window':f.window=replace(f.window,pid=f.window.pid+1)
    if fault=='rarity':n.rarity='green'
    if fault=='type':n.type=NodeType.OFFERING
    if fault=='identity':n.icon_id='another'
    assert known_price(e,f,n) is None


def test_chain_rejection_reuses_same_substantive_map_but_not_new_states():
    e,d,f,r=setup();reject_chain(e,f,r)
    assert not chain_probe_allowed(e,copy.copy(f),copy.deepcopy(r))
    changed=copy.deepcopy(r);changed.nodes[1].state=NodeState.PURCHASED
    assert chain_probe_allowed(e,f,changed)
    assert not chain_probe_allowed(e,f,r)




def test_observed_event_price_can_authorize_known_nonperk_without_colour_pricing():
    from src.core.turbo_burst import prepare
    e,d,f,r=setup()
    for n in r.nodes[1:]:
        n.orange_background=True;n.icon_margin=.1
        p=dict(price=3250,proofs=[dict(node=n.id,price=3250,stamps=[f.captured_at-.2,f.captured_at-.1],source_sha256='a'*64)])
        remember_price(e,f,n,p)
    assert len(prepare(e,f,r))==5
    e.direct_price_observations.clear()
    assert prepare(e,f,r) is None


def test_real_nicolas35_batch_has_two_native_outcomes_for_each_of_five_points(root,tmp_path):
    import hashlib,json
    from dataclasses import fields
    from src.core.models import Analysis,Node,NodeType,ScreenState
    from src.core.screen_manager import CapturedFrame,WindowInfo,Rect
    from src.core.scene_memory import record_scene
    from src.core.action_outcomes import ActionOutcomes
    folder=root/'tests/fixtures/v511/burst35';sources=json.loads((folder/'sources.json').read_text(encoding='utf-8'))
    for row in sources['sources']:
        assert hashlib.sha256((root/row['copy']).read_bytes()).hexdigest()==row['sha256']
    views=[]
    for i in range(3):
        data=json.loads((folder/f'{i}_analysis.json').read_text(encoding='utf-8'))
        meta=json.loads((folder/f'{i}_manifest.json').read_text(encoding='utf-8'))['frame']
        result=Analysis(**{k:v for k,v in data.items() if k in {f.name for f in fields(Analysis)} and k!='nodes'})
        result.screen_state=ScreenState(result.screen_state);result.nodes=[Node(**n) for n in data['nodes']]
        for n in result.nodes:n.state=NodeState(n.state);n.type=NodeType(n.type)
        w=dict(meta['window']);w['client']=Rect(**w['client']);w['monitor']=Rect(**w['monitor'])
        # Only genuine serialized geometry is needed; this is not a generated image.
        shape=SimpleNamespace(shape=(meta['image_size'][1],meta['image_size'][0],3))
        frame=CapturedFrame(shape,WindowInfo(**w),Rect(**meta['roi']),meta['captured_at'],
            bloodpoints=meta['bloodpoints'],level=meta['level'],level_confidence=meta['level_confidence'])
        views.append((frame,result))
    points=sources['actual_group']['points']
    scene=record_scene(*views[0],points,dict(operation_id=sources['actual_group']['operation_id'],
        point=points[-1],radius=30,batch=dict(physical_points=points)))
    row=ActionOutcomes(tmp_path/'actual_outcomes.json').reconcile(scene,views[1:],'saved_native_replay')
    assert row['states']==['purchased']*5 and row['confirmed_nodes']==5 and not row['absorbed_nodes']
