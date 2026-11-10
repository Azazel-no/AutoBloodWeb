import copy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.core.automation import State
from src.core.models import Analysis, NodeState, ScreenState
from src.core.recovery_supervisor import PAUSE, RETRY, WAIT, RecoverySupervisor, classify
from src.core.scene_memory import record_scene
from src.core.screen_manager import Rect, ScreenError
from tests.test_automation import Device, engine, web


class Clock:
    def __init__(self): self.now = 100.
    def __call__(self): return self.now


def supervised(results=None, **kwargs):
    device = Device(results or [web()])
    device.backend = SimpleNamespace(info=lambda: device.window)
    e = engine(device, dry_run=False, recovery_supervisor=True, **kwargs)
    e.recovery.base_delay = 0
    e.recovery.countdown_seconds = .03
    e.events = []
    e.event = lambda state, message, packet: e.events.append((state, message, packet))
    return device, e


def script(e, *steps):
    """Replace the live loop: each step raises, or runs as a callable."""
    calls = []
    steps = list(steps)
    def loop(calibrate, start):
        calls.append(start)
        if not steps:
            return
        step = steps.pop(0)
        if isinstance(step, Exception):
            raise step
        step()
    e._main_loop = loop
    return calls


def stages(e):
    return [entry['stage'] for entry in e.report.entries]


def pending_scene(device, e, **extra):
    frame = device.capture()
    scene = record_scene(frame, web(), [frame.to_screen(230, 200)])
    scene['pending'] = dict(point=list(frame.to_screen(230, 200)), radius=20, icon='target', operation_id='op-1', **extra)
    e.scene_record = scene
    e.pending_action = True
    e.attempted = [frame.to_screen(230, 200)]
    e.signature = frame.window.signature
    return scene


@pytest.mark.parametrize('code,detail,kind', [
    ('stale_frame', '', RETRY), ('window_changed', '', RETRY), ('no_confident_action', '', RETRY),
    ('refresh_timeout', '', RETRY), ('capture_failed', '', RETRY), ('prestige_price_unproven', '', PAUSE),
    ('game_focus_lost', '', WAIT), ('game_not_found', '', WAIT), ('game_minimized', '', WAIT),
    ('purchase_result_unknown', '', PAUSE), ('input_integrity_mismatch', '', PAUSE), ('sendinput_failed', '', PAUSE),
    ('cursor_position_mismatch', '', PAUSE), ('chain_budget_limit', '', PAUSE), ('runtime_limit', '', PAUSE),
    ('action_limit', '', PAUSE), ('insufficient_bp', '', PAUSE), ('hotkey_registration_failed', '', PAUSE),
    ('modal_attempt_limit', '', PAUSE), ('tutorial_not_dismissed', '', PAUSE),
    ('brand_new_code_nobody_listed', '', PAUSE),
    ('window_changed', 'geometry_recovery_limit', PAUSE),
    ('window_changed', 'saved_pending_geometry_requires_evidence', PAUSE),
    ('chain_map_changed', 'saved_chain_contract_block', PAUSE),
])
def test_classification_retries_only_listed_codes(code, detail, kind):
    assert classify(code, detail) == kind


def test_attempt_limit_uses_a_sliding_window():
    clock = Clock()
    s = RecoverySupervisor(clock)
    s.begin(0)
    assert all(s.charge(0) is None for _ in range(10))
    assert s.charge(0) == 'recovery_attempt_limit'
    clock.now += 61
    assert s.charge(0) is None


def test_slow_failures_under_the_rate_limit_still_hit_the_progress_limit():
    clock = Clock()
    s = RecoverySupervisor(clock)
    s.begin(0)
    limit = None
    for _ in range(30):
        clock.now += 15
        limit = s.charge(0)
        if limit:
            break
    assert limit == 'recovery_no_progress_limit' and clock.now - 100 > 300


def test_confirmed_progress_resets_both_limits():
    clock = Clock()
    s = RecoverySupervisor(clock)
    s.begin(0)
    for _ in range(10):
        s.charge(0)
    clock.now += 290
    assert s.charge(1) is None and len(s.attempts) == 1
    clock.now += 290
    assert s.charge(1) is None


def test_time_waiting_for_the_game_does_not_count_as_no_progress():
    clock = Clock()
    s = RecoverySupervisor(clock)
    s.begin(0)
    clock.now += 280
    s.exclude_wait(280)
    clock.now += 30
    assert s.charge(0) is None


