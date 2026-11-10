"""Native tutorial pixels; injected controller cases never touch the game."""
import copy,hashlib,json,time
from dataclasses import replace
import pytest
from src.core.models import Analysis,ScreenState,NodeState,Node,NodeType
from src.core.screen_manager import ScreenManager,ScreenError,WindowInfo,Rect
from src.core.bloodweb_detector import BloodwebDetector
from src.core.video_ui import certificate
from src.core.modal_recovery import dismiss_tutorial
from src.core.scene_memory import record_scene
from tests.test_automation import Device,engine,web
from tests.test_v46 import Clock
from src.utils.images import read_image
from tests.test_gui import app


def test_native_tutorial_holdout_is_read_before_any_center_search(root,library,monkeypatch):
    item=json.loads((root/'tests/fixtures/v54/tutorial_unpredictability.json').read_text(encoding='utf-8'))
    path=root/item['source'];assert hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256']
    image=read_image(path);detector=BloodwebDetector(library,'killer','dark_lord')
    class Backend:
        def info(self):return WindowInfo(1,'DBD',Rect(0,0,1920,1080),Rect(0,0,1920,1080),96,True)
        def grab(self,rect):return image.copy(),'test_native_pixels'
        def occlusions(self,w):return []
    def no_center(*args,**kwargs):raise AssertionError('Tutorial must not search for the hidden centre')
    monkeypatch.setattr('src.core.bloodweb_detector.find_center',no_center)
    frame=ScreenManager(backend=Backend(),detector=detector).capture();result=detector.analyze_live(frame)
    assert frame.roi==Rect(0,0,1920,1080) and frame.level==2 and frame.level_confidence>=.95
    assert result.screen_state==ScreenState.TUTORIAL and result.ok_box==(686,745,101,20)
    assert not result.nodes and min(result.details['tutorial']['scores'].values())>=.96


@pytest.mark.parametrize('region',['title','body','continue'])
def test_tutorial_requires_all_three_regions(root,region):
    record=json.loads((root/'assets/ui/video_v54/manifest.json').read_text(encoding='utf-8'))['tutorial_unpredictability']
    image=read_image(root/record['source']);x,y,w,h=record['regions'][region]['box']
    image[y:y+h,x:x+w]=0  # Explicit negative injection, never a native proof.
    assert certificate(image,root,'tutorial_unpredictability') is None


def test_tutorial_rejects_moved_continue_and_plain_bloodweb(root):
    record=json.loads((root/'assets/ui/video_v54/manifest.json').read_text(encoding='utf-8'))['tutorial_unpredictability']
    image=read_image(root/record['source']);x,y,w,h=record['regions']['continue']['box']
    moved=image.copy();moved[y:y+h,x:x+w]=0;moved[y+60:y+60+h,x:x+w]=image[y:y+h,x:x+w]
    assert certificate(moved,root,'tutorial_unpredictability') is None
    assert certificate(read_image(root/'references/bloodweb/bloodweb_level43_start.png'),root,'tutorial_unpredictability') is None


def tutorial():
    result=Analysis(400,400,ScreenState.TUTORIAL,ok_box=(100,300,200,30),level=2,level_confidence=.99)
    result.details['tutorial']=dict(source_sha256='a'*64,regions=dict(title=(80,70,200,25),body=(50,130,300,60),
        **{'continue':result.ok_box}),scores=dict(title=.99,body=.99,**{'continue':.99}))
    return result


def next_web():
    result=web();result.level=2;result.level_confidence=.99
    return result


def setup(monkeypatch,results):
    clock=Clock();monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    device=Device(results);runner=engine(device,dry_run=False,purchase_mode='repeat');runner.wait=clock.advance
    original=device.capture
    def capture():
        frame=original();scene=device.results[device.last]
        return replace(frame,level=scene.level,level_confidence=scene.level_confidence)
    device.capture=capture
    return clock,device,runner


def test_one_tutorial_click_ignores_repeat_and_preserves_pending_until_fresh_web(monkeypatch):
    clock,d,r=setup(monkeypatch,[tutorial(),tutorial(),next_web(),next_web()])
    r.pending_action=True;r.persisted_scene={'pending':{'automatic':True}};r.attempted=[(1,2)]
    frame=d.capture();fresh,result,_=dismiss_tutorial(r,frame,tutorial())
    assert result.screen_state==ScreenState.BLOODWEB and d.index==4
    assert len(d.presses)==r.attempts==1 and r.actions==0 and not r.prestige_actions
    assert d.presses[0][1]==.05 and r.pending_action and r.persisted_scene and r.attempted==[(1,2)]


