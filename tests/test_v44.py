"""v4.4 replay/GUI checks; all capture and input devices are injected."""
import copy,json,time
from dataclasses import replace
import pytest
from PySide6.QtCore import QEventLoop,QTimer,QThread,Signal
from tests.test_gui import app,isolate_preferences_and_hotkeys
from tests.test_automation import Device,web,engine
from src.core.preferences import Preferences
from src.core.bloodweb_detector import BloodwebDetector
from src.core.diagnostics import DiagnosticStore
from src.core.models import NodeState
from src.core.automation import State
from src.gui.main_window import MainWindow
from src.utils.images import read_image


def receipt():
    return dict(verified=True,user_confirmed=True,mode='hold',hold_seconds=.65,
        before='a'*64,after='b'*64,point=[789,500],window={'hwnd':123},roi={'x':243})


def test_restart_restores_method_without_coordinates_or_f9_proof(app,library):
    first=MainWindow(library);first.calibration=receipt();first.save_preferences();first.close()
    second=MainWindow(library)
    assert second.calibration==receipt()
    assert second.emergency_tested_signature is None and getattr(second,'calibration_signature',None) is None
    second.last_live=object();second.role.setCurrentIndex(1)
    assert second.calibration==receipt() and second.last_live is None
    if second.character.count()>1:second.character.setCurrentIndex((second.character.currentIndex()+1)%second.character.count())
    second.clear_selection();assert second.calibration==receipt()
    second.close()


@pytest.mark.parametrize('field,value',[('verified',False),('user_confirmed',False),('before','oops'),
    ('after','a'*64),('mode','click'),('hold_seconds',.7),('blocked_reason','purchase_not_confirmed')])
def test_invalid_saved_calibration_never_arms(tmp_path,library,field,value):
    value_receipt=receipt();value_receipt[field]=value
    path=tmp_path/'prefs.json';path.write_text(json.dumps({'calibration':value_receipt}),encoding='utf-8')
    assert Preferences(path,library.entries).saved_calibration() is None


@pytest.mark.parametrize('seconds',[3,5,10])
def test_full_countdown_before_any_capture_and_stop_cancels(seconds,monkeypatch):
    from tests.test_v46 import Clock
    clock=Clock();monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    device=Device([web()]);runner=engine(device,dry_run=False);runner.settings.startup_delay=seconds
    waited=[];events=[]
    def wait(duration):waited.append(duration);clock.advance(duration)
    runner.wait=wait
    def event(state,message,packet):
        if message=='prepare_countdown':events.append(packet['seconds'])
        if isinstance(packet,tuple):
            assert sum(waited)==seconds;runner.halt()
    runner.event=event;runner.run()
    assert events==list(range(seconds,0,-1)) and not device.presses
    device=Device([web()]);runner=engine(device,dry_run=False);runner.settings.startup_delay=seconds
    runner.wait=lambda duration:runner.halt()
    runner.run();assert device.index==0 and not device.presses and device.releases


def test_delay_gui_range_default_and_persistence(app,library):
    window=MainWindow(library)
    assert (window.startup_delay.minimum(),window.startup_delay.maximum(),window.startup_delay.value())==(3,3,3)
    window.startup_delay.setValue(10);window.close()
    window=MainWindow(library);assert window.startup_delay.value()==3;window.close()


def test_action_panel_stays_above_scroll_and_switches_language(app,library):
    window=MainWindow(library);window.show();app.processEvents()
    assert window.action_panel.parent()==window.background
    assert window.content_layout.indexOf(window.action_panel)==2
    assert all(b.height()>=46 for b in window.buttons.values())
    assert window.buttons['stop'].isVisible()
    window.language.setCurrentIndex(1);assert window.session_button.text()=='Quick F9 check'
    window.close()


def signature(result):
    return (result.screen_state,[(n.x,n.y,n.state,n.icon_id,n.candidates,n.match_reason,n.geometry_confidence) for n in result.nodes],result.edges)


def test_live_identity_cache_reuses_only_exact_glyph_and_resets_geometry(root,library):
    detector=BloodwebDetector(library)
    image=read_image(root/'tests/fixtures/v43/followup/red_connection_1.png')
    device=Device([web()]);frame=device.capture();frame.image=image
    first=detector.analyze_live(frame);second=detector.analyze_live(frame)
    assert signature(first)==signature(second) and second.details['identity_cache_hits']>0
    fresh=BloodwebDetector(library).analyze(image)
    assert signature(second)==signature(fresh)
    changed=copy.copy(frame);changed.window=replace(frame.window,hwnd=2)
    assert detector.analyze_live(changed).details['identity_cache_hits']==0
    changed.image=read_image(root/'tests/fixtures/v43/followup/red_connection_2.png')
    result=detector.analyze_live(changed)
    assert signature(result)==signature(BloodwebDetector(library).analyze(changed.image))


