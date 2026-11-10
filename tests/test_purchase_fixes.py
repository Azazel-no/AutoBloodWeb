"""No node hover, no node cost, known prestige cost, stale scene and recovery rules."""
import copy
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from src.core.automation import State
from src.core.direct_buy import install
from src.core.early_unlock import purchase_level
from src.core.models import Analysis, Node, NodeState, NodeType, ScreenState
from src.core.preferences import Preferences
from src.core.prestige import PRESTIGE_COST, PRESTIGE_HOLD_SECONDS, activate_ready
from src.core.scene_memory import record_scene
from src.core.screen_manager import CapturedFrame, Rect, ScreenError
from src.core.series_budget import SeriesBudget
from tests.test_automation import Device, engine, web
from tests.test_rc1_geometry import resolved


def ready_scene(level=50):
    return Analysis(400, 400, ScreenState.PRESTIGE_READY,
                    [Node(0, 200, 200, 35, NodeState.CENTER, NodeType.PRESTIGE, geometry_confidence=.95)],
                    level=level, level_confidence=.99, details={'prestige_ready': {'score': 1}})


def prestige_engine(after, bloodpoints=50000):
    device = Device(after)
    device.parking_point = lambda f: (-392, 8)
    device.park = lambda *a: None
    runner = engine(device, dry_run=False, auto_prestige=True)
    runner.prestige_approved = True
    before = CapturedFrame(np.zeros((400, 400, 3), np.uint8), device.window, Rect(0, 0, 400, 400), time.monotonic(),
                           bloodpoints=bloodpoints, level=50)
    return device, runner, before


def test_prestige_cost_is_the_verified_constant_and_hold_is_half_a_second():
    assert PRESTIGE_COST == 20000 and PRESTIGE_HOLD_SECONDS == .5


def test_prestige_holds_once_with_no_hover_and_needs_two_new_frames():
    next_web = web()
    next_web.level = 1
    next_web.level_confidence = 1
    device, runner, before = prestige_engine([next_web, next_web])
    hovers = []
    device.capture_overlay = lambda frame: hovers.append(frame)
    fresh, result, _ = activate_ready(runner, before, ready_scene())
    assert device.presses == [((-200, 200), .5)] and not hovers
    assert result.level == 1 and runner.prestige_actions == 1 and not runner.pending_action and runner.actions == 0


def test_prestige_succeeds_when_rewards_hides_the_bp_counter():
    reward = Analysis(400, 400, ScreenState.REWARD, ok_box=(300, 300, 40, 20), confidence=.94)
    device, runner, before = prestige_engine([reward, reward])
    activate_ready(runner, before, ready_scene())
    assert runner.prestige_actions == 1 and len(device.presses) == 1


@pytest.mark.parametrize('bloodpoints,code', [(None, 'prestige_bp_unreadable'), (19999, 'insufficient_bp')])
def test_prestige_never_clicks_without_a_readable_sufficient_counter(bloodpoints, code):
    device, runner, before = prestige_engine([web()], bloodpoints)
    runner.settings.bloodpoint_checks = True  # Developer option: the BP counter is off by default.
    with pytest.raises(ScreenError, match=code):
        activate_ready(runner, before, ready_scene())
    assert not device.presses and not runner.pending_action and not runner.attempted


def test_unreadable_bp_pauses_at_once_instead_of_looping():
    device, runner, _ = prestige_engine([ready_scene()])
    runner.settings.bloodpoint_checks = True  # Developer option: the BP counter is off by default.
    runner.recovery = __import__('src.core.recovery_supervisor', fromlist=['RecoverySupervisor']).RecoverySupervisor()
    runner.recovery.base_delay = 0
    original = device.capture
    device.capture = lambda: replace(original(), bloodpoints=None, level=50, level_confidence=.99)
    runner.run()
    assert runner.state == State.PAUSED and not device.presses
    assert not [e for e in runner.report.entries if e['stage'] == 'supervisor_recovery']
    assert [e['reason'] for e in runner.report.entries if e['stage'] == 'failure'] == ['prestige_bp_unreadable']


def prestige_requires_opt_in_cases():
    return [(False, True), (True, False)]


@pytest.mark.parametrize('setting,approved', prestige_requires_opt_in_cases())
def test_prestige_requires_both_opt_ins(setting, approved):
    device, runner, before = prestige_engine([web()])
    runner.settings.auto_prestige = setting
    runner.prestige_approved = approved
    with pytest.raises(ScreenError, match='prestige_manual'):
        activate_ready(runner, before, ready_scene())
    assert not device.presses


def live_scene(defect=None):
    device, frame, result, memory = resolved()
    fresh = replace(frame, window=replace(frame.window, hwnd=99, pid=77))
    runner = engine(device, dry_run=False)
    runner.signature = fresh.window.signature
    runner.persisted_scene = memory
    runner.scene_record = copy.deepcopy(memory)
    runner.attempted = [tuple(p) for p in memory['attempted']]
    result.nodes[2].geometry_confidence = .64  # This map does not match the saved one.
    return device, runner, fresh, result, memory


