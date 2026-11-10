"""Timing honesty, pristine Turbo ordering and same-hover native proof reuse."""
import copy,json,time
from types import SimpleNamespace
import pytest


@pytest.mark.parametrize('permanent',[False,True])
def test_profile_atomic_save_survives_transient_windows_reader_without_losing_order(tmp_path,monkeypatch,permanent):
    from pathlib import Path
    from src.core.preferences import Preferences
    path=tmp_path/'preferences.json'
    p=Preferences(path,[SimpleNamespace(id='known',role='survivor',character=None)])
    p.save('en','survivor');old=path.read_bytes();p.priorities['survivor']=['known']
    original=Path.replace;attempts=[]
    def replace(source,target):
        if source==path.with_suffix('.json.tmp'):
            attempts.append(1)
            if permanent or len(attempts)<3:
                assert path.read_bytes()==old
                raise PermissionError('reader temporarily denies FILE_SHARE_DELETE')
        return original(source,target)
    monkeypatch.setattr(Path,'replace',replace)
    if permanent:
        with pytest.raises(PermissionError):p.save('en','survivor')
        assert path.read_bytes()==old and len(attempts)==7
    else:
        p.save('en','survivor')
        assert json.loads(path.read_text())['priorities']['survivor']==['known'] and len(attempts)==3


def test_native_pre_input_frame_requires_its_own_known_header():
    from tests.test_v57_turbo import setup
    from src.core.screen_manager import ScreenError
    e,d,f,r=setup();e.screen.capture_overlay=lambda frame:frame
    r.level=None;r.level_confidence=0
    with pytest.raises(ScreenError,match='frame_unstable'):
        e._buy_once(f,r,r.nodes[1])
    assert not d.presses and not e.pending_action and e.scene_record is None


@pytest.mark.parametrize('level,confidence',[(None,0),(9,.94),(10,.99)])
def test_price_clean_maps_reject_missing_weak_or_different_header(level,confidence):
    from tests.test_automation import web,Device,engine
    from dataclasses import replace
    from src.core.path_planner import RoutePlan
    from src.core.screen_manager import ScreenError
    from scripts.live_test_v54 import scene_key,wait_price_scene
    initial=web();initial.level=9;initial.level_confidence=.99
    d=Device([initial]);e=engine(d,dry_run=False);f=d.capture()
    fresh=copy.deepcopy(initial);fresh.level=level;fresh.level_confidence=confidence
    reads=[]
    def read(*args,**kwargs):
        reads.append(1)
        return replace(f,captured_at=f.captured_at+len(reads)*.01),fresh,RoutePlan(1,[1])
    e.recover_read=read
    with pytest.raises(ScreenError,match='chain_map_changed'):
        wait_price_scene(e,scene_key(f,initial),f.to_screen(230,200),True)
    assert len(reads)==4 and not d.presses


def test_turbo_runs_before_native_chain_preparation(monkeypatch):
    from tests.test_v57_turbo import setup
    import src.core.turbo_burst as turbo
    e,d,f,r=setup();expected=(f,r,None)
    monkeypatch.setattr(turbo,'dispatch',lambda *args:expected)
    e.native_chain_dispatch=lambda *args:pytest.fail('native hover preempted pristine Turbo')
    assert e.buy(f,r,r.nodes[1]) is expected


def test_rejected_turbo_is_not_recomputed_for_identical_frame(monkeypatch):
    from tests.test_v57_turbo import setup
    import src.core.turbo_burst as turbo
    e,d,f,r=setup();calls=[]
    monkeypatch.setattr(turbo,'dispatch',lambda *args:calls.append(args))
    assert e.try_turbo(f,r,r.nodes[1]) is None
    assert e.try_turbo(f,r,r.nodes[1]) is None
    fresh=copy.copy(f);fresh.captured_at+=1
    assert e.try_turbo(fresh,r,r.nodes[1]) is None
    assert len(calls)==2 and not d.presses


def test_absent_priorities_allow_known_filler_burst_without_promoting_unknown():
    from tests.test_v57_turbo import setup
    from src.core.models import NodeState,NodeType
    from src.core.turbo_burst import prepare
    e,d,f,r=setup();e.priorities=['absent_id'];r.nodes[1].icon_id=None;r.nodes[1].type=NodeType.UNKNOWN
    r.nodes[2].state=NodeState.AVAILABLE;r.edges.append((0,2))
    assert [n.id for _,n in prepare(e,f,r)]==[2,3,4,5,6]
    assert r.nodes[1].icon_id is None and not d.presses


def test_exact_nested_timeline_survives_bounded_sample_retention(tmp_path):
    from src.core.performance import Performance
    p=Performance(tmp_path/'performance.json')
    with p.span('outer'):
        with p.span('inner'):pass
    for _ in range(5001):p.add('duration_only',.001)
    p.save()
    rows=[json.loads(l) for l in (tmp_path/'performance.timeline.jsonl').read_text().splitlines()]
    inner,outer=rows[:2]
    assert len(p.samples)==5000 and len(rows)==5003
    assert inner['parent_span']==outer['span_id']
    assert outer['started_at']<=inner['started_at']<=inner['ended_at']<=outer['ended_at']
    assert 'started_at' not in rows[-1]  # A duration alone has no invented start.


