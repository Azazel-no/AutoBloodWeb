"""Real video holdouts and explicit fake-device contracts, no game input."""
import copy,json,time
from dataclasses import replace
import pytest
from src.core.bloodweb_header import automatic_tooltip
from src.core.bloodweb_detector import BloodwebDetector,find_center
from src.core.early_unlock import eligible,purchase_level
from src.core.modal_recovery import dismiss_reward
from src.core.models import NodeState,ScreenState,Analysis
from src.core.prestige import ready_evidence,reviewed_price_region
from src.core.resources import ResourceReader
from src.core.screen_manager import ScreenError
from src.core.video_ui import certificate
from src.utils.images import read_image
from tests.test_automation import Device,engine,web
from tests.test_v46 import Clock


def test_video_holdouts_recognize_reward_automatic_and_prestige(root,library):
    folder=root/'references/bloodweb/v54_onryo'
    detector=BloodwebDetector(library,'killer','onryo')
    reward=read_image(folder/'reward_holdout_1500.png')
    assert detector.reward_box(reward)==(1666,906,34,22)
    assert automatic_tooltip(read_image(folder/'automatic_holdout_1830.png'),root)
    clean=read_image(folder/'prestige_clean_1308.png')
    assert ready_evidence(clean,root,find_center(clean))['source']=='user_video_prestige_wreath'
    result=detector.analyze(clean)
    assert result.screen_state==ScreenState.PRESTIGE_READY
    animation=read_image(folder/'prestige_animation_negative_1410.png')
    assert detector.reward_box(animation) is None and certificate(animation,root,'automatic') is None
    assert ready_evidence(animation,root,find_center(animation)) is None
    assert not automatic_tooltip(clean,root) and detector.reward_box(clean) is None


def test_reward_needs_title_and_ok_and_reviewed_relative_layout(root):
    image=read_image(root/'references/bloodweb/v54_onryo/reward_holdout_1500.png')
    for x,y,w,h in ((755,253,410,39),(1666,906,34,22)):
        hidden=image.copy();hidden[y:y+h,x:x+w]=0
        assert certificate(hidden,root,'reward') is None
    moved=image.copy();moved[906:928,1666:1700]=0
    moved[850:872,1666:1700]=image[906:928,1666:1700]
    assert certificate(moved,root,'reward') is None


def test_video_price_is_real_decoded_crop_not_live_proof(root):
    record=json.loads((root/'assets/ui/video_v54/manifest.json').read_text(encoding='utf-8'))['prestige_price_calibration']
    region=reviewed_price_region(root,record);assert region and record['video_evidence_only']
    image=read_image(root/record['source']);x,y,w,h=region.box
    assert ResourceReader(root).decode(image[y:y+h,x:x+w])==20000


def test_early_center_preserves_concrete_priorities_and_only_resolved_attempts():
    initial=web();initial.level=4;initial.level_confidence=.99
    initial.nodes[1].state=NodeState.PURCHASED
    second=copy.deepcopy(initial.nodes[1]);second.id=2;second.x=300;second.state=NodeState.AVAILABLE
    initial.nodes.append(second);initial.edges.append((0,2))
    d=Device([initial]);runner=engine(d,dry_run=False);frame=d.capture()
    runner.attempted=[frame.to_screen(initial.nodes[1].x,initial.nodes[1].y)]
    assert eligible(runner,initial,frame)
    assert runner.priorities==['target']
    runner.pending_action=True;assert not eligible(runner,initial,frame)
    runner.pending_action=False;initial.nodes[1].state=NodeState.AVAILABLE
    assert not eligible(runner,initial,frame)
    runner.attempted.clear();initial.level=10
    assert not eligible(runner,initial,frame)


@pytest.mark.parametrize('proof_age',[0,6])
def test_center_counts_only_new_nodes_after_partial_purchase(monkeypatch,proof_age):
    clock=Clock();monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    initial=web(NodeState.PURCHASED);initial.level=4;initial.level_confidence=.99;initial.nodes[0].geometry_confidence=.95
    second=copy.deepcopy(initial.nodes[1]);second.id=2;second.x=300;second.state=NodeState.AVAILABLE
    initial.nodes.append(second);initial.edges.append((0,2))
    owned=copy.deepcopy(initial);owned.nodes[2].state=NodeState.PURCHASED
    following=web();following.level=5;following.level_confidence=.99
    d=Device([owned,owned,following,following]);runner=engine(d,dry_run=False)
    runner.settings.purchase_timeout=8
    runner.wait=clock.advance;d.parking_point=lambda f:None
    frame=d.capture();d.index=0;runner.actions=1;runner.attempted=[frame.to_screen(230,200)]
    runner.automatic_proof=(frame.window.signature,4,frame.to_screen(150,200),clock.now-proof_age)
    purchase_level(runner,frame,initial)
    assert len(d.presses)==1 and runner.actions==2 and not runner.pending_action