def test_old_scene_from_a_dead_process_with_nothing_pending_is_dropped_at_once():
    device, runner, fresh, result, memory = live_scene()
    assert runner.recheck_saved_scene(fresh, result)
    assert runner.persisted_scene is None and runner.scene_record is None and not runner.attempted
    assert runner.actions == 0 and not device.presses
    stages = [e for e in runner.report.entries if e['stage'] == 'saved_scene_discarded']
    assert stages[0]['purchase_credit'] == 0 and stages[0]['level'] == memory['level']


@pytest.mark.parametrize('change', ['probe', 'focus'])
def test_scene_is_not_dropped_from_a_frame_that_cannot_buy(change):
    _, runner, fresh, result, _ = live_scene()
    fresh = replace(fresh, probe_only=True) if change == 'probe' else replace(fresh, window=replace(fresh.window, foreground=False))
    runner.signature = fresh.window.signature
    assert not runner.recheck_saved_scene(fresh, result)
    assert runner.persisted_scene


def test_pending_operation_in_a_new_process_is_never_dropped():
    device, runner, fresh, result, memory = live_scene()
    memory['pending'] = dict(point=list(memory['attempted'][0]), radius=20, icon='target')
    try:
        assert not runner.recheck_saved_scene(fresh, result)
        assert not runner.recheck_saved_scene(replace(fresh, captured_at=fresh.captured_at + .1), result)
    except ScreenError:
        pass
    assert runner.persisted_scene and runner.persisted_scene['pending'] and not device.presses


def supervised(device):
    runner = engine(device, dry_run=False, recovery_supervisor=True)
    runner.recovery.base_delay = 0
    return runner


def script(runner, *steps):
    calls = []
    steps = list(steps)

    def loop(calibrate, start):
        calls.append(start)
        if steps:
            step = steps.pop(0)
            if callable(step):
                step = step()
            if isinstance(step, Exception):
                raise step
    runner._main_loop = loop
    return calls


def test_failed_hover_pauses_instead_of_hovering_again():
    runner = supervised(Device([web()]))

    def hovered_then_failed():
        runner.hover_probes = getattr(runner, 'hover_probes', 0) + 1
        return ScreenError('chain_price_unproven')
    calls = script(runner, hovered_then_failed, hovered_then_failed)
    runner.run()
    assert len(calls) == 1 and runner.state == State.PAUSED
    assert any('recovery_hover_not_repeated' in (e.get('detail') or '') for e in runner.report.entries if e['stage'] == 'failure')


def test_failure_without_a_hover_is_retried_by_reading_again():
    runner = supervised(Device([web()]))
    calls = script(runner, ScreenError('stale_frame'))
    runner.run()
    assert len(calls) == 2


def test_prestige_price_failure_is_no_longer_retried():
    from src.core.recovery_supervisor import PAUSE, classify
    assert classify('prestige_price_unproven') == PAUSE and classify('prestige_bp_unreadable') == PAUSE


def test_absorbed_flicker_is_not_progress_and_does_not_reset_limits():
    runner = supervised(Device([web()]))
    mark = runner.progress_mark()
    runner.entity_actions += 3
    assert runner.progress_mark() == mark
    runner.actions += 1
    assert runner.progress_mark() != mark


def test_direct_single_uses_no_hover_price_or_bp():
    device = Device([web()])
    runner = engine(device, dry_run=False)
    runner.report = SimpleNamespace(path=None, add=lambda *a, **k: None)
    install(runner, None)
    frame = replace(device.capture(), bloodpoints=None)
    node = web().nodes[1]
    node.icon_id = None  # Unknown identity needs no price either.
    returned = runner.prepare_direct_single(frame, frame, node)
    assert returned[2] is node and returned[0] is frame
    assert not hasattr(runner, 'direct_single_price_bound') and not hasattr(runner, 'native_price_locator')


def test_ordinary_run_buys_a_node_with_an_unreadable_bp_counter():
    device = Device([web(), web(), web(NodeState.PURCHASED), web(NodeState.PURCHASED)])
    original = device.capture
    device.capture = lambda: replace(original(), bloodpoints=None)
    runner = engine(device, dry_run=False, max_actions=1)
    install(runner, None)
    runner.run()
    assert len(device.presses) == 1 and runner.actions == 1


def test_centre_is_clicked_with_no_tooltip_hover():
    from tests.test_automation import Device as D
    before = web()
    before.level = 1
    before.level_confidence = .99
    before.nodes[0].geometry_confidence = .95
    after = web()
    after.level = 2
    after.level_confidence = .99
    device = D([after, after])
    hovers = []
    device.capture_overlay = lambda frame: hovers.append(frame)
    device.parking_point = lambda f: None
    runner = engine(device, dry_run=False)
    frame = device.capture()
    device.index = 0
    frame = replace(frame, level=1, level_confidence=.99)
    purchase_level(runner, frame, before)
    assert len(device.presses) == 1 and not hovers and device.presses[0][0] == frame.to_screen(150, 200)


