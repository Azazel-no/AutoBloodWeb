"""Safety, abstract targets and bilingual GUI; no real game input."""
import copy,json,time
from types import SimpleNamespace
import cv2,numpy as np,pytest
from PySide6.QtCore import Qt
from tests.test_gui import app,isolate_preferences_and_hotkeys
from tests.test_automation import Device,web,engine
from src.core.automation import Settings,State
from src.core.models import Node,NodeState,NodeType,Analysis,ScreenState
from src.core.node_category import classify_category
from src.core.path_planner import plan_route
from src.core.priority_targets import EVENT_TARGET
from src.core.preferences import Preferences
from src.gui.main_window import MainWindow


def test_continuous_has_no_action_runtime_cap_but_requires_explicit_mode():
    Settings(run_mode='continuous',max_actions=None,max_runtime=None).validate()
    with pytest.raises(ValueError):Settings(max_actions=None,max_runtime=None).validate()
    with pytest.raises(ValueError):Settings(max_consecutive_errors=0).validate()


def test_continuous_exceeds_five_and_stop_releases():
    graph=web();graph.nodes=graph.nodes[:1];graph.edges=[]
    for i in range(1,8):
        graph.nodes.append(Node(i,230,40+i*40,20,NodeState.AVAILABLE,NodeType.ITEM,'target'+str(i),state_confidence=.95,geometry_confidence=.95))
        graph.edges.append((0,i))
    class Progress(Device):
        def analyze(self,image,source):return copy.deepcopy(graph)
        def purchase(self,point,duration,stop,check):
            super().purchase(point,duration,stop,check)
            node=next(n for n in graph.nodes if (n.x-400,n.y)==point);node.state=NodeState.PURCHASED
    device=Progress([graph]);runner=engine(device,dry_run=False,max_actions=None,max_runtime=None,run_mode='continuous')
    runner.event=lambda state,*args:runner.halt() if state==State.WAIT_REFRESH else None
    runner.run();assert runner.actions==7 and len(device.presses)==7 and device.releases
    assert runner.state==State.STOPPED


def test_completed_same_layout_does_not_clear_attempted_point():
    device=Device([web(),web(),web(NodeState.PURCHASED),web(NodeState.PURCHASED),
        web(NodeState.PURCHASED),web(NodeState.PURCHASED),web(),web()])
    runner=engine(device,dry_run=False,max_actions=2);runner.run()
    assert len(device.presses)==1 and any(e.get('reason')=='next_bloodweb_timeout' for e in runner.report.entries)


def test_one_refresh_then_same_web_cannot_confirm_purchase():
    device=Device([web(),web(),Analysis(400,400,ScreenState.REFRESH),web(),web()])
    runner=engine(device,dry_run=False);runner.settings.purchase_timeout=.2;runner.run()
    assert runner.actions==0 and len(device.presses)==1


def test_recovery_processing_does_not_add_full_sleep(monkeypatch):
    device=Device([web()]);runner=engine(device,dry_run=False)
    clock=[100.];monkeypatch.setattr(time,'monotonic',lambda:clock[0]);waits=[];calls=[0]
    def read():
        runner.last_capture_at=clock[0];clock[0]+=.5;calls[0]+=1
        if calls[0]==1:raise __import__('src.core.screen_manager',fromlist=['ScreenError']).ScreenError('item_tooltip')
        return device.capture(),web(),None
    runner.read=read;runner.wait=lambda seconds:waits.append(seconds)
    runner.recover_read();assert waits==[0]


def test_consecutive_errors_are_bounded_and_stop_sends_no_input():
    device=Device([Analysis(400,400,ScreenState.UNKNOWN)])
    runner=engine(device,dry_run=False);runner.settings.max_consecutive_errors=3;runner.run()
    assert device.index==3 and not device.presses and device.releases
    assert any(e.get('detail','').startswith('consecutive_errors:') for e in runner.report.entries)


def test_focus_loss_on_read_stops_before_action():
    from dataclasses import replace
    device=Device([web()]);device.window=replace(device.window,foreground=False)
    runner=engine(device,dry_run=False);runner.run()
    assert not device.presses and any(e.get('reason')=='game_focus_lost' for e in runner.report.entries)