def test_backoff_grows_and_is_capped():
    s = RecoverySupervisor(Clock())
    s.begin(0)
    delays = []
    for _ in range(7):
        s.charge(0)
        delays.append(s.delay())
    assert delays == [.5, 1, 2, 4, 5, 5, 5]


def test_retry_reenters_with_clean_preparation_and_keeps_counters():
    device, e = supervised()
    e.actions, e.attempts = 7, 9
    e.attempted = [(1, 2)]
    e.automatic_proof = 'old'
    e.native_chain_permit = 'old'
    e.direct_prepared_single = 'old'
    e.direct_price_observations = {'known': 1}
    runtime = []
    def probe():
        runtime.append(e.runtime_deadline)
    calls = script(e, ScreenError('stale_frame'), probe)
    e.run()
    assert len(calls) == 2 and calls[0] == calls[1]
    assert e.actions == 7 and e.attempts == 9 and e.attempted == [(1, 2)]
    assert e.automatic_proof is None and e.native_chain_permit is None and e.direct_prepared_single is None
    assert e.direct_price_observations == {'known': 1} and not device.presses
    assert 'supervisor_recovery' in stages(e) and 'failure' not in stages(e)
    assert any(m == 'supervisor_recovery' and p['reason'] == 'stale_frame' for _, m, p in e.events)


def test_recoveries_are_not_recorded_as_failures_that_invalidate_calibration():
    _, e = supervised()
    script(e, ScreenError('capture_failed'), ScreenError('sendinput_failed'))
    e.run()
    failures = [entry['reason'] for entry in e.report.entries if entry['stage'] == 'failure']
    assert failures == ['sendinput_failed'] and e.state == State.PAUSED


def test_exhausted_recovery_pauses_with_the_original_code():
    _, e = supervised()
    calls = script(e, *[ScreenError(('frame_unstable', 'stale_frame')[i % 2]) for i in range(50)])
    e.run()
    assert len(calls) == 11 and e.state == State.PAUSED and e.pause.is_set()
    assert 'recovery_exhausted' in stages(e)
    assert any(m == 'frame_unstable' for _, m, _p in e.events)
    assert any(isinstance(p, dict) and 'recovery_attempt_limit' in p.get('error_detail', '') for _, _m, p in e.events)


def test_same_failure_pauses_after_five_retries_without_clicks():
    device, e = supervised()
    calls = script(e, *[ScreenError('chain_price_unproven') for _ in range(50)])
    e.run()
    assert len(calls) == 6 and e.state == State.PAUSED and not device.presses
    assert stages(e).count('supervisor_recovery') == 5
    assert any(isinstance(p, dict) and 'recovery_same_failure_limit' in p.get('error_detail', '') for _, _m, p in e.events)


def test_same_code_with_another_detail_or_other_failure_resets_the_streak():
    s = RecoverySupervisor(Clock())
    s.begin(0)
    for failure in (('a', ''), ('a', ''), ('a', 'x'), ('a', 'x'), ('b', ''), ('a', '')):
        assert s.charge(0, failure) is None
    assert s.same_count == 1


def test_confirmed_progress_resets_the_same_failure_streak():
    s = RecoverySupervisor(Clock())
    s.begin(0)
    for _ in range(5):
        assert s.charge(0, ('a', '')) is None
    assert s.charge(1, ('a', '')) is None and s.same_count == 1


def test_window_rebinds_do_not_count_as_a_repeated_failure():
    s = RecoverySupervisor(Clock())
    s.begin(0)
    for _ in range(5):
        s.charge(0, ('a', ''))
    assert s.charge(0) is None and s.same_count == 5


@pytest.mark.parametrize('code', ['insufficient_bp', 'input_integrity_mismatch', 'chain_budget_limit',
                                  'runtime_limit', 'brand_new_code_nobody_listed'])
def test_critical_and_unknown_codes_pause_at_once(code):
    device, e = supervised()
    calls = script(e, ScreenError(code), lambda: None)
    e.run()
    assert len(calls) == 1 and e.state == State.PAUSED and not device.presses
    assert 'supervisor_recovery' not in stages(e)