def test_preparation_cache_is_shared_but_matches_are_owned(library):
    a=BloodwebDetector(library);b=BloodwebDetector(library)
    assert b.matcher.preparation_reused and a.matcher.templates is b.matcher.templates
    assert a.matcher.match_cache is not b.matcher.match_cache
    assert not b.matcher.templates.flags.writeable


def test_normal_diagnostics_and_failure_frames_preserve_parent_ids(tmp_path,root,detector):
    image=read_image(root/'references/bloodweb/bloodweb_level43_start.png')
    result=detector.analyze(image);folder=DiagnosticStore(tmp_path).record(image,result,detector,full=False)
    assert {p.name for p in folder.iterdir()}=={'analysis.json','manifest.json'}
    parent=result.analysis_id
    device=Device([web()]);runner=engine(device,dry_run=False)
    runner.diagnostics=DiagnosticStore(tmp_path);runner.detector=detector;frame=device.capture();frame.image=image
    runner.recent_frames.append((frame,result,None));runner.save_failure_evidence('purchase_not_confirmed')
    assert result.analysis_id==parent
    evidence=[json.loads(p.read_text(encoding='utf-8')) for p in tmp_path.glob('*/analysis.json')]
    assert any(e['details'].get('parent_analysis_id')==parent for e in evidence)
    assert any(p.name=='raw.png' for p in tmp_path.glob('*/raw.png'))


def spin_until(predicate,app,timeout=3):
    deadline=time.monotonic()+timeout
    while not predicate() and time.monotonic()<deadline:app.processEvents();time.sleep(.005)
    assert predicate()


def test_short_f9_check_rejects_backup_focus_and_synthetic_then_recaptures(app,library,monkeypatch):
    from types import SimpleNamespace
    class Capture(Device):
        def validate_click(self,frame,x,y,**kwargs):return super().validate_click(frame,x,y)
    device=Capture([web(),web()])
    captures=[]
    class Worker(QThread):
        completed=Signal(object);failed=Signal(str)
        def __init__(self,*args,**kwargs):super().__init__()
        def run(self):
            result=web();result.live_frame=device.capture();result.capture_manager=device;captures.append(result)
            self.completed.emit(result)
    monkeypatch.setattr('src.gui.main_window.AnalysisWorker',Worker)
    window=MainWindow(library);window.calibration=receipt();window.open_session_check();dialog=window.session_check
    dialog.scan_window();spin_until(lambda:dialog.waiting and dialog.worker is None,app)
    packet=dict(signature=device.window.signature,focus_matches=True,shortcut='F9',native_event={'message_verified':True})
    for invalid in (dict(shortcut='Ctrl+Alt+F9'),dict(focus_matches=False),dict(native_event={'message_verified':False})):
        dialog.receive_emergency_test(dict(packet,**invalid));assert window.emergency_tested_signature is None
    dialog.receive_emergency_test(packet)
    spin_until(lambda:window.emergency_tested_signature is not None and dialog.worker is None,app)
    assert len(captures)==2 and not device.presses and window.auto_worker is None
    dialog.close();assert window.emergency_tested_signature==device.window.signature
    window.close()


def test_invalidation_is_saved_and_cannot_resurrect(app,library):
    window=MainWindow(library);window.calibration=receipt();window.save_preferences()
    window.purchase_mode.setCurrentIndex(0);window.close()
    window=MainWindow(library);assert window.calibration is None
    assert window.prefs.data['calibration']['blocked_reason']=='input_method_changed';window.close()


def test_short_check_timeout_cancels_without_input(app,library):
    window=MainWindow(library);window.calibration=receipt();window.open_session_check();dialog=window.session_check
    dialog.waiting=True;dialog.deadline=time.monotonic()-1;dialog.tick()
    assert not dialog.waiting and window.emergency_tested_signature is None and window.auto_worker is None
    dialog.close();window.close()


