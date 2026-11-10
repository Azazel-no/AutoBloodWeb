import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop, QTimer

from src.gui.main_window import MainWindow


def test_final_worker_restore_cannot_reopen_closed_product(app,library):
    from src.core.automation import State
    window=MainWindow(library);window.show();app.processEvents()
    window.close();app.processEvents()
    window.automation_update(State.STOPPED,'restore_window',None)
    app.processEvents()
    assert not window.isVisible() and window._closing


def test_visible_progress_counts_confirmed_nodes_instead_of_input_attempts(app,library):
    from tests.test_automation import Device,engine,web
    from src.core.path_planner import plan_route
    window=MainWindow(library);d=Device([web()]);e=engine(d,dry_run=False)
    result=web();result.level=16;e.actions=2;e.attempts=3
    e.recent_frames.append((d.capture(),result,plan_route(result,['any_perk'])))
    window.update_runtime_summary(e)
    assert window.locale.text('runtime_progress',done=0,limit='∞',speed=120)==window.runtime_summary.text()


def test_direct_burst_rejection_stays_visible_until_ready_without_changing_run_state(app,library):
    from src.core.automation import State
    window=MainWindow(library);before=window.status_key
    window.automation_update(State.READY,'direct_burst_fallback',dict(level=35,node=12,reason='target_geometry_unproven'))
    assert '35' in window.diagnostics.text() and '12' in window.diagnostics.text()
    assert window.locale.text('direct_burst_reason_target_geometry_unproven') in window.diagnostics.text()
    assert window.diagnostics.text() in window.turbo_burst.toolTip() and window.status_key==before
    window.automation_update(State.READY,'direct_burst_ready',dict(level=35))
    assert window.direct_burst_notice is None and window.turbo_burst.toolTip()==window.locale.text('turbo_burst_hint')


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolate_preferences_and_hotkeys(monkeypatch,tmp_path):
    original=MainWindow.__init__
    monkeypatch.setattr(MainWindow,'__init__',lambda self,library:original(self,library,tmp_path/'preferences.json'))
    class Hotkeys:
        ready=True
        def __init__(self,callback,bindings=None): pass
        def close(self): pass
    monkeypatch.setattr('src.gui.main_window.GlobalHotkeys',Hotkeys)
    yield
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget,MainWindow): widget.close()


def wait_for_worker(window,app):
    loop=QEventLoop()
    timer=QTimer(); timer.setSingleShot(True); timer.timeout.connect(loop.quit)
    window.worker.finished.connect(loop.quit)
    timer.start(15000); loop.exec(); timer.stop(); app.processEvents()
    assert window.worker is None


@pytest.mark.parametrize('health',[None,False,True])
def test_start_after_mode_change_uses_current_stop_health_without_test_purchase(app,library,monkeypatch,health):
    from types import SimpleNamespace
    from tests.test_automation import Device,web
    from src.core.automation import Automation
    window=MainWindow(library);window.purchase_mode.setCurrentIndex(window.purchase_mode.findData('repeat'))
    window.dry_run.setChecked(False)
    previous=dict(mode='hold',verified=True,user_confirmed=True,hold_seconds=.25)
    window.prefs.data['calibration']=dict(previous);window.calibration=None
    created=[];device=Device([web()]);device.backend=SimpleNamespace(info=lambda:device.window)
    def factory(library,role,priorities,settings,approved,hotkeys):
        created.append(Automation(device,device,device,priorities,settings,approved=approved,hotkeys_ready=hotkeys));return created[-1]
    monkeypatch.setattr('src.gui.main_window.make_engine',factory)
    monkeypatch.setattr('src.gui.main_window.AutomationWorker.start',lambda self:None)
    if health is not None:window.hotkeys.native_health=lambda:dict(ready=health)
    window.start_automation();app.processEvents()
    assert window.calibration_wizard is None and window.session_check is None and not device.presses
    if health:
        assert len(created)==1 and created[0].approved and created[0].settings.startup_delay==3
    else:
        assert not created and window.status_key=='paused'
        assert window.error_message==window.locale.text('hotkey_registration_failed')
    assert window.prefs.data['calibration']==previous
    window.auto_worker=None;window.close()