def test_exclusive_intervals_do_not_add_parent_and_child_time():
    from scripts.audit_live_speed_v58 import exclusive
    samples=[dict(stage='parent',started_at=1,ended_at=11),dict(stage='scan',started_at=3,ended_at=8),
             dict(stage='io',started_at=4,ended_at=5),dict(stage='unplaced',seconds=500)]
    totals,counts,_=exclusive(samples,0,12)
    assert totals=={'UNATTRIBUTED':2,'parent':5,'scan':4,'io':1}
    assert counts=={'parent':1,'scan':1,'io':1} and sum(totals.values())==12


def test_survivor_budget_is_separate_role_and_persists_all_500_nodes(tmp_path):
    from src.core.series_budget import SeriesBudget
    from src.core.screen_manager import ScreenError
    a=dict(role='survivor',character='nicolas_cage',max_bp=1000000,max_nodes=500,max_commands=4000,
           allow_prestige=False,allow_chain=True)
    b=SeriesBudget(tmp_path/'ledger.json',a)
    b.reserve(2000000,1000000,'batch',[(i,1) for i in range(500)])
    restored=SeriesBudget(b.path,a)
    with pytest.raises(ScreenError,match='action_limit'):restored.reserve(2000000,1,'single',[(1,2)])
    bad=dict(a,role='killer',character='')
    with pytest.raises(ValueError,match='series_scope_invalid'):SeriesBudget(tmp_path/'other.json',bad)


def test_survivor_scene_save_preserves_killer_scene(tmp_path):
    from scripts.live_optimize_v55 import save_scene
    p=tmp_path/'preferences.json';old={'purchase_scenes':{'killer/knight':{'pending':'keep'}},'character':'knight'}
    p.write_text(json.dumps(old))
    save_scene(p,{'pending':None},'nicolas_cage','survivor')
    new=json.loads(p.read_text())
    assert new['purchase_scenes']['killer/knight']==old['purchase_scenes']['killer/knight']
    assert new['character']=='knight' and new['purchase_scenes']['survivor/']=={'pending':None}


def test_unknown_header_is_a_negative_centre_price_scene():
    from tests.test_v57_turbo import setup
    from scripts.live_test_v54 import scene_key
    from scripts.native_price_v55 import center_scene_valid
    e,d,f,r=setup();r.level=None;r.level_confidence=0
    assert not center_scene_valid(scene_key(f,r),f,r)


def test_unknown_post_purchase_header_never_enters_red_confirmation_pair():
    from tests.test_v57_turbo import setup
    from src.core.models import NodeState
    from src.core.purchase_outcome import PurchaseEvidence
    e,d,f,r=setup();node=r.nodes[1];ev=PurchaseEvidence(f,r,node,time.monotonic())
    red=copy.deepcopy(r);red.nodes[1].state=NodeState.PURCHASED;red.nodes[1].state_confidence=.93
    for increment,level,expected in ((1,20,False),(2,None,False),(3,20,True)):
        after=copy.copy(f);after.captured_at+=increment;view=copy.deepcopy(red);view.level=level
        assert ev.observe(after,view,time.monotonic())[1] is expected
    assert len(ev.red_observations)==2 and all(view.level==20 for _,view in ev.red_observations)


def test_native_unknown_reused_slot_retires_only_on_two_pristine_new_maps():
    from dataclasses import fields
    from pathlib import Path
    from src.core.models import Analysis,Node,NodeState,NodeType,ScreenState
    from src.core.screen_manager import CapturedFrame,Rect,WindowInfo
    from src.core.level_transition import pending_target_on_proven_next_level
    from src.utils.images import read_image
    from tests.test_v57_turbo import setup
    root=Path(__file__).resolve().parents[1];p=root/'debug/v58/nicolas_20261018_181723/unknown_12'
    saved=json.loads((p/'pending_scene_before_recheck.json').read_text())['scene']
    e,d,_,_=setup();e.persisted_scene=copy.deepcopy(saved)
    observations=[]
    for name in ('1792338200725264000_4927319e','1792338201034299500_fa045811'):
        folder=p/name;data=json.loads((folder/'analysis.json').read_text(encoding='utf-8'))
        meta=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))['frame'];w=meta['window']
        window=WindowInfo(w['hwnd'],w['title'],Rect(**w['client']),Rect(**w['monitor']),w['dpi'],True,w['pid'])
        roi=Rect(**meta['roi']);raw=read_image(folder/'raw.png')
        f=CapturedFrame(raw[roi.y:roi.y+roi.height,roi.x:roi.x+roi.width].copy(),window,roi,meta['captured_at'],
            raw_image=raw,level=12,level_confidence=meta['level_confidence'])
        names={field.name for field in fields(Analysis)}
        r=Analysis(**{k:v for k,v in data.items() if k in names and k!='nodes'})
        r.screen_state=ScreenState(r.screen_state)
        r.nodes=[Node(**n) for n in data['nodes']]
        for n in r.nodes:n.state=NodeState(n.state);n.type=NodeType(n.type)
        assert pending_target_on_proven_next_level(saved,f,r)
        observations.append((f,r))
    e.signature=observations[0][0].window.signature
    assert not e.recheck_saved_scene(*observations[0])
    assert e.recheck_saved_scene(*observations[1])
    assert e.actions==0 and not d.presses and e.persisted_scene is None
    bad=copy.deepcopy(observations[-1][1]);bad.level=11
    assert not pending_target_on_proven_next_level(saved,observations[-1][0],bad)
    bad=copy.deepcopy(observations[-1][1])
    for n in bad.nodes:n.icon_id=None
    assert not pending_target_on_proven_next_level(saved,observations[-1][0],bad)


