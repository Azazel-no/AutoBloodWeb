"""BLOODWEB_FIXES A + C: the three live Wraith pauses do not stop a run; the BP counter is not used."""
import time

import numpy as np
import pytest

from src.core.automation import Automation, Settings, State
from src.core.level_transition import level_header_moved
from src.core.models import Analysis, Node, NodeState, NodeType, ScreenState
from src.core.recovery_supervisor import PAUSE, RETRY, WAIT, classify
from src.core.scene_memory import record_scene
from src.core.screen_manager import CapturedFrame, Rect, ScreenError
from tests.test_automation import Device, web


def wide_web(state=NodeState.AVAILABLE, level=None):
    nodes = [Node(0, 200, 200, 20, NodeState.CENTER, NodeType.CENTER, state_confidence=.95, geometry_confidence=.95)]
    for i, (x, y) in enumerate(((280, 200), (120, 200), (200, 280), (200, 120)), 1):
        nodes.append(Node(i, x, y, 20, state, NodeType.ITEM, f'item{i}', state_confidence=.95, geometry_confidence=.95))
    result = Analysis(400, 400, ScreenState.BLOODWEB, nodes, [(0, i) for i in range(1, 5)], confidence=.95)
    if level is not None:
        result.level = level; result.level_confidence = .97
    return result


def live_engine(device, priorities=('target',), **kwargs):
    settings = Settings(startup_delay=0, poll_interval=.01, purchase_timeout=1, refresh_timeout=2, dry_run=False, **kwargs)
    engine = Automation(device, device, device, list(priorities), settings, approved=True, hotkeys_ready=True)
    engine.events = []
    original = engine.event
    def event(state, message, packet):
        engine.events.append((state, message))
        return original(state, message, packet)
    engine.event = event
    return engine


def test_bp_counter_is_off_by_default_and_never_checked():
    assert Settings().bloodpoint_checks is False
    device = Device([web()])
    engine = live_engine(device)
    engine.check_bloodpoint_observation = lambda frame: pytest.fail('BP counter checked')
    frame = engine.capture_frame()  # A frame without any BP reading is fine.
    assert frame.bloodpoints is None


def test_the_bp_check_is_still_available_for_developers():
    engine = live_engine(Device([web()]), bloodpoint_checks=True)
    checked = []
    engine.check_bloodpoint_observation = checked.append
    engine.capture_frame()
    assert len(checked) == 1


def test_codes_from_the_live_pauses_no_longer_stop_the_run():
    for code in ('outcome_context_changed', 'outcome_evidence_required', 'bloodweb_transition_started',
                 'live_frame_required', 'mouse_operation_busy', 'automatic_unlock_entity'):
        assert classify(code) == RETRY, code
    assert classify('focus_wait_timeout') == WAIT
    # Real stops stay stops.
    for code in ('mouse_being_moved', 'mouse_still_held', 'sendinput_failed', 'outcome_confirmed_state_contradiction'):
        assert classify(code) == PAUSE, code


def test_a_level_header_read_twice_retires_a_pending_click():
    """16:57:45: the click finished level 41; both new frames read level 42."""
    device = Device([web()])
    frame = device.capture()
    old = wide_web(level=41)
    record = record_scene(frame, old, [])
    new_a, new_b = wide_web(level=42), wide_web(level=42)
    assert level_header_moved(record, (device.capture(), new_a, None), new_b)
    unread = wide_web(level=42); unread.level = None
    anonymous = wide_web(level=42)
    for n in anonymous.nodes: n.icon_id = None
    assert not level_header_moved(record, (device.capture(), anonymous, None), new_b)  # Identity unproven.
    assert not level_header_moved(record, (device.capture(), unread, None), new_b)
    assert not level_header_moved(record, (device.capture(), wide_web(level=43), None), new_b)
    assert not level_header_moved(record, None, new_b)


class MovingCursor(Device):
    """The cursor is moved away just before every MouseDown; nothing is pressed."""
    def __init__(self, results):
        super().__init__(results); self.down_attempt_sequence = 0; self.moves = 0
    def purchase(self, point, duration, stop, check):
        check(); self.moves += 1
        raise ScreenError('cursor_position_mismatch')


def test_a_moved_cursor_is_retried_and_only_repeated_movement_pauses():
    device = MovingCursor([web()])
    engine = live_engine(device)
    engine.run()
    assert device.presses == [] and device.moves == 4  # Three retries, then the clear stop.
    assert engine.state == State.PAUSED and (State.PAUSED, 'mouse_being_moved') in engine.events, engine.events[-3:]


def test_three_verified_unbought_nodes_stop_with_out_of_bloodpoints(monkeypatch):
    """No BP counter: three different nodes verified as not bought (each retried once) mean no bloodpoints."""
    clock = [100.]
    monkeypatch.setattr(time, 'monotonic', lambda: clock[0])
    class Replay(Device):
        def capture(self):
            clock[0] += .35; return super().capture()
        def analyze(self, image, source):
            return wide_web()
    device = Replay([wide_web()])
    engine = live_engine(device, priorities=['item1', 'item2', 'item3', 'item4'])
    engine.settings.purchase_timeout = 3; engine.wait = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    engine.run()
    assert (State.INSUFFICIENT_BP, 'out_of_bloodpoints') in engine.events, engine.events[-4:]
    pressed = {p for p, _ in device.presses}
    assert len(pressed) == 3 and len(device.presses) == 6 and engine.actions == 0