@pytest.mark.parametrize('gate',['armed','no_f9','no_opt_in','wrong_selection'])
def test_calibration_window_context_survives_invalidation_only_from_armed_wizard(app,library,detector,monkeypatch,gate):
    import time
    from tests.test_automation import Device,web
    from src.core.automation import Automation
    result=web();device=Device([result]);result.live_frame=device.capture()
    result.capture_manager=device;result.raw_image=result.live_frame.image;result.renderer=detector
    created=[]
    def factory(library,role,priorities,settings,approved,hotkeys):
        engine=Automation(device,device,device,priorities,settings,approved=approved,hotkeys_ready=hotkeys)
        created.append(engine);return engine
    monkeypatch.setattr('src.gui.main_window.make_engine',factory)
    monkeypatch.setattr('src.gui.main_window.AutomationWorker.start',lambda self:None)
    window=MainWindow(library);window.dry_run.setChecked(False);window.open_calibration()
    wizard=window.calibration_wizard;wizard.checked_result(result)
    wizard.ack.setChecked(gate!='no_opt_in')
    if gate!='no_f9':window.emergency_tested_signature=result.live_frame.window.signature
    window.calibration_selection=(wizard.target.x,wizard.target.y,result.live_frame.window.signature,result.live_frame.roi)
    if gate=='wrong_selection':window.calibration_selection=(999,999,*window.calibration_selection[2:])
    started=time.monotonic();window.start_automation(calibrate=True)
    try:
        engine=created[0]
        assert not engine.approved and window.emergency_tested_signature is None
        if gate=='armed':
            assert engine.calibration_window_proof['target']==engine.calibration_target
            assert started<=engine.calibration_window_proof['created']<=time.monotonic()
        else:assert engine.calibration_window_proof is None
        assert not device.presses and engine.attempts==0
    finally:window.auto_worker=None;wizard.close();window.close()


def test_real_gui_workflow(app,library,root,monkeypatch):
    # Preferences of the actual user are never overwritten by this test.
    monkeypatch.setattr(MainWindow,'save_preferences',lambda self:None)
    window=MainWindow(library); window.show(); app.processEvents()
    window.locale.language='ru'; window.language.setCurrentIndex(0); window.clear_selection()
    ident='survivor_offering_044'
    window.toggle_icon(ident)
    assert ident in window.active_selected
    assert window.icon_grid.buttons[ident].isChecked()
    window.start_analysis(root/'references/bloodweb/bloodweb_level43_start.png')
    wait_for_worker(window,app)
    assert window.last_analysis is not None
    assert window.counter_values['nodes'].text()=='20'
    assert window.counter_values['targets'].text()=='1'
    assert not window.preview.original.isNull()
    assert window.table.rowCount()==20
    assert window.buttons['start'].isEnabled() and not window.dry_run.isChecked()  # release UI: a normal Start buys
    window.language.setCurrentIndex(1); app.processEvents()
    assert window.test_button.text()=='Open Image'
    assert window.scan_button.text()=='Scan Game'
    assert window.counter_labels['nodes'].text()=='Nodes'
    assert 'F9' in window.mode_label.text()
    (root/'debug/tests').mkdir(exist_ok=True,parents=True)
    assert window.grab().save(str(root/'debug/tests/gui_en.png'))
    window.language.setCurrentIndex(0); app.processEvents()
    assert window.grab().save(str(root/'debug/tests/gui_ru.png'))
    # Check that diagnostics can be viewed by scrolling the actual GUI.
    from PySide6.QtWidgets import QScrollArea
    window.debug_title.setChecked(True); app.processEvents()
    body=next(s for s in window.findChildren(QScrollArea) if s not in (window.grid_scroll,window.selected_scroll))
    body.verticalScrollBar().setValue(body.verticalScrollBar().maximum()); app.processEvents()
    assert window.grab().save(str(root/'debug/tests/gui_debug.png'))
    window.role.setCurrentIndex(1); app.processEvents()
    assert len(window.icon_grid.entries)==len(library.filter('killer'))+1 and window.character.isVisible()
    assert 'event_offering_orange' not in window.icon_grid.buttons and 'any_perk' in window.icon_grid.buttons
    assert not window.active_selected
    window.role.setCurrentIndex(0); app.processEvents()
    assert ident in window.active_selected
    window.clear_selection(); window.close(); app.processEvents()