def test_supervisor_is_off_by_default_and_calibration_never_retries():
    device = Device([web()])
    e = engine(device, dry_run=False)
    assert e.recovery is None
    calls = script(e, ScreenError('stale_frame'), lambda: None)
    e.run()
    assert len(calls) == 1 and e.state == State.PAUSED
    device, e = supervised()
    calls = script(e, ScreenError('stale_frame'), lambda: None)
    e.run(calibrate=True)
    assert len(calls) == 1 and e.state == State.PAUSED


def test_stop_during_backoff_stops_without_another_read():
    device, e = supervised()
    e.recovery.base_delay = 5
    original = e.emit
    def emit(state, message='', packet=None):
        original(state, message, packet)
        if message == 'supervisor_recovery':
            e.halt()
    e.emit = emit
    calls = script(e, ScreenError('stale_frame'), lambda: None)
    e.run()
    assert len(calls) == 1 and e.state == State.STOPPED and device.releases >= 2


def test_focus_loss_waits_then_counts_down_before_any_new_read():
    device, e = supervised()
    device.window = replace(device.window, foreground=False)
    order = []
    original = e.wait
    def wait(seconds):
        order.append('wait')
        if order.count('wait') == 3:
            device.window = replace(device.window, foreground=True)
        original(seconds)
    e.wait = wait
    calls = script(e, ScreenError('game_focus_lost'), lambda: order.append('read'))
    e.run()
    messages = [m for _, m, _p in e.events]
    assert messages.index('recovery_waiting_game') < messages.index('prepare_countdown')
    assert order[-1] == 'read' and len(calls) == 2 and not device.presses
    assert any(m == 'restore_window' for m in messages[:messages.index('recovery_waiting_game')])
    assert len(e.recovery.attempts) == 0


def test_countdown_runs_the_full_three_seconds_by_default_and_shows_each_second():
    device, e = supervised()
    assert RecoverySupervisor.countdown_seconds == 3
    e.recovery.countdown_seconds = 3
    now = [1000.]
    import src.core.automation as automation
    real = automation.time
    automation.time = SimpleNamespace(monotonic=lambda: now[0], perf_counter=real.perf_counter, time=real.time)
    def wait(seconds):
        now[0] += seconds
    e.wait = wait
    try:
        script(e, ScreenError('game_focus_lost'))
        e.run()
    finally:
        automation.time = real
    shown = [p['seconds'] for _, m, p in e.events if m == 'prepare_countdown']
    assert shown == [3, 2, 1]


def test_stop_during_countdown_prevents_any_new_read():
    device, e = supervised()
    e.recovery.countdown_seconds = 5
    original = e.emit
    def emit(state, message='', packet=None):
        original(state, message, packet)
        if message == 'prepare_countdown':
            e.halt()
    e.emit = emit
    calls = script(e, ScreenError('game_focus_lost'), lambda: None)
    e.run()
    assert len(calls) == 1 and e.state == State.STOPPED and device.releases >= 2


def test_losing_focus_during_countdown_restarts_the_wait_and_countdown():
    device, e = supervised()
    e.recovery.countdown_seconds = .2
    seen = []
    original = e.emit
    def emit(state, message='', packet=None):
        original(state, message, packet)
        seen.append(message)
        if message == 'prepare_countdown' and seen.count('prepare_countdown') == 1:
            device.window = replace(device.window, foreground=False)
        if message == 'recovery_waiting_game' and seen.count('recovery_waiting_game') == 2:
            device.window = replace(device.window, foreground=True)
    e.emit = emit
    calls = script(e, ScreenError('game_focus_lost'))
    e.run()
    assert seen.count('recovery_waiting_game') == 2 and seen.count('prepare_countdown') >= 2
    assert 'recovery_countdown_interrupted' in stages(e) and len(calls) == 2


def test_game_never_returning_pauses_after_the_wait_limit():
    device, e = supervised()
    e.recovery.wait_limit_seconds = .05
    def missing():
        raise ScreenError('game_not_found')
    def vanish():
        device.backend = SimpleNamespace(info=missing)
        e.screen.backend = device.backend
        raise ScreenError('game_not_found')
    calls = script(e, vanish, lambda: None)
    e.run()
    assert len(calls) == 1 and e.state == State.PAUSED and not device.presses
    assert any(isinstance(p, dict) and 'recovery_wait_limit' in p.get('error_detail', '') for _, _m, p in e.events)


