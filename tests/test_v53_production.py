"""Production integration, real historical pixels and no-input device contracts."""
import copy
import time
from dataclasses import replace
from types import SimpleNamespace
import numpy as np
import pytest
from src.core.automation import Automation,Settings,State
from src.core.fast_opening import FastOpening,OpeningFallback
from src.core.models import Match,NodeState,NodeType,ScreenState
from src.core.persistent_route import PersistentVision,PersistentRoute
from src.core.purchase_outcome import PurchaseEvidence
from src.core.screen_manager import ScreenError
from tests.test_automation import Device,engine,web
from tests.test_v51_hybrid import scene


def test_normal_start_uses_persistent_vision_with_selected_policy():
    device=Device([]);runner=Automation(device,device,device,[],Settings())
    assert isinstance(runner.opening,PersistentVision)
    assert runner.opening.local_recognition and not runner.opening.route.strict_priorities
    assert isinstance(Automation(device,device,device,[],Settings(persistent_route=False)).opening,FastOpening)
    assert not isinstance(Automation(device,device,device,[],Settings(persistent_route=False)).opening,PersistentVision)
    with pytest.raises(ValueError,match='persistent_route'):Settings(persistent_route=1).validate()


def test_explicit_command_cap_counts_proven_miss_retry_before_second_mouse(monkeypatch):
    from tests.test_v46 import scenario
    runner,device,_=scenario(monkeypatch,lambda *a:None)
    runner.settings.max_input_commands=1;runner.run()
    assert len(device.presses)==1 and runner.attempts==1 and runner.actions==0
    assert any(e.get('reason')=='action_limit' for e in runner.report.entries)
    with pytest.raises(ValueError,match='max_input_commands'):Settings(max_input_commands=True).validate()


@pytest.mark.parametrize('mode',['smart','strict'])
def test_production_route_preserves_selected_priority_policy(monkeypatch,mode):
    from src.core.path_planner import plan_route
    frame,result=scene(3);calls=[]
    def planner(analysis,priorities,policy,excluded):
        calls.append(policy);return plan_route(analysis,priorities,policy,excluded)
    monkeypatch.setattr('src.core.persistent_route.plan_route',planner)
    route=PersistentRoute(strict_priorities=False);route.choose(frame,result,['goal'],mode)
    assert calls and set(calls)=={mode}


def test_full_fallback_can_reseed_pending_map_without_clearing_attempt_or_authorizing_input(root,library):
    from tests.test_v49_opening import opening_scene
    _,frame,_,detector=opening_scene(root,library)
    device=Device([]);device.capture=lambda:replace(frame,captured_at=time.monotonic())
    runner=Automation(device,detector,device,[],Settings(dry_run=False,startup_delay=0,fast_early_levels=False),approved=True,hotkeys_ready=True)
    current,result,plan=runner.read();target=next(n for n in result.nodes if n.id==plan.next_node)
    point=current.to_screen(target.x,target.y)
    runner.attempted=[point];runner.pending_action=True;runner.opening.cancel('test_full_fallback')
    fresh,result,plan=runner.read()
    assert runner.opening.active and runner.opening.full_scans==2
    assert runner.pending_action and runner.attempted==[point]
    assert target.id in runner.opening.expected_purchases and plan.next_node is None
    with pytest.raises(ScreenError,match='purchase_result_unknown'):runner._buy_once(fresh,result,node=target)
    assert not device.presses