def test_missing_background_still_opens(app,library,monkeypatch):
    from src.gui.widgets import Background
    monkeypatch.setattr(MainWindow,'save_preferences',lambda self:None)
    background=Background(library.root/'debug/nonexistent')
    background.resize(420,700); background.show(); app.processEvents()
    assert not background.grab().isNull()
    background.close()


def test_missing_or_covered_game_stays_safe(app,library,monkeypatch):
    from src.core.capture import GameNotFoundError
    monkeypatch.setattr(MainWindow,'save_preferences',lambda self:None)
    window=MainWindow(library); window.show()
    for message in ('game_not_found','game_occluded'):
        def capture(self): raise GameNotFoundError(message)
        monkeypatch.setattr('src.gui.main_window.ScreenManager.capture',capture)
        window.start_analysis(None); wait_for_worker(window,app)
        assert window.status_key=='error' and window.last_analysis is None
        assert window.buttons['start'].isEnabled() and not window.dry_run.isChecked()
    window.close()


def test_priority_up_down_drag_model_and_real_purchase_gate(app,library):
    from PySide6.QtCore import QModelIndex,Qt
    window=MainWindow(library)
    first,second='survivor_item_003','survivor_item_001'
    window.toggle_icon(first); window.toggle_icon(second)
    window.priority_list.setCurrentRow(1); window.priority_list.move_current(-1)
    assert window.active_selected==[second,first]
    assert window.priority_list.item(0).text()=='1.'
    # InternalMove uses the same model operation as an actual drag-and-drop.
    assert window.priority_list.model().moveRows(QModelIndex(),0,1,QModelIndex(),2)
    assert window.active_selected==[first,second]
    window.save_preferences(); path=window.preferences_path
    window.dry_run.setChecked(False); window.start_automation()
    assert window.auto_worker is None and window.status_key=='paused'
    window.close()
    reopened=MainWindow(library)
    assert reopened.active_selected==[first,second] and not reopened.dry_run.isChecked()
    assert reopened.auto_worker is None and reopened.emergency_tested_signature is None
    reopened.close()


def test_preview_stays_in_panel(app,root):
    from src.gui.widgets import Preview
    preview=Preview(); preview.resize(280,180); preview.show()
    preview.set_image(root/'references/bloodweb/bloodweb_level43_start.png'); app.processEvents()
    pixmap=preview.pixmap()
    assert pixmap.width()<=preview.width() and pixmap.height()<=preview.height()
    assert abs(pixmap.width()/pixmap.height()-916/840)<.02
    preview.close()


def test_new_calibration_clears_previous_purchase_approval(app,library,monkeypatch):
    window=MainWindow(library)
    window.calibration={'verified':True,'user_confirmed':True}
    def unavailable_engine(*args): raise RuntimeError('device_unavailable')
    monkeypatch.setattr('src.gui.main_window.make_engine',unavailable_engine)
    with pytest.raises(RuntimeError,match='device_unavailable'):
        window.start_automation(calibrate=True)
    assert window.calibration is None and not window.confirm_button.isEnabled()
    window.dry_run.setChecked(False); window.start_automation()
    assert window.auto_worker is None and window.status_key=='paused'
    window.close()


def test_failure_detail_survives_restore_and_calibration_stays_disabled(app,library):
    from src.core.automation import State
    window=MainWindow(library)
    window.automation_update(State.PAUSED,'sendinput_failed',{'error_detail':'SendInput Win32=5'})
    window.automation_update(State.PAUSED,'restore_window',None)
    assert 'Win32=5' in window.error_message and not window.confirm_button.isEnabled()
    window.close()


def test_calibration_wizard_never_arms_without_game_and_emergency_test(app,library):
    window=MainWindow(library); window.show(); window.open_calibration(); app.processEvents()
    wizard=window.calibration_wizard
    assert wizard.delay.value()>=5 and not wizard.begin.isEnabled() and not wizard.confirm.isEnabled()
    wizard.ack.setChecked(True)
    assert not wizard.begin.isEnabled()
    wizard.start_purchase(); assert window.auto_worker is None
    wizard.close(); window.close()


