"""BLOODWEB_FIXES B + C: centre auto-purchase when nothing is a priority; stop after N levels."""
import time

import pytest

from src.core.automation import Automation, Settings, State
from src.core.early_unlock import center_level, center_mode, purchase_level
from src.core.models import NodeState
from tests.test_automation import Device, web
from tests.test_zero_pauses import wide_web


def make(device, priorities=('item1',), **kwargs):
    settings = Settings(startup_delay=0, poll_interval=.01, purchase_timeout=1, refresh_timeout=15, dry_run=False, **kwargs)
    engine = Automation(device, device, device, list(priorities), settings, approved=True, hotkeys_ready=True)
    engine.events = []
    original = engine.event
    engine.event = lambda s, m, p: (engine.events.append((s, m)), original(s, m, p))[1]
    return engine


def test_stop_after_n_levels_before_touching_the_next_level():
    device = Device([web()])
    engine = make(device, priorities=['target'], max_levels=2)
    engine.levels_completed = 2  # Two completed levels (a prestige counts as one: it confirms a new level too).
    engine.run()
    assert (State.STOPPED, 'level_limit') in engine.events and device.presses == []


def test_a_level_confirmed_twice_counts_once():
    """Live 18:45: level 29 was confirmed by the saved-scene check and again by the next-level wait."""
    engine = make(Device([web()]), max_levels=10)
    for source, level in (('persisted_scene_geometry', 29), ('wait_next_bloodweb', 29), ('wait_next_bloodweb', 30),
                          ('prestige', 1), ('wait_next_bloodweb', 2)):
        engine.trace('new_level_confirmed', source=source, level=level)
    assert engine.levels_completed == 4 and engine.transitions == 5


def test_no_limit_by_default():
    assert Settings().max_levels is None


def test_centre_above_level_9_only_without_priority_targets_and_decided_once():
    class Screen(Device):
        capture_overlay = True
    engine = make(Screen([wide_web(level=20)]), priorities=['something_else'])
    assert center_level(engine, wide_web(level=20)) and center_level(engine, wide_web(level=20))
    assert len([e for e in engine.report.entries if e['stage'] == 'center_auto']) == 1
    with_target = make(Screen([wide_web(level=20)]), priorities=['item2'])
    assert not center_level(with_target, wide_web(level=20))
    assert center_level(with_target, wide_web(level=5))  # Levels 1-9 as before.
    with_target.center_auto_unavailable = True
    assert not center_level(make(Screen([wide_web(level=20)]), priorities=[], center_auto_above_9=False), wide_web(level=20))


def test_centre_takes_over_once_the_priorities_are_bought():
    class Screen(Device):
        capture_overlay = True
    result = wide_web(level=20)
    engine = make(Screen([result]), priorities=['item2'])
    assert not center_level(engine, result)
    target = next(n for n in result.nodes if n.icon_id == 'item2')
    target.state = NodeState.PURCHASED
    assert center_level(engine, result)
    decisions = [e for e in engine.report.entries if e['stage'] == 'center_auto']
    assert len(decisions) == 2 and decisions[-1]['reason'] == 'priorities finished'


def test_perks_priority_counts_as_a_priority_target():
    from src.core.priority_targets import PERK_TARGET
    class Screen(Device):
        capture_overlay = True
    from src.core.models import NodeType
    result = wide_web(level=20)
    perk = result.nodes[1]
    perk.type = NodeType.PERK; perk.category_hint = 'perk'; perk.category_confidence = .99
    engine = make(Screen([result]), priorities=[PERK_TARGET])
    assert not center_level(engine, result)  # A perk on the level and PERKS is a priority: buy normally.


class CentreScreen(Device):
    """Fake game screen for a centre click above level 9."""
    capture_overlay = True
    def __init__(self, results, clock):
        super().__init__(results); self.clock = clock; self.down_attempt_sequence = 0
    def capture(self):
        self.clock[0] += .4; frame = super().capture(); frame.level = 20; frame.level_confidence = .99; return frame
    def parking_point(self, frame): return None
    def purchase(self, point, duration, stop, check):
        check(); self.down_attempt_sequence += 1; self.presses.append((point, duration))


def test_when_the_game_does_not_auto_buy_above_level_9_it_buys_node_by_node(monkeypatch):
    clock = [100.]
    monkeypatch.setattr(time, 'monotonic', lambda: clock[0])
    level = wide_web(level=20)
    device = CentreScreen([level], clock)
    engine = make(device, priorities=['nothing_here'])
    engine.wait = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    frame = device.capture()
    assert center_mode(engine, level)
    fresh, web_after, plan = purchase_level(engine, frame, level)
    assert len(device.presses) == 1 and engine.center_auto_unavailable and not engine.pending_action
    assert not center_mode(engine, wide_web(level=21))  # From now on: node by node.
    assert [e for e in engine.report.entries if e['stage'] == 'center_auto_unavailable']


def test_the_entity_may_take_nodes_during_a_centre_purchase_above_level_9(monkeypatch):
    clock = [100.]
    monkeypatch.setattr(time, 'monotonic', lambda: clock[0])
    level = wide_web(level=20)
    taken = wide_web(level=20); taken.nodes[2].state = NodeState.ABSORBED
    bought = wide_web(level=20)
    for n in bought.nodes[1:]: n.state = NodeState.PURCHASED
    bought.nodes[2].state = NodeState.ABSORBED
    device = CentreScreen([level, taken, taken, bought, bought, bought, bought], clock)
    engine = make(device, priorities=['nothing_here'])
    engine.wait = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    frame = device.capture()
    with pytest.raises(Exception) as caught:
        purchase_level(engine, frame, level)  # No next level in this fake: it times out, but never on the Entity.
    assert 'automatic_unlock_entity' not in str(caught.value)
    assert engine.actions == 3 and not getattr(engine, 'center_auto_unavailable', False)