def test_restarted_game_with_new_window_rebinds_instead_of_pausing():
    device, e = supervised()
    frame = device.capture()
    e.signature = frame.window.signature
    e.scene_record = record_scene(frame, web(), [])
    missing = {'gone': False}
    def info():
        if missing['gone']:
            missing['gone'] = False
            raise ScreenError('game_not_found')
        return device.window
    device.backend = SimpleNamespace(info=info)
    def crash():
        missing['gone'] = True
        device.window = replace(device.window, hwnd=99, pid=77)
        raise ScreenError('game_not_found')
    captured = []
    calls = script(e, crash, lambda: captured.append(e.capture_frame()))
    e.run()
    assert len(calls) == 2 and captured[0].window.hwnd == 99 and e.signature == device.window.signature
    assert e.persisted_scene and e.state != State.PAUSED and not device.presses
    assert 'window_geometry_recovery' in stages(e)


def test_repeated_window_changes_use_the_sliding_limit_not_three_per_session():
    device, e = supervised()
    frame = device.capture()
    e.signature = frame.window.signature
    for hwnd in range(2, 12):
        device.window = replace(device.window, hwnd=hwnd)
        e.capture_frame()
    assert e.signature == device.window.signature
    device.window = replace(device.window, hwnd=50)
    with pytest.raises(ScreenError) as caught:
        e.capture_frame()
    assert caught.value.detail == 'geometry_recovery_limit' and classify('window_changed', caught.value.detail) == PAUSE


def test_without_supervisor_the_old_limit_of_three_stays():
    device = Device([web()])
    device.backend = SimpleNamespace(info=lambda: device.window)
    e = engine(device, dry_run=False)
    e.signature = device.capture().window.signature
    for hwnd in range(2, 5):
        device.window = replace(device.window, hwnd=hwnd)
        e.capture_frame()
    device.window = replace(device.window, hwnd=50)
    with pytest.raises(ScreenError, match='window_changed'):
        e.capture_frame()


def test_pending_purchase_is_only_rechecked_never_submitted_again():
    device, e = supervised()
    scene = pending_scene(device, e)
    seen = []
    def observe():
        seen.append((copy.deepcopy(e.persisted_scene), e.pending_action, list(e.attempted)))
    calls = script(e, ScreenError('stale_frame'), observe)
    e.run()
    persisted, pending_flag, attempted = seen[0]
    assert len(calls) == 2 and not device.presses and e.attempts == 0
    assert persisted['pending'] == scene['pending'] and persisted['attempted'] == [list(attempted[0])]
    assert pending_flag is False and attempted and 'recovery_readonly_recheck' in stages(e)
    assert e.scene_record['pending'], 'the unresolved operation is not forgotten'


def test_unknown_payment_result_gets_one_readonly_pass_then_pauses():
    device, e = supervised()
    pending_scene(device, e)
    calls = script(e, ScreenError('purchase_result_unknown'), ScreenError('purchase_result_unknown'), lambda: None)
    e.run()
    assert len(calls) == 2 and e.state == State.PAUSED and not device.presses
    assert e.scene_record['pending'] and e.pending_action is True


@pytest.mark.parametrize('detail', ['previous_start_unresolved', 'scene_validation_required'])
def test_readonly_verdict_of_unresolved_pauses_immediately(detail):
    device, e = supervised()
    pending_scene(device, e)
    calls = script(e, ScreenError('purchase_result_unknown', detail), lambda: None)
    e.run()
    assert len(calls) == 1 and e.state == State.PAUSED and e.pending_action is True


def test_pending_with_new_process_and_same_geometry_is_read_only_rebound():
    device, e = supervised()
    pending_scene(device, e)
    device.window = replace(device.window, hwnd=99, pid=77)
    calls = script(e, ScreenError('window_changed', 'paid_operation_geometry_unresolved'), lambda: None)
    e.run()
    assert len(calls) == 2 and e.signature == device.window.signature and not device.presses
    assert e.persisted_scene['pending']['operation_id'] == 'op-1'


def test_pending_with_changed_geometry_pauses_and_keeps_the_operation():
    device, e = supervised()
    pending_scene(device, e)
    old = e.signature
    device.window = replace(device.window, dpi=192, client=Rect(0, 0, 800, 800))
    calls = script(e, ScreenError('window_changed', 'paid_operation_geometry_unresolved'), lambda: None)
    e.run()
    assert len(calls) == 1 and e.state == State.PAUSED and e.signature == old
    assert e.scene_record['pending'] and e.pending_action and not e.persisted_scene