def test_wizard_one_purchase_and_confirmation_with_fake_devices(app,library,detector,monkeypatch):
    # Only injected frames and mouse calls; no cursor movement or OS input.
    import copy
    from tests.test_automation import Device,web
    from src.core.automation import Automation
    first=web(); first.nodes[1].icon_id='survivor_item_003'
    from src.core.models import NodeState
    after=copy.deepcopy(first); after.nodes[1].state=NodeState.PURCHASED
    device=Device([first,first,after,after]); device.save_debug=detector.save_debug
    frame=device.capture(); device.index=0
    result=copy.deepcopy(first); result.live_frame=frame; result.raw_image=frame.image
    result.renderer=detector; result.capture_manager=device
    def factory(library,role,priorities,settings,approved,hotkeys):
        settings.startup_delay=0; settings.poll_interval=.01
        return Automation(device,device,device,priorities,settings,approved=approved,hotkeys_ready=hotkeys)
    monkeypatch.setattr('src.gui.main_window.make_engine',factory)
    window=MainWindow(library); window.show(); window.open_calibration()
    wizard=window.calibration_wizard; wizard.checked_result(result); wizard.ack.setChecked(True)
    wizard.emergency_observed(frame.window.signature)
    assert wizard.begin.isEnabled() and not wizard.confirm.isEnabled()
    window.dry_run.setChecked(True)
    assert not wizard.begin.isEnabled()
    assert 'Dry Run' in wizard.status.text()
    wizard.start_purchase(); assert window.auto_worker is None and not device.presses
    window.dry_run.setChecked(False)
    assert wizard.begin.isEnabled()
    window.hotkeys.ready=False; window.hotkeys.error_detail='RegisterHotKey Win32=1409'
    wizard.refresh_gate()
    assert not wizard.begin.isEnabled() and 'Win32=1409' in wizard.status.text()
    window.hotkeys.ready=True; wizard.refresh_gate()
    wizard.start_purchase()
    loop=QEventLoop(); window.auto_worker.finished.connect(loop.quit); QTimer.singleShot(5000,loop.quit); loop.exec(); app.processEvents()
    assert window.auto_worker is None and len(device.presses)==1
    assert wizard.confirm.isEnabled() and window.calibration['verified']
    assert not wizard.begin.isEnabled()  # Cannot reuse an old frame.
    wizard.confirm_purchase(); assert window.calibration['user_confirmed']
    assert window.calibration_wizard is None and window.auto_worker is None
    wizard.close(); window.close()


def test_wizard_layout_on_user_screenshot_and_localization(app,library,root,detector):
    import time
    from src.core.screen_manager import CapturedFrame,WindowInfo,Rect
    from src.utils.images import read_image
    image=read_image(root/'references/bloodweb/bloodweb_level43_start.png'); result=detector.analyze(image)
    # This is a GUI layout test, explicitly not a live DBD capture.
    result.live_frame=CapturedFrame(image,WindowInfo(1,'Test frame',Rect(0,0,916,840),Rect(0,0,1920,1080)),Rect(0,0,916,840),time.monotonic())
    result.raw_image=image; result.renderer=detector
    window=MainWindow(library); window.show(); window.open_calibration(); wizard=window.calibration_wizard
    wizard.checked_result(result); app.processEvents(); app.processEvents()
    assert wizard.preview.geometry().bottom()<wizard.coordinates.geometry().top()
    assert wizard.coordinates.geometry().bottom()<wizard.emergency.geometry().top()
    output=root/'debug/v3/gui'; output.mkdir(parents=True,exist_ok=True)
    assert wizard.grab().save(str(output/'wizard_ru.png'))
    assert wizard.width()<=window.width() and wizard.height()<window.height()
    wizard.close(); window.language.setCurrentIndex(1); window.open_calibration()
    wizard=window.calibration_wizard; wizard.checked_result(result); app.processEvents(); app.processEvents()
    assert wizard.grab().save(str(output/'wizard_en.png'))
    assert 'one real purchase' in wizard.begin.text()
    wizard.close(); window.close()