@pytest.mark.parametrize('cause',['dry','unapproved','probe','focus','weak','wrong_level','cta_only','budget'])
def test_unsafe_tutorial_never_clicks(monkeypatch,cause):
    clock,d,r=setup(monkeypatch,[tutorial(),tutorial()]);scene=tutorial();frame=d.capture()
    if cause=='dry':r.settings.dry_run=True
    elif cause=='unapproved':r.approved=False
    elif cause=='probe':frame=replace(frame,probe_only=True)
    elif cause=='focus':frame=replace(frame,window=replace(frame.window,foreground=False))
    elif cause=='weak':scene.details['tutorial']['scores']['title']=.95
    elif cause=='wrong_level':scene.level=3
    elif cause=='cta_only':scene.details['tutorial']['regions'].pop('body')
    elif cause=='budget':r.settings.max_input_commands=1;r.attempts=1
    with pytest.raises(ScreenError):dismiss_tutorial(r,frame,scene)
    assert not d.presses
    if cause not in ('dry','unapproved'):assert d.releases


@pytest.mark.parametrize('cause',['duplicate','stays_open','stop'])
def test_tutorial_does_not_repeat_or_count_duplicate_observations(monkeypatch,cause):
    clock,d,r=setup(monkeypatch,[tutorial()]);frame=d.capture()
    if cause=='duplicate':
        original=d.capture;d.capture=lambda:replace(original(),captured_at=frame.captured_at)
    elif cause=='stop':
        original=d.purchase
        def purchase(*args):original(*args);r.halt()
        d.purchase=purchase
    with pytest.raises(InterruptedError if cause=='stop' else ScreenError):dismiss_tutorial(r,frame,tutorial(),deadline=clock.now+2)
    assert len(d.presses)==(0 if cause=='duplicate' else 3 if cause=='stays_open' else 1) and not r.actions and d.releases


def advanced_web():
    result=next_web();result.nodes[1].x=80;result.nodes[1].y=80
    result.nodes.append(Node(2,300,300,20,NodeState.AVAILABLE,NodeType.ITEM,'second',confidence=.99,
        contour_score=.9,icon_margin=.1,geometry_confidence=.95,state_confidence=.95))
    result.edges.append((0,2));result.nodes[1].confidence=.99;result.nodes[1].contour_score=.9;result.nodes[1].icon_margin=.1
    return result


def test_center_operation_closes_tutorial_then_accepts_two_fresh_next_level_maps(monkeypatch):
    from src.core.early_unlock import purchase_level
    clock,d,r=setup(monkeypatch,[tutorial(),tutorial(),advanced_web(),advanced_web(),advanced_web()])
    initial=web();initial.level=1;initial.level_confidence=.99;initial.nodes[0].geometry_confidence=.95
    frame=replace(d.capture(),level=1,level_confidence=.99);d.index=0
    d.parking_point=lambda frame:None
    r.automatic_proof=(frame.window.signature,1,frame.to_screen(150,200),clock.now)
    _,result,_=purchase_level(r,frame,initial)
    assert result.level==2 and not r.pending_action and not r.attempted
    assert r.actions==0 and r.unconfirmed_level_actions==1
    assert len(d.presses)==r.attempts==2  # One centre command, one continuation.


def test_normal_run_continues_after_tutorial_without_another_start(monkeypatch):
    purchased=next_web();purchased.nodes[1].state=NodeState.PURCHASED
    clock,d,r=setup(monkeypatch,[tutorial(),tutorial(),next_web(),next_web(),next_web(),purchased,purchased])
    r.settings.max_actions=1;r.settings.max_input_commands=4
    r.run()
    assert r.actions==1 and r.attempts==len(d.presses)==2 and not r.pending_action
    assert sum(e['stage']=='modal_input' for e in r.report.entries)==1
    assert any(e['stage']=='tutorial_dismissed' for e in r.report.entries)


def test_dry_run_on_tutorial_sends_no_input(monkeypatch):
    clock,d,r=setup(monkeypatch,[tutorial()]);r.settings.dry_run=True;r.run()
    assert not d.presses and not r.actions and not r.attempts