def budget(tmp_path, **extra):
    return SeriesBudget(tmp_path / 'ledger.json', dict(role='killer', character='mastermind', max_bp=None, max_nodes=2,
                        max_prestiges=2, allow_prestige=True, allow_chain=False, **extra))


def test_budget_counts_nodes_and_prestiges_without_any_cost(tmp_path):
    ledger = budget(tmp_path)
    ledger.reserve(None, None, 'single', [(1, 1)])
    ledger.reserve(5, None, 'single', [(2, 2)])
    with pytest.raises(ScreenError, match='action_limit'):
        ledger.reserve(None, None, 'single', [(3, 3)])
    ledger.reserve(100000, 20000, 'prestige', [(4, 4)])
    ledger.reserve(80000, 20000, 'prestige', [(4, 4)])
    with pytest.raises(ScreenError, match='prestige_manual'):
        ledger.reserve(60000, 20000, 'prestige', [(4, 4)])
    ledger.observe(0)
    assert ledger.data['spent'] > 0 and not ledger.data.get('violation')
    ledger.settle(0, 0)
    assert budget(tmp_path).data['reservations'][0]['upper_bound'] is None


def test_a_bp_limited_budget_still_refuses_an_unreadable_balance(tmp_path):
    limited = SeriesBudget(tmp_path / 'l.json', dict(role='killer', character='dredge', max_bp=100, allow_prestige=False, allow_chain=False))
    with pytest.raises(ScreenError, match='chain_bp_unproven'):
        limited.reserve(None, 50, 'single', [(1, 1)])


def test_native_chain_is_switched_off_once_and_can_be_turned_back_on(tmp_path):
    path = tmp_path / 'preferences.json'
    path.write_text(json.dumps(dict(settings=dict(native_chain=True))), encoding='utf-8')
    prefs = Preferences(path, [])
    assert prefs.settings['native_chain'] is False
    prefs.settings['native_chain'] = True
    prefs.save('en', 'killer')
    assert Preferences(path, []).settings['native_chain'] is True


def test_cursor_is_moved_off_the_web_with_no_click():
    device = Device([web()])
    moves = []
    device.park = lambda point, stop, check: (check(), moves.append(point))
    device.backend = SimpleNamespace(info=lambda: device.window, owns_point=lambda window, point: True)
    runner = engine(device, dry_run=False)
    runner.park_cursor_off_web()
    client = device.window.client
    assert moves == [(client.x + 8, client.y + client.height // 2)] and not device.presses


@pytest.mark.parametrize('case', ['not_foreground', 'not_ours', 'no_backend'])
def test_cursor_stays_put_when_it_cannot_be_moved_safely(case):
    device = Device([web()])
    moves = []
    device.park = lambda point, stop, check: moves.append(point)
    window = replace(device.window, foreground=case != 'not_foreground')
    if case != 'no_backend':
        device.backend = SimpleNamespace(info=lambda: window, owns_point=lambda w, p: case != 'not_ours')
    engine(device, dry_run=False).park_cursor_off_web()
    assert not moves


@pytest.mark.parametrize('top,moved', [(36, True), (150, True), (170, False)])
def test_window_is_moved_off_the_bp_counter_only_when_it_covers_it(top, moved, tmp_path, library, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from src.gui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    class Hotkeys:
        ready = True
        def __init__(self, callback, bindings=None): pass
        def close(self): pass
    monkeypatch.setattr('src.gui.main_window.GlobalHotkeys', Hotkeys)
    window = MainWindow(library, tmp_path / 'preferences.json')
    window.show()
    app.processEvents()
    window.move(1273, top)
    app.processEvents()
    x = window.frameGeometry().left()
    ratio = window.devicePixelRatioF()
    client = Rect(0, 0, round(1920 * ratio), round(1080 * ratio))
    runner = SimpleNamespace(screen=SimpleNamespace(backend=SimpleNamespace(info=lambda: SimpleNamespace(client=client))))
    before = window.frameGeometry().top()
    window.keep_clear_of_game_hud(runner)
    app.processEvents()
    if moved:
        assert window.frameGeometry().top() >= 162 and window.frameGeometry().left() == x
    else:
        assert window.frameGeometry().top() == before
    window.close()


def test_prestige_reserve_works_after_node_purchases_without_costs(tmp_path):
    ledger = SeriesBudget(tmp_path / 'm.json', dict(role='killer', character='dredge', max_bp=300000, max_nodes=100,
                          max_prestiges=2, allow_prestige=True, allow_chain=False))
    ledger.reserve(100000, None, 'single', [(1, 1)])
    ledger.reserve(95000, 20000, 'prestige', [(2, 2)])
    assert ledger.data['reservations'][1]['upper_bound'] == 20000


def test_prestige_failing_before_the_button_goes_down_leaves_nothing_pending():
    device, runner, before = prestige_engine([web()])
    runner.mouse.down_attempt_sequence = 0
    def refuse(*args):
        raise ScreenError('chain_budget_limit')
    device.purchase = refuse
    with pytest.raises(ScreenError, match='chain_budget_limit'):
        activate_ready(runner, before, ready_scene())
    assert not runner.pending_action and runner.scene_record is None and not runner.attempted