def test_failed_scan_cannot_leave_stale_dry_run_message(app,library,detector):
    import json
    from tests.test_automation import Device,web
    result=web(); device=Device([result]); result.live_frame=device.capture()
    result.raw_image=result.live_frame.image; result.renderer=detector
    window=MainWindow(library); window.show(); window.open_calibration()
    wizard=window.calibration_wizard; wizard.checked_result(result)
    assert not wizard.ack.isChecked() and not window.dry_run.isChecked()
    wizard.failed('bloodweb_occluded')
    wizard.ack.setChecked(True)
    assert not window.dry_run.isChecked() and not wizard.begin.isEnabled()
    assert wizard.status.text()==window.locale.text('bloodweb_occluded')
    wizard.copy_report(); packet=json.loads(QApplication.clipboard().text())
    assert packet['gate']['reason']=='bloodweb_occluded'
    assert packet['gate']['opt_in'] and not packet['gate']['dry_run']
    assert not packet['gate']['live_frame'] and not packet['entries']
    wizard.scan_error=''; window.error_message='SendInput Win32=5'; wizard.finished()
    assert wizard.status.text()=='SendInput Win32=5'
    assert wizard.gate_snapshot()['reason']=='wizard_attempt_failed'
    wizard.close(); window.close()


def test_emergency_test_uses_focus_at_hotkey_receipt_not_later_qt_delivery(app,library,detector):
    import threading
    from tests.test_automation import Device,web
    result=web(); device=Device([result]); result.live_frame=device.capture()
    result.raw_image=result.live_frame.image; result.renderer=detector
    window=MainWindow(library); window.show(); window.open_calibration()
    wizard=window.calibration_wizard; wizard.checked_result(result); wizard.ack.setChecked(True)
    wizard.start_emergency_test()
    window.get_foreground_hwnd=lambda:999
    thread=threading.Thread(target=lambda:window.hotkey_received('stop')); thread.start(); thread.join()
    app.processEvents()
    assert not wizard.begin.isEnabled() and not wizard.gate_snapshot()['emergency_tested']
    window.get_foreground_hwnd=lambda:result.live_frame.window.hwnd
    thread=threading.Thread(target=lambda:window.hotkey_received('stop')); thread.start(); thread.join()
    # User switched back to AutoBloodWeb before Qt processed the queued signal.
    window.get_foreground_hwnd=lambda:999
    app.processEvents()
    assert wizard.begin.isEnabled() and wizard.gate_snapshot()['emergency_tested']
    assert not device.presses
    wizard.close(); window.close()


def test_dry_run_route_message_is_localized_with_node(app,library,root,detector):
    from types import SimpleNamespace
    import time
    from src.core.automation import State
    from src.core.path_planner import plan_route
    from src.core.screen_manager import CapturedFrame,WindowInfo,Rect
    from src.utils.images import read_image
    image=read_image(root/'references/bloodweb/bloodweb_level43_start.png')
    result=detector.analyze(image); plan=plan_route(result,[])
    frame=CapturedFrame(image,WindowInfo(1,'DBD',Rect(0,0,916,840),Rect(0,0,1920,1080)),Rect(0,0,916,840),time.monotonic())
    window=MainWindow(library)
    window.auto_worker=SimpleNamespace(engine=SimpleNamespace(detector=detector,settings=SimpleNamespace(dry_run=True)))
    try:
        window.automation_update(State.DRY_RUN,'filler',(frame,result,plan))
        assert window.status_key=='dry_run' and str(plan.next_node) in window.error_message
        assert window.last_analysis is result and not window.main_preview.original.isNull()
    finally: window.auto_worker=None; window.close()