def test_wait_for_next_level_closes_tutorial_after_completed_old_web(monkeypatch):
    clock,d,r=setup(monkeypatch,[tutorial(),tutorial(),advanced_web(),advanced_web(),advanced_web()])
    initial=web(NodeState.PURCHASED);initial.level=1;initial.level_confidence=.99
    frame=replace(d.capture(),level=1,level_confidence=.99);d.index=0
    _,result,_=r.wait_next_bloodweb(frame,initial)
    assert result.level==2 and len(d.presses)==r.attempts==1 and r.actions==0


def test_last_single_purchase_can_transition_through_tutorial_without_false_item_credit(monkeypatch):
    initial=web();initial.level=1;initial.level_confidence=.99
    clock,d,r=setup(monkeypatch,[tutorial(),tutorial(),tutorial(),advanced_web(),advanced_web(),advanced_web()])
    r.settings.purchase_mode='hold';r.settings.purchase_timeout=8
    frame=replace(d.capture(),level=1,level_confidence=.99);d.index=0
    _,result,_=r.buy(frame,initial,node=initial.nodes[1])
    assert result.level==2 and r.actions==0 and r.unconfirmed_level_actions==1 and not r.pending_action
    assert len(d.presses)==r.attempts==2  # One purchase input, one UI continuation.


@pytest.mark.parametrize('negative',[None,'cta_only','weak','duplicate'])
def test_read_only_start_preparation_can_start_on_the_certified_tutorial(app,library,tmp_path,monkeypatch,negative):
    from types import SimpleNamespace
    from src.gui.main_window import MainWindow
    from src.gui.session_check_dialog import SessionCheckDialog
    class Keys:
        ready=True
        def __init__(self,*a,**k):pass
        def close(self):pass
        def native_health(self):return dict(ready=True,physical_key_verified=False)
    monkeypatch.setattr('src.gui.main_window.GlobalHotkeys',Keys)
    owner=MainWindow(library,tmp_path/'preferences.json');owner.physical_f9_check.setChecked(False)
    dialog=SessionCheckDialog(owner,auto_start=True);result=tutorial();device=Device([result]);frame=device.capture()
    frame=replace(frame,level=2,level_confidence=.99);result.live_frame=frame;validations=[]
    result.capture_manager=SimpleNamespace(validate_click=lambda *a,**k:validations.append((a,k)))
    if negative=='cta_only':result.details['tutorial']['regions'].pop('body')
    elif negative=='weak':result.details['tutorial']['scores']['title']=.95
    try:
        dialog.checked_result(result)
        if negative in ('cta_only','weak'):
            assert not dialog.succeeded and not validations
        else:
            assert dialog.receipt and not dialog.succeeded
            second=copy.copy(result);second.live_frame=frame if negative=='duplicate' else replace(frame,captured_at=frame.captured_at+.1)
            dialog.checked_result(second)
            assert dialog.succeeded==(negative is None)
        assert owner.auto_worker is None and not device.presses
    finally:dialog.close();owner.close()


@pytest.mark.parametrize('negative',[None,'single','chain','same_level','unknown_ids','old_geometry','duplicate'])
def test_saved_automatic_scene_requires_two_strict_next_level_proofs(monkeypatch,negative):
    clock,d,r=setup(monkeypatch,[advanced_web()]);frame=d.capture()
    initial=web();initial.level=1;initial.level_confidence=.99
    pending=dict(point=list(frame.to_screen(150,200)),radius=20,icon=None,automatic=True)
    r.persisted_scene=record_scene(frame,initial,[(1,2)],pending);r.attempted=[(1,2)]
    scene=advanced_web()
    if negative=='single':pending.pop('automatic')
    elif negative=='chain':pending['chain']={'physical_points':[(1,2),(3,4)]}
    elif negative=='same_level':scene.level=1
    elif negative=='unknown_ids':scene.nodes[1].icon_id=None
    elif negative=='old_geometry':scene.nodes=copy.deepcopy(initial.nodes);scene.nodes[1].confidence=.99
    assert not r.recheck_saved_scene(frame,scene)
    if negative=='duplicate':
        assert not r.recheck_saved_scene(frame,scene) and r.persisted_scene;return
    later=replace(frame,captured_at=frame.captured_at+.1)
    if negative:
        with pytest.raises(ScreenError):r.recheck_saved_scene(later,scene)
        assert r.persisted_scene and r.attempted and r.actions==0
    else:
        assert r.recheck_saved_scene(later,scene) and not r.attempted and r.persisted_scene is None
        assert r.actions==0 and r.unconfirmed_level_actions==1 and not d.presses