def test_resume_rebuilds_engine_and_preserves_attempts_limits_and_f9(app,library,monkeypatch):
    from types import SimpleNamespace
    from src.core.automation import Automation
    from src.gui.automation_worker import AutomationWorker
    class FullDevice(Device):
        def validate_click(self,frame,x,y,**kwargs):return super().validate_click(frame,x,y)
    device=FullDevice([web(),web(),web(NodeState.PURCHASED),web(NodeState.PURCHASED)])
    device.backend=SimpleNamespace(info=lambda:device.window)
    previous=engine(device,dry_run=False);previous.actions=4;previous.attempted=[(-100,100)]
    previous.signature=device.window.signature;previous.runtime_deadline=time.monotonic()+90
    created=[]
    def factory(library,role,priorities,settings,approved,hotkeys):
        settings.startup_delay=0;settings.poll_interval=.01
        runner=Automation(device,device,device,priorities,settings,approved=approved,hotkeys_ready=hotkeys)
        created.append(runner);return runner
    monkeypatch.setattr('src.gui.main_window.make_engine',factory)
    window=MainWindow(library);window.calibration=receipt();window.dry_run.setChecked(False);window.run_mode.setCurrentIndex(window.run_mode.findData('test'))
    window.hotkeys.native_health=lambda:dict(ready=True)
    window.auto_worker=AutomationWorker(previous);window.auto_worker.user_paused=True;window.automation_finished()
    assert window.status_key=='paused' and window.resume_engine is previous
    window.pause_automation();assert window.session_check is None
    spin_until(lambda:window.auto_worker is None,app)
    assert len(created)==1 and created[0] is not previous
    assert created[0].actions==5 and created[0].attempted[0]==(-100,100)
    assert created[0].settings.max_runtime<=90 and device.index>=4 and len(device.presses)==1
    assert window.emergency_tested_signature is None
    window.close()


def test_pause_during_unconfirmed_input_preserves_method_and_records_outcome(app,library):
    from src.gui.automation_worker import AutomationWorker
    device=Device([web()]);runner=engine(device,dry_run=False);runner.pending_action=True
    window=MainWindow(library);window.calibration=receipt();window.auto_worker=AutomationWorker(runner)
    window.pause_automation();assert runner.stop.is_set() and device.releases
    window.automation_finished();assert window.calibration==receipt()
    assert window.prefs.data['last_purchase_outcome']['reason']=='purchase_result_unknown'
    window.close();window=MainWindow(library)
    assert window.calibration==receipt() and window.emergency_tested_signature is None;window.close()


def test_failed_evidence_write_still_releases_and_reports_pause(tmp_path):
    from src.core.screen_manager import ScreenError
    device=Device([web()]);runner=engine(device,dry_run=False)
    def capture():raise ScreenError('window_changed')
    def failed(reason):raise OSError('disk unavailable')
    device.capture=capture;runner.save_failure_evidence=failed
    runner.run();assert runner.state==State.PAUSED and device.releases and not device.presses
    assert any(e['stage']=='failure_evidence_error' for e in runner.report.entries)


def test_escape_closes_check_and_late_scan_cannot_arm(app,library):
    window=MainWindow(library);window.calibration=receipt();window.open_session_check();dialog=window.session_check
    dialog.reject();assert window.session_check is None and dialog.closed
    dialog.checked_result(web());assert window.emergency_tested_signature is None
    window.close()


def test_close_during_unconfirmed_action_preserves_method_without_session_proof(app,library):
    from src.gui.automation_worker import AutomationWorker
    window=MainWindow(library);window.calibration=receipt()
    runner=engine(Device([web()]),dry_run=False);runner.pending_action=True
    window.auto_worker=AutomationWorker(runner);window.close()
    window=MainWindow(library)
    assert window.calibration==receipt() and window.emergency_tested_signature is None
    assert window.prefs.data['last_purchase_outcome']['reason']=='purchase_result_unknown';window.close()


def test_bounded_performance_samples_keep_analysis_links():
    runner=engine(Device([web()]),dry_run=True)
    for _ in range(5001):runner.performance.add('old',0)
    from types import SimpleNamespace
    runner.diagnostics=SimpleNamespace(record=lambda image,result,*args,**kwargs:setattr(result,'analysis_id','latest'))
    runner.read()
    recent=[s for s in runner.performance.samples if s['stage']!='old']
    assert recent and all(s['analysis_id']=='latest' for s in recent)


def test_normal_manual_scan_has_preview_and_review_without_disk_crops(app,library,root):
    from tests.test_gui import wait_for_worker
    from src.gui.review_dialog import ReviewDialog
    from src.gui.widgets import Preview
    window=MainWindow(library);window.start_analysis(root/'references/bloodweb/bloodweb_level43_start.png')
    wait_for_worker(window,app);result=window.last_analysis
    assert result.debug_image is None and not window.main_preview.original.isNull()
    node=next(n for n in result.nodes if n.icon_id)
    assert node.crop_path is None and node.normalized_path is None
    dialog=ReviewDialog(window,result,node)
    assert all(not p.original.isNull() for p in dialog.findChildren(Preview))
    dialog.close();window.close()