def test_each_full_start_checks_live_stop_without_physical_f9_or_manual_scan(app,library,monkeypatch):
    from types import SimpleNamespace
    from tests.test_automation import Device,web
    from src.core.models import NodeState
    from src.core.automation import Automation
    class FullDevice(Device):
        def validate_click(self,frame,x,y,**kwargs):return super().validate_click(frame,x,y)
    scenes=[web(),web(),web(NodeState.PURCHASED),web(NodeState.PURCHASED)]
    for scene in scenes:scene.nodes[1].icon_id='survivor_item_003'
    device=FullDevice(scenes)
    device.backend=SimpleNamespace(info=lambda:device.window)
    def factory(library,role,priorities,settings,approved,hotkeys):
        settings.startup_delay=0;settings.poll_interval=.01;settings.max_actions=1
        return Automation(device,device,device,priorities,settings,approved=approved,hotkeys_ready=hotkeys)
    monkeypatch.setattr('src.gui.main_window.make_engine',factory)
    window=MainWindow(library);window.calibration=dict(verified=True,user_confirmed=True)
    window.hotkeys.native_health=lambda:dict(ready=True)
    window.calibration_signature=device.window.signature;window.dry_run.setChecked(False)
    window.start_automation()
    assert window.auto_worker is not None and window.session_check is None
    loop=QEventLoop();window.auto_worker.finished.connect(loop.quit);QTimer.singleShot(5000,loop.quit);loop.exec();app.processEvents()
    assert len(device.presses)==1 and window.auto_worker is None
    window.hotkeys.native_health=lambda:dict(ready=False)
    window.start_automation()
    assert window.auto_worker is None and len(device.presses)==1
    window.close()


def test_explicit_emergency_wait_timeout_retry_and_session_persistence(app,library,detector):
    import time
    from tests.test_automation import Device,web
    result=web(); device=Device([result]); result.live_frame=device.capture()
    result.raw_image=result.live_frame.image; result.renderer=detector
    window=MainWindow(library); window.show(); window.open_calibration(); wizard=window.calibration_wizard
    wizard.checked_result(result); wizard.ack.setChecked(True)
    packet=dict(signature=result.live_frame.window.signature,foreground=result.live_frame.window.hwnd,
                expected_game=result.live_frame.window.hwnd)
    wizard.receive_emergency_test(packet)  # No explicit waiting step yet.
    assert not wizard.begin.isEnabled()
    wizard.start_emergency_test()
    wizard.receive_emergency_test(dict(packet,foreground=999))
    assert wizard.emergency_waiting and not wizard.begin.isEnabled()
    assert 'was not focused' in wizard.emergency.text()
    wizard.emergency_deadline=time.monotonic()-1; wizard.emergency_tick()
    assert not wizard.emergency_waiting and wizard.gate_snapshot()['reason']=='wizard_emergency_timeout'
    wizard.start_emergency_test(); wizard.receive_emergency_test(packet)
    assert wizard.begin.isEnabled() and not wizard.emergency_timer.isActive()
    window.stop_analysis(); wizard.refresh_gate()
    assert wizard.begin.isEnabled() and wizard.gate_snapshot()['emergency_tested']
    wizard.checked_result(result)
    assert wizard.begin.isEnabled()  # A new scan of the same window preserves the result.
    wizard.close(); window.open_calibration()
    assert not window.calibration_wizard.ack.isChecked() and window.emergency_tested_signature is None
    window.close()


def test_downloaded_killer_selection_portraits_and_priority_contexts(app,library,root):
    if 'trapper' not in library.wiki_status: pytest.skip('Wiki assets not downloaded in this environment')
    window=MainWindow(library); window.show(); window.role.setCurrentIndex(1)
    window.character.setCurrentIndex(window.character.findData('trapper')); app.processEvents()
    addons=[e for e in window.icon_grid.entries if e.category=='addons']
    assert len(addons)==21 and sum(e.character=='trapper' for e in addons)==20
    assert all(e.character in ('trapper',None) for e in addons)
    assert not window.character.itemIcon(window.character.currentIndex()).isNull()
    assert 'files verified' in window.library_count.text()
    target=addons[0].id; window.toggle_icon(target)
    window.character.setCurrentIndex(window.character.findData('nurse')); app.processEvents()
    assert target not in window.active_selected
    assert all(e.character in (None,'nurse') for e in window.icon_grid.entries)
    window.character.setCurrentIndex(window.character.findData('trapper')); app.processEvents()
    assert target in window.active_selected
    assert window.grab().save(str(root/'debug/v4/killer_ru.png'))
    window.language.setCurrentIndex(1); app.processEvents()
    assert window.character.currentText()=='Trapper'
    assert window.grab().save(str(root/'debug/v4/killer_en.png'))
    window.close()