def test_chain_contract_block_is_never_retried():
    device, e = supervised()
    pending_scene(device, e, chain=True, chain_blocked_reason='chain_map_changed')
    calls = script(e, ScreenError('stale_frame'), lambda: None)
    e.run()
    assert len(calls) == 1 and e.state == State.PAUSED


def test_stop_during_readonly_pass_still_reports_the_operation_unresolved():
    device, e = supervised()
    pending_scene(device, e)
    calls = script(e, ScreenError('stale_frame'), e.halt)
    e.run()
    assert len(calls) == 2 and e.pending_action is True and e.scene_record['pending']


def test_readonly_resolution_credits_only_nodes_not_already_counted():
    device, e = supervised()
    pending_scene(device, e, chain_outcome=dict(confirmed=[[1, 1]]))
    script(e, ScreenError('stale_frame'))
    e.run()
    e.scene_record = dict(last_outcome=dict(operation_id='op-1', confirmed_nodes=3))
    e.credit_readonly_resolution()
    e.credit_readonly_resolution()
    assert e.actions == 2


def test_real_loop_recovers_a_capture_error_then_buys_once():
    results = [web(), web(), web(NodeState.PURCHASED), web(NodeState.PURCHASED)]
    device, e = supervised(results, max_actions=1)
    del device.backend
    capture = device.capture
    failures = [ScreenError('capture_failed')]
    def flaky():
        if failures:
            raise failures.pop()
        return capture()
    device.capture = flaky
    e.run()
    assert len(device.presses) == 1 and e.actions == 1 and e.attempts == 1
    assert 'supervisor_recovery' in stages(e)


def test_real_loop_no_confident_action_retries_without_input_then_pauses():
    device, e = supervised([web(NodeState.UNCERTAIN)])
    e.recovery.max_attempts = 2
    e.run()
    assert e.state == State.PAUSED and not device.presses
    assert stages(e).count('supervisor_recovery') == 2
    assert [entry['reason'] for entry in e.report.entries if entry['stage'] == 'failure'] == ['no_confident_action']


def test_real_loop_refresh_timeout_stays_bounded_without_clicks():
    device, e = supervised([Analysis(400, 400, ScreenState.REFRESH)])
    e.recovery.max_attempts = 1
    e.run()
    assert e.state == State.PAUSED and not device.presses
    assert stages(e).count('supervisor_recovery') == 1


def test_gui_strings_exist_in_both_languages():
    from src.core.localization import STRINGS
    for language in ('ru', 'en'):
        text = STRINGS[language]['supervisor_recovery'].format(reason='stale_frame', attempt=1, limit=10)
        assert 'stale_frame' in text and '1/10' in text
        assert 'game_focus_lost' in STRINGS[language]['recovery_waiting_game'].format(reason='game_focus_lost')


@pytest.mark.parametrize('dry_run,expected', [(False, True), (True, False)])
def test_ordinary_start_enables_the_supervisor_for_live_runs_only(monkeypatch, tmp_path, library, dry_run, expected):
    from PySide6.QtWidgets import QApplication
    from src.core.automation import Automation
    from src.gui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    original = MainWindow.__init__
    monkeypatch.setattr(MainWindow, '__init__', lambda self, lib: original(self, lib, tmp_path / 'preferences.json'))
    class Hotkeys:
        ready = True
        def __init__(self, callback, bindings=None): pass
        def close(self): pass
        def native_health(self): return dict(ready=True)
    monkeypatch.setattr('src.gui.main_window.GlobalHotkeys', Hotkeys)
    device = Device([web()])
    device.backend = SimpleNamespace(info=lambda: device.window)
    created = []
    def factory(lib, role, priorities, settings, approved, hotkeys):
        created.append(Automation(device, device, device, priorities, settings, approved=approved, hotkeys_ready=hotkeys))
        return created[-1]
    monkeypatch.setattr('src.gui.main_window.make_engine', factory)
    monkeypatch.setattr('src.gui.main_window.AutomationWorker.start', lambda self: None)
    window = MainWindow(library)
    window.dry_run.setChecked(dry_run)
    window.start_automation()
    app.processEvents()
    assert created and created[0].settings.recovery_supervisor is expected
    assert (created[0].recovery is not None) is expected and not device.presses
    window.auto_worker = None
    window.close()