@pytest.mark.parametrize('returned_id',[None,'goal','conflicting'])
def test_local_ranking_is_independent_and_conflicting_known_id_still_falls_back(monkeypatch,returned_id):
    frame,result=scene(2);node=result.nodes[1];node.icon_id='goal'
    vision=PersistentVision(local_recognition=True);vision.local_updates={};vision.refreshed_matches={};vision.certificates=[]
    def failed(*a):raise OpeningFallback('glyph_category_or_rarity_changed')
    monkeypatch.setattr(FastOpening,'verify_local',failed)
    category=SimpleNamespace(category='offering',confidence=.95,candidates=['offering'],event_offering=False,orange_fraction=0.)
    vision.parts=lambda *a:(np.ones((64,64),np.uint8),('green',True,'offering',('offering',),True,False),category,frame.image)
    calls=[]
    def match(*a,**kw):
        calls.append(kw);return Match(icon_id=returned_id,confidence=.9 if returned_id else 0.,margin=.05,contour_score=.8)
    detector=SimpleNamespace(matcher=SimpleNamespace(match=match,by_id={i:SimpleNamespace(category='offerings') for i in ('goal','conflicting')}),
        memory=SimpleNamespace(identity=lambda *a:(None,'fresh',None,None)))
    if returned_id=='conflicting':
        with pytest.raises(OpeningFallback,match='conflicting_fresh_identity'):vision.verify_local(frame,node,detector)
    else:
        vision.verify_local(frame,node,detector)
        assert node.icon_id==returned_id and node.id in vision.refreshed_matches
    assert len(calls)==1 and calls[0]['use_cache'] is False


def test_nonred_animation_does_not_invalidate_map_and_still_needs_two_fresh_reds():
    device=Device([web(),web(),web(NodeState.PURCHASED),web(NodeState.PURCHASED)])
    invalidations=[];device.invalidate=lambda:invalidations.append(True)
    runner=engine(device,dry_run=False);frame=device.capture();result=device.analyze(frame.image,'live_game')
    runner.buy(frame,result,node=result.nodes[1])
    assert runner.actions==1 and runner.purchase_evidence.red_count==2 and not invalidations
    assert len(device.presses)==1


def test_red_evidence_rejects_duplicate_probe_old_and_other_window():
    frame,result=scene(2);node=result.nodes[1];evidence=PurchaseEvidence(frame,result,node,time.monotonic())
    red=copy.deepcopy(result);red.nodes[1].state=NodeState.PURCHASED;red.nodes[1].state_confidence=.95
    fresh=replace(frame,captured_at=frame.captured_at+.1)
    assert not evidence.observe(fresh,red,time.monotonic())[1] and evidence.red_count==1
    for invalid in (fresh,replace(fresh,captured_at=fresh.captured_at+.1,probe_only=True),
            replace(fresh,captured_at=fresh.captured_at+.1,window=replace(frame.window,pid=99))):
        assert not evidence.observe(invalid,red,time.monotonic())[1]
    assert evidence.red_count==1
    assert evidence.observe(replace(frame,captured_at=fresh.captured_at+.2),red,time.monotonic())[1]


def test_entity_resolved_target_continues_to_alternative_without_retry():
    initial=web();second=copy.deepcopy(initial.nodes[1]);second.id=2;second.x=310
    initial.nodes.append(second);initial.edges.append((0,2))
    absorbed=copy.deepcopy(initial);absorbed.nodes[1].state=NodeState.ABSORBED
    done=copy.deepcopy(absorbed);done.nodes[2].state=NodeState.PURCHASED
    device=Device([initial,initial,absorbed,absorbed,absorbed,absorbed,done,done])
    runner=engine(device,dry_run=False,max_actions=2,max_input_commands=2);runner.run()
    assert len(device.presses)==2 and len(set(p for p,_ in device.presses))==2
    assert runner.actions==1 and not runner.pending_action and runner.state==State.PAUSED


def test_transition_wait_returns_two_certified_prestige_frames_to_dispatcher():
    ready=web();ready.screen_state=ScreenState.PRESTIGE_READY;ready.nodes=ready.nodes[:1]
    ready.nodes[0].type=NodeType.PRESTIGE;ready.details['prestige_ready']={'score':1}
    device=Device([ready,ready]);runner=engine(device,dry_run=False)
    frame=device.capture();frame=replace(frame,captured_at=frame.captured_at-.1)
    device.index=0
    _,result,_=runner.wait_next_bloodweb(frame,web(NodeState.PURCHASED))
    assert result.screen_state==ScreenState.PRESTIGE_READY and device.index==2 and not device.presses