def test_calibration_prefers_identified_safe_node_and_records_observation_without_unlock(app,library,detector):
    from tests.test_automation import Device,web
    from src.core.models import Node,NodeState,NodeType
    result=web(); result.nodes[1].icon_id=None
    result.nodes.append(Node(2,150,300,20,NodeState.AVAILABLE,NodeType.ITEM,'survivor_item_003',
        confidence=.97,state_confidence=.95,geometry_confidence=.95))
    result.edges.append((0,2))  # The recognized candidate is immediately reachable.
    device=Device([result]); result.live_frame=device.capture(); result.raw_image=result.live_frame.image; result.renderer=detector
    window=MainWindow(library); window.open_calibration(); wizard=window.calibration_wizard
    wizard.checked_result(result)
    assert wizard.target.id==2 and wizard.coordinates_valid
    wizard.observed_purchase(True)
    assert window.calibration is None and not wizard.confirm.isEnabled() and not wizard.begin.isEnabled()
    assert wizard.gate_report.entries[-1]['stage']=='user_purchase_observation'
    window.close()


def test_wizard_excludes_real_outer_gold_preview_until_neighbor_is_owned(app,library,detector,root):
    import time
    from src.utils.images import read_image
    from src.core.models import NodeState
    from src.core.screen_manager import CapturedFrame,WindowInfo,Rect
    image=read_image(root/'tests/fixtures/v43/followup/outer_before.png')
    result=detector.analyze(image)
    target=next(n for n in result.nodes if n.icon_id=='wiki_survivor_common_addons_69cf520f0aa9')
    assert target.state==NodeState.AVAILABLE
    result.live_frame=CapturedFrame(image,WindowInfo(1,'Test frame',Rect(0,0,840,840),Rect(0,0,1920,1080)),Rect(0,0,840,840),time.monotonic())
    result.raw_image=image;result.renderer=detector
    window=MainWindow(library);window.open_calibration();wizard=window.calibration_wizard
    try:
        wizard.checked_result(result)
        assert wizard.nodes.count()==6
        assert all(wizard.nodes.itemData(i).id!=target.id for i in range(wizard.nodes.count()))
    finally:wizard.close();window.close()


def test_recheck_of_previous_purchase_never_sends_another_click_and_requires_new_stop_test(app,library,detector,monkeypatch):
    import copy
    from tests.test_automation import Device,web
    from src.core.models import Node,NodeState,NodeType
    from types import SimpleNamespace
    before=web(); before.nodes[1].icon_id='survivor_item_003'
    for i,(x,y) in enumerate(((80,80),(80,320),(320,80)),2):
        before.nodes.append(Node(i,x,y,20,NodeState.AVAILABLE,NodeType.ITEM,f'anchor_{i}',state_confidence=.95))
    after=copy.deepcopy(before); after.nodes[1].state=NodeState.PURCHASED
    device=Device([after,after]); device.backend=SimpleNamespace(); device.save_debug=detector.save_debug
    frame=device.capture(); device.index=0
    monkeypatch.setattr('src.core.screen_manager.ScreenManager',lambda **kwargs:device)
    monkeypatch.setattr('src.core.bloodweb_detector.BloodwebDetector',lambda *args,**kwargs:device)
    window=MainWindow(library); window.purchase_mode.setCurrentIndex(0); window.open_calibration()
    wizard=window.calibration_wizard
    wizard.recheck_context=dict(frame=frame.metadata(),target={'id':1,'icon':'survivor_item_003'},point=[-170,200],mode='click',duration=.05,
        nodes=[dict(id=n.id,x=n.x,y=n.y,icon=n.icon_id) for n in before.nodes],before='saved_png_hash',report='test_attempt.json')
    wizard.recheck_purchase()
    loop=QEventLoop(); window.worker.finished.connect(loop.quit); QTimer.singleShot(3000,loop.quit); loop.exec(); app.processEvents()
    assert not device.presses and window.calibration['verified'] and not window.calibration['user_confirmed']
    assert wizard.rechecked and not wizard.confirm.isEnabled() and not wizard.begin.isEnabled()
    wizard.emergency_observed(frame.window.signature)
    assert wizard.confirm.isEnabled() and not wizard.begin.isEnabled()
    wizard.confirm_purchase(); assert window.calibration['user_confirmed']
    window.close()