@pytest.mark.parametrize('shape',['hex','square','diamond','rim','plain'])
def test_orange_requires_offering_shape(shape):
    # Geometry-only test masks are not game references or library assets.
    image=np.zeros((101,101,3),np.uint8);color=tuple(int(v) for v in cv2.cvtColor(np.uint8([[[20,240,200]]]),cv2.COLOR_HSV2BGR)[0,0])
    if shape=='hex':
        pts=np.int32([[50+36*np.cos(a),50+36*np.sin(a)] for a in np.arange(6)*np.pi/3]);cv2.fillConvexPoly(image,pts,color)
    elif shape=='square':cv2.rectangle(image,(20,20),(80,80),color,-1)
    elif shape=='diamond':cv2.fillConvexPoly(image,np.int32([[50,12],[88,50],[50,88],[12,50]]),color)
    elif shape=='rim':cv2.circle(image,(50,50),45,color,5)
    else:image[:]=color
    evidence=classify_category(image,50)
    assert evidence.event_offering==(shape=='hex')


def test_event_priority_does_not_change_concrete_identity_or_choose_uncertain():
    result=web();node=result.nodes[1];node.event_offering=True;node.event_confidence=.95
    node.category_hint='offering';node.type=NodeType.OFFERING
    for mode in ('smart','strict'):
        plan=plan_route(result,[EVENT_TARGET],mode);assert plan.next_node==1 and plan.target_icon==EVENT_TARGET
        assert node.icon_id=='target'
    node.event_confidence=.8;assert plan_route(result,[EVENT_TARGET],'strict').target_icon!=EVENT_TARGET
    node.event_confidence=.95;node.type=NodeType.PERK
    assert plan_route(result,[EVENT_TARGET],'strict').target_icon!=EVENT_TARGET


def test_event_priority_persists_for_both_roles_and_killers(tmp_path,library):
    path=tmp_path/'preferences.json';prefs=Preferences(path,library.entries)
    prefs.priorities['survivor']=[EVENT_TARGET];prefs.priorities['killer']=[EVENT_TARGET]
    prefs.activate_character('nurse');prefs.priorities['killer']=[EVENT_TARGET];prefs.save('ru','killer')
    loaded=Preferences(path,library.entries)
    assert loaded.priorities['survivor']==loaded.priorities['killer']==[]
    assert EVENT_TARGET in loaded.data['retired_priority_targets']
    loaded.activate_character('blight');assert loaded.priorities['killer']==[]
    assert EVENT_TARGET not in {e.id for e in library.entries}


@pytest.mark.parametrize('query,character',[('Nurse','nurse'),('медсест','nurse'),('BLIGHT','blight'),('Мор','blight'),('huntress','huntress'),('охотниц','huntress')])
def test_killer_search_is_bilingual_and_keeps_id_until_selected(app,library,query,character):
    window=MainWindow(library);window.role.setCurrentIndex(1);window.show();app.processEvents()
    before=window.character.currentData();window.character.showPopup();window.character.search_line.setText(query);app.processEvents()
    visible=[window.character.itemData(window.character.search_results.item(i).data(Qt.UserRole)) for i in range(window.character.search_results.count()) if not window.character.search_results.item(i).isHidden()]
    assert character in visible and window.character.currentData()==before
    window.character.popup.close();window.close()


def test_modes_counter_role_icon_and_no_recalibration_on_priorities(app,library):
    window=MainWindow(library);window.calibration=dict(verified=True,user_confirmed=True,mode='hold',hold_seconds=.65)
    window.run_mode.setCurrentIndex(1);window.toggle_icon('any_perk')
    assert window.calibration['user_confirmed'] and not window.role.itemIcon(1).isNull()
    window.automation_update(State.SCANNING,'prepare_countdown',{'seconds':5})
    assert window.countdown.text()=='5' and not window.countdown.isHidden()
    window.automation_update(State.SCANNING,'waiting_game_focus',None);assert window.countdown.isHidden()
    window.close();window=MainWindow(library);assert window.run_mode.currentData()=='continuous';window.close()


def test_bad_center_hint_falls_back_to_full_search(root):
    from src.core.bloodweb_detector import find_center
    from src.utils.images import read_image
    image=read_image(root/'tests/fixtures/v43/raw_1791793636036233900.png')
    full=find_center(image)
    assert find_center(image,hint=(170,650,40))==full
    hint=find_center(image,hint=full)
    assert np.hypot(hint[0]-full[0],hint[1]-full[1])<3