def test_native_nicolas_label_has_independent_level_holdouts():
    from pathlib import Path
    from src.core.bloodweb_header import read_level
    from src.utils.images import read_image
    root=Path(__file__).resolve().parents[1]
    p=root/'debug/v58/nicolas_20261018_181723'
    for number,expected in ((1,1),(2,1),(3,2),(4,2)):
        image=read_image(p/f'price_review_{number:03d}/clean.png')
        level,score=read_level(image,root)
        assert level==expected and score>=.95
        image=image.copy();image[70:105,544:610]=0
        assert read_level(image,root)[0] is None


def test_native_automatic_requires_both_original_regions():
    from pathlib import Path
    from src.utils.images import read_image
    from src.core.video_ui import certificate
    root=Path(__file__).resolve().parents[1]
    image=read_image(root/'references/bloodweb/v58_nicolas/automatic_native.png')
    proof=certificate(image,root,'automatic')
    assert proof and set(proof['regions'])=={'title','description'} and min(proof['scores'].values())>=.96
    image=image.copy();image[590:629,765:1228]=0
    assert certificate(image,root,'automatic') is None


def test_native_4000_rehover_holdouts_keep_strict_price_reader():
    from pathlib import Path
    from src.core.resources import ResourceReader
    from scripts.native_price_v55 import NativePriceLocator
    from src.utils.images import read_image
    root=Path(__file__).resolve().parents[1];reader=ResourceReader(root)
    locator=NativePriceLocator(root,json.loads((root/'data/native_price_locator.json').read_text()),reader)
    p=root/'debug/v58/nicolas_20261018_181723/price_review_009'
    for i in range(4):
        observed=locator.locate(read_image(p/f'current_3_{i}.png'),[579,500])
        assert observed and observed['value']==4000 and observed['score']>=.96
    for i in range(2):
        assert locator.locate(read_image(p/f'node_3_{i}.png'),[579,500])['value']==4000


@pytest.mark.parametrize('expired',[False,True])
@pytest.mark.parametrize('kind',['single','early_center'])
def test_two_native_reads_one_hover_are_not_ocr_decoded_again(tmp_path,monkeypatch,expired,kind):
    from tests.test_v57_turbo import setup
    import scripts.live_test_v54 as survey
    e,d,f,r=setup();f.raw_image=f.image;e.settings.purchase_timeout=8;e.runtime_deadline=time.monotonic()+30
    r.nodes=r.nodes[:2];r.edges=[(0,1)]
    e.recover_read=lambda **kw:(f,r,None);e.event=lambda *a:None;e.wait=lambda n:None;e.gate=lambda:None
    monkeypatch.setattr(survey,'ROOT',tmp_path)
    clean_calls=[]
    monkeypatch.setattr(survey,'wait_price_scene',lambda *args:(clean_calls.append(1) or (f,r,None),(f,r,None)))
    overlay=copy.copy(f);overlay.captured_at=time.monotonic()-((6 if kind=='early_center' else 3) if expired else (3 if kind=='early_center' else .1))
    captures=[]
    def capture(*args):
        fresh=copy.copy(overlay);fresh.captured_at+=len(captures)*.001;captures.append(fresh);return fresh
    e.screen.capture_overlay=capture
    calls=[]
    def locate(*args):calls.append(1);return dict(value=2500,box=[0,0,2,2])
    e.native_price_locator=SimpleNamespace(locate=locate)
    e.check_held=lambda *args:f.to_screen(r.nodes[1].x,r.nodes[1].y)
    e.mouse.park=lambda *args:None;e.screen.parking_point=lambda *args:(1,1)
    reobserved=[]
    monkeypatch.setattr(survey,'reobserve_price',lambda *args:(reobserved.append(1) or [2500,2500],[time.monotonic(),time.monotonic()+.01]))
    review=survey.NativeReview(e,tmp_path)
    review.prepare(f,r,None,[r.nodes[1]],kind)
    assert len(captures)==2 and len(calls)==2 and clean_calls==[1]
    assert bool(reobserved)==expired
    assert bool(review.permit['proofs'][0].get('same_hover_native_proof')) is (not expired)