def reward():return Analysis(400,400,ScreenState.REWARD,ok_box=(300,300,40,20),confidence=.99)


def modal_runner(monkeypatch,results):
    clock=Clock();monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    d=Device(results);runner=engine(d,dry_run=False,auto_prestige=True,max_actions=None,run_mode='continuous')
    runner.prestige_approved=True;runner.wait=clock.advance
    original=d.capture
    d.capture=lambda:replace(original(),captured_at=clock.now)
    return clock,d,runner


def test_reward_waits_for_two_frames_then_closes_once_and_continues(monkeypatch):
    clock,d,runner=modal_runner(monkeypatch,[reward(),reward(),web(),web()])
    frame=d.capture();result=d.analyze(frame.image,'live_game')
    fresh,result,_=dismiss_reward(runner,frame,result)
    assert result.screen_state==ScreenState.BLOODWEB and len(d.presses)==runner.attempts==1
    assert runner.actions==0 and not runner.pending_action and d.releases>=2


def test_reward_maximum_five_attempts_and_no_repeated_stale_click(monkeypatch):
    clock,d,runner=modal_runner(monkeypatch,[reward()])
    runner.settings.refresh_timeout=15
    frame=d.capture()
    with pytest.raises(ScreenError,match='modal_attempt_limit'):dismiss_reward(runner,frame,reward())
    assert len(d.presses)==runner.attempts==5 and d.releases>=6
    assert clock.now>=5


@pytest.mark.parametrize('bad',['pending','approval','unknown','probe','command_cap'])
def test_modal_guards_never_click(monkeypatch,bad):
    clock,d,runner=modal_runner(monkeypatch,[reward()]);frame=d.capture();result=reward()
    if bad=='pending':runner.pending_action=True
    if bad=='approval':runner.prestige_approved=False
    if bad=='unknown':result.screen_state=ScreenState.UNKNOWN
    if bad=='probe':frame=replace(frame,probe_only=True)
    if bad=='command_cap':runner.settings.max_input_commands=1;runner.attempts=1
    with pytest.raises(ScreenError):dismiss_reward(runner,frame,result)
    assert not d.presses


def test_modal_stop_releases_without_another_click(monkeypatch):
    clock,d,runner=modal_runner(monkeypatch,[reward()]);frame=d.capture()
    runner.halt()
    with pytest.raises(InterruptedError):dismiss_reward(runner,frame,reward())
    assert not d.presses and d.releases>=2




def test_normal_run_closes_rewards_then_buys_without_second_start(monkeypatch):
    clock,d,runner=modal_runner(monkeypatch,[reward(),reward(),reward(),web(),web(),web(),web(NodeState.PURCHASED),web(NodeState.PURCHASED)])
    runner.settings.max_actions=1;runner.settings.max_input_commands=2
    runner.run()
    assert len(d.presses)==runner.attempts==2 and runner.actions==1
    assert not runner.pending_action
    assert any(e['stage']=='reward_dismissed' for e in runner.report.entries)


def test_unknown_animation_recovers_without_a_modal_click_or_pause(monkeypatch):
    unknown=Analysis(400,400,ScreenState.UNKNOWN)
    clock,d,runner=modal_runner(monkeypatch,[unknown,unknown,web(),web(),web(NodeState.PURCHASED),web(NodeState.PURCHASED)])
    runner.settings.max_actions=1;runner.run()
    assert len(d.presses)==1 and runner.actions==1
    assert not any(e['stage']=='modal_input' for e in runner.report.entries)


def test_center_stops_on_regression_of_previously_purchased_node(monkeypatch):
    clock=Clock();monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    initial=web(NodeState.PURCHASED);initial.level=4;initial.level_confidence=.99;initial.nodes[0].geometry_confidence=.95
    node=copy.deepcopy(initial.nodes[1]);node.id=2;node.x=300;node.state=NodeState.AVAILABLE
    initial.nodes.append(node);initial.edges.append((0,2))
    contradiction=copy.deepcopy(initial);contradiction.nodes[1].state=NodeState.AVAILABLE
    d=Device([contradiction]);runner=engine(d,dry_run=False);runner.wait=clock.advance
    d.parking_point=lambda f:None;frame=d.capture()
    runner.automatic_proof=(frame.window.signature,4,frame.to_screen(150,200),clock.now)
    with pytest.raises(ScreenError,match='automatic_unlock_state_contradiction'):purchase_level(runner,frame,initial)
    assert len(d.presses)==1 and runner.pending_action and not runner.actions and d.releases>=2


def test_modal_command_is_included_in_current_level_metrics(monkeypatch):
    clock,d,runner=modal_runner(monkeypatch,[reward(),reward(),web(),web()])
    frame=d.capture();scene=web();scene.level=50
    runner.performance.levels.read(frame,scene,True,0,0)
    runner.performance.levels.confirm(frame,scene)
    dismiss_reward(runner,frame,reward())
    assert runner.performance.levels.report()['records'][0]['commands']==1