def test_geometry_reuse_requires_exact_channel_and_can_be_invalidated(root,library):
    from src.core.bloodweb_detector import BloodwebDetector
    from src.utils.images import read_image
    detector=BloodwebDetector(library);image=read_image(root/'references/bloodweb/bloodweb_level43_start.png')
    detector.analyze(image,'live_game');repeat=detector.analyze(image,'live_game')
    assert repeat.details['geometry_channels_reused']==2
    detector.invalidate();fresh=detector.analyze(image,'live_game')
    assert fresh.details['geometry_channels_reused']==0 and fresh.details['analysis_mode']=='full'
    assert [(n.x,n.y,n.state,n.icon_id) for n in repeat.nodes]==[(n.x,n.y,n.state,n.icon_id) for n in fresh.nodes]


def test_explicit_recalibration_cannot_reuse_old_purchase(app,library):
    window=MainWindow(library);window.calibration=dict(verified=True,user_confirmed=True,mode='hold',hold_seconds=.65)
    window.open_calibration();assert window.calibration is None
    assert not window.calibration_wizard.confirm.isEnabled()
    window.calibration_wizard.close();window.close()


def test_five_visually_reviewed_missing_addons_and_blue_perk(root,library):
    import hashlib
    from src.utils.images import read_image
    from src.core.bloodweb_detector import BloodwebDetector
    review=json.loads((root/'assets/reference_versions/v45_review.json').read_text(encoding='utf-8'))
    path=root/review['source'];assert hashlib.sha256(path.read_bytes()).hexdigest()==review['sha256']
    result=BloodwebDetector(library).analyze(read_image(path)[140:983,262:1105])
    for case in review['proposals']:
        node=min(result.nodes,key=lambda n:np.hypot(n.x-case['x'],n.y-case['y']))
        assert node.icon_id==case['new'] and node.type==NodeType.ADDON
    perk=min(result.nodes,key=lambda n:np.hypot(n.x-(995-262),n.y-(387-140)))
    assert perk.type==NodeType.PERK and perk.icon_id is None and perk.category_hint=='perk'


@pytest.mark.parametrize('hue',[55,105,145])
def test_colored_perk_diamond_is_not_item_square(hue):
    from src.core.bloodweb_detector import BloodwebDetector
    color=tuple(int(v) for v in cv2.cvtColor(np.uint8([[[hue,180,100]]]),cv2.COLOR_HSV2BGR)[0,0])
    diamond=np.zeros((81,81,3),np.uint8);cv2.fillConvexPoly(diamond,np.int32([[40,6],[74,40],[40,74],[6,40]]),color)
    square=np.zeros_like(diamond);cv2.rectangle(square,(15,15),(65,65),color,-1)
    assert BloodwebDetector._is_perk(diamond,40) and not BloodwebDetector._is_perk(square,40)


def test_continuous_stops_on_known_zero_bloodpoints():
    class Empty(Device):
        def capture(self):
            frame=super().capture();frame.bloodpoints=0;return frame
    device=Empty([web()]);runner=engine(device,dry_run=False,max_actions=None,max_runtime=None,run_mode='continuous',bloodpoint_checks=True);runner.run()
    assert runner.state==State.INSUFFICIENT_BP and not device.presses


def test_saved_live_choice_does_not_arm_without_new_f9(app,library):
    window=MainWindow(library);window.dry_run.setChecked(False);window.close()
    window=MainWindow(library);assert not window.dry_run.isChecked()
    window.start_automation();assert window.auto_worker is None
    assert window.emergency_tested_signature is None;window.close()


def test_event_goal_keeps_specific_priority_rank_and_blocks_unknown_orange():
    result=web();n=result.nodes[1];n.type=NodeType.OFFERING;n.category_hint='offering'
    n.event_offering=True;n.event_confidence=.95;n.orange_background=True
    assert plan_route(result,['target',EVENT_TARGET],'strict').target_icon=='target'
    assert plan_route(result,[EVENT_TARGET,'target'],'strict').target_icon==EVENT_TARGET
    n.icon_id=None;n.event_offering=False;n.category_hint='unknown'
    assert plan_route(result,[EVENT_TARGET],'strict').next_node is None
    assert n.state==NodeState.AVAILABLE
    assert plan_route(result,[],'strict').next_node==1


def test_identity_glyph_and_rarity_are_separate_evidence(root,library):
    from src.core.bloodweb_detector import BloodwebDetector
    from src.utils.images import read_image
    result=BloodwebDetector(library).analyze(read_image(root/'references/bloodweb/bloodweb_level43_start.png'))
    accepted=[n for n in result.nodes if n.icon_id]
    assert accepted and all(n.confidence==n.glyph_score for n in accepted)
    assert any(n.rarity_tie_break for n in accepted)
    assert all(0<=n.rarity_confidence<=1 for n in result.nodes)