def test_level_metrics_exclude_partial_levels_and_unknown_bp():
    from src.core.performance import LevelPerformance
    metrics=LevelPerformance();frame,result=scene(2);frame=replace(frame,bloodpoints=None);result.level=48
    metrics.confirm(frame,result)
    metrics.input();metrics.purchase(True);metrics.wait('animation',.1)
    next_frame=replace(frame,captured_at=frame.captured_at+4);next_result=copy.deepcopy(result);next_result.level=49
    metrics.read(next_frame,next_result,True,1,2);metrics.confirm(next_frame,next_result,transition=True)
    metrics.input();metrics.purchase();metrics.pause('test')
    end=replace(next_frame,captured_at=next_frame.captured_at+3.5);end_result=copy.deepcopy(result);end_result.level=50
    metrics.confirm(end,end_result,transition=True)
    report=metrics.report();assert report['summary']['10-49']==dict(count=1,median=3.5,p95=None,max=3.5)
    assert report['records'][0]['commands']==1 and report['records'][0]['priority_items']==1
    assert report['records'][1]['full']==1 and report['records'][1]['local_matches']==2
    assert all(r['bp_spent'] is None for r in report['records'])


def test_addressed_real_series_replay_reduces_full_scans_without_erasing_guards(library):
    from scripts.replay_production_v53 import replay
    output=replay(library)
    assert output['new_inputs']==0 and output['historical_confirmed']==5
    # Owned-ring frames and flickering absorbed rims no longer force a full scan;
    # every decision on these 18 real frames matched the old code (8 full before).
    assert output['full']<=6 and output['full']+output['compact']==18
    assert 'connection_changed' in output['aborts']
    assert not {'node_frame_changed','absorbed_state_contradiction'}&set(output['aborts'])
    assert max(r['route']['replans'] for r in output['frames'] if r['route'])>1


def test_prestige_price_calibration_needs_reviewed_source_sha_and_client_size(root):
    from src.core.prestige import reviewed_price_region
    assert reviewed_price_region(root,None) is None
    assert reviewed_price_region(root,dict(kind='prestige_total_price',reviewed=True,
        source='references/bloodweb/prestige_node_example.png',sha256='0'*64,box=[0,0,10,10],client_size=[950,1015])) is None
    assert reviewed_price_region(root,dict(kind='chain_total_price',reviewed=True)) is None


@pytest.fixture(scope='module')
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_gui_start_passes_enabled_production_route_and_preserves_legacy_choice(app,library,tmp_path,monkeypatch):
    import json
    from src.gui.main_window import MainWindow
    from tests.test_v44 import spin_until
    class Keys:
        ready=True
        def __init__(self,*a,**k):pass
        def close(self):pass
    monkeypatch.setattr('src.gui.main_window.GlobalHotkeys',Keys)
    created=[];device=Device([web()])
    def factory(lib,role,priorities,settings,approved,hotkeys):
        created.append(settings);return Automation(device,device,device,priorities,settings,approved=approved,hotkeys_ready=hotkeys)
    monkeypatch.setattr('src.gui.main_window.make_engine',factory)
    path=tmp_path/'preferences.json';path.write_text(json.dumps({'settings':{'experimental_persistent':False}}),encoding='utf-8')
    window=MainWindow(library,path)
    try:
        assert window.experimental_persistent.isChecked();window.dry_run.setChecked(True)
        window.start_automation();spin_until(lambda:window.auto_worker is None,app)
        assert len(created)==1 and created[0].persistent_route and not created[0].experimental_persistent
        window.experimental_persistent.setChecked(False)
        saved=json.loads(path.read_text(encoding='utf-8'))['settings']
        assert saved['persistent_route'] is False and saved['experimental_persistent'] is False
    finally:window.close()
