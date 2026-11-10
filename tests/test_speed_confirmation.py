"""Cheap local confirmation, one map read per purchase, fewer dialog checks."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.core.models import NodeState
from src.core.screen_manager import ScreenError
from src.core.series_budget import SeriesBudget
from src.core.target_evidence import local_target_check
from tests.test_automation import Device, engine, web


def leveled(state=NodeState.AVAILABLE, level=20):
    result = web(state)
    result.level = level
    result.level_confidence = .99
    return result


class LocalDevice(Device):
    """Full reads come from `results`; local captures report the ring colour."""
    def __init__(self, results, local_red):
        super().__init__(results)
        self.local_red = list(local_red)
        self.local_calls = 0
        self.full_reads = 0
        self.sequence = None

    def capture(self):
        self.full_reads += 1
        return replace(super().capture(), level=20, level_confidence=.99)

    def capture_confirmation(self, before):
        self.local_calls += 1
        frame = replace(super().capture(), level=20, level_confidence=.99, confirmation_only=True,
                        visibility={'confirmation_anchor': True})
        self.index -= 1  # A local capture does not consume a full-read result.
        if self.sequence is not None:
            self.sequence[:] = [self.local_red.pop(0) if self.local_red else True]
        return frame


@pytest.fixture
def red_from_device(monkeypatch):
    def fractions(hsv, x, y, radius):
        red = fractions.sequence[0] if fractions.sequence else fractions.red
        return (.9, 0.) if red else (0., .9)
    fractions.red = True
    fractions.sequence = []
    monkeypatch.setattr('src.core.target_evidence.ring_fractions', fractions)
    return fractions


def test_local_check_reports_red_pending_or_needs_a_full_read(red_from_device):
    device = LocalDevice([leveled()], [True])
    runner = engine(device, dry_run=False)
    before = device.capture()
    node = leveled().nodes[1]
    frame, result, _ = local_target_check(runner, before, node)
    assert result.details['confirmation_only'] and result.nodes[0].state == NodeState.PURCHASED
    red_from_device.red = False
    frame, result, plan = local_target_check(runner, before, node)
    assert result is None and plan is None
    device.capture_confirmation = lambda b: replace(b, level=21, confirmation_only=True, visibility={'confirmation_anchor': True})
    assert local_target_check(runner, before, node) is None


def test_purchase_needs_one_local_red_and_one_normal_read(red_from_device):
    results = [leveled(), leveled(), leveled(NodeState.PURCHASED), leveled(NodeState.PURCHASED)]
    device = LocalDevice(results, [False, False, True])
    device.sequence = red_from_device.sequence
    runner = engine(device, dry_run=False, max_actions=1)
    runner.run()
    assert len(device.presses) == 1 and runner.actions == 1
    assert device.local_calls == 3
    # Two stable reads before the click, one normal read after it.
    assert device.full_reads == 3
    latency = [e for e in runner.report.entries if e['stage'] == 'purchase_red_latency']
    assert latency and latency[0]['local_polls'] == 3


def test_local_check_never_counts_twice_and_a_non_red_read_is_not_success(red_from_device):
    results = [leveled(), leveled(), leveled(), leveled(NodeState.PURCHASED), leveled(NodeState.PURCHASED)]
    device = LocalDevice(results, [True])
    device.sequence = red_from_device.sequence
    runner = engine(device, dry_run=False, max_actions=1)
    runner.run()
    assert len(device.presses) == 1 and runner.actions == 1 and device.local_calls == 1


def test_dialog_check_is_skipped_only_mid_level():
    device = Device([leveled()])
    runner = engine(device, dry_run=False)
    runner.read()
    assert device.dialog_check_needed is True  # One open node left: a dialog can follow.
    many = leveled()
    many.nodes.append(replace(many.nodes[1], id=2, x=300))
    device.results = [many]
    device.index = 0
    runner.read()
    assert device.dialog_check_needed is False
    runner.reset_prepared_state('test')
    assert device.dialog_check_needed is True


def test_fallback_frames_are_kept_in_the_background_and_bounded(tmp_path):
    device = Device([leveled()])
    runner = engine(device, dry_run=False)
    jobs = []
    runner.evidence_writer = SimpleNamespace(submit=lambda identity, write: jobs.append(write))
    runner.report.path = tmp_path / 'run.json'
    frame = device.capture()
    for _ in range(45):
        runner.keep_fallback_frame(frame, 'node_frame_changed')
    assert len(jobs) == 40
    jobs[0]()
    assert (tmp_path / 'run' / 'opening_fallbacks' / '000_node_frame_changed.png').is_file()


def test_spend_only_bp_cap_stops_at_the_observed_spend(tmp_path):
    ledger = SeriesBudget(tmp_path / 'l.json', dict(role='killer', character='dredge', max_bp=1000, max_nodes=100,
                          allow_prestige=False, allow_chain=False))
    ledger.reserve(5000, None, 'single', [(1, 1)])
    ledger.observe(4500)
    ledger.reserve(4500, None, 'single', [(2, 2)])
    ledger.observe(4000)
    with pytest.raises(ScreenError, match='chain_budget_limit'):
        ledger.reserve(None, None, 'single', [(3, 3)])


def test_waiting_reads_retry_a_covered_frame_without_input():
    device = Device([leveled()])
    runner = engine(device, dry_run=False)
    calls = []
    def read():
        calls.append(1)
        if len(calls) == 1:
            raise ScreenError('bloodweb_occluded')
        return device.capture(), leveled(), None
    runner.read = read
    frame, result, _ = runner.recover_read()
    assert len(calls) == 2 and not device.presses


def test_own_window_does_not_fail_the_region_check_but_others_do():
    from src.core.screen_manager import Rect
    panel, other = Rect(1272, 162, 656, 877), Rect(1000, 200, 400, 400)
    protected = Rect(300, 100, 1000, 900)
    own = [panel]
    assert not any(protected.intersects(r) for r in [panel] if r not in own)
    assert any(protected.intersects(r) for r in [panel, other] if r not in own)


def test_missing_centre_anchor_right_after_a_click_means_poll_again(red_from_device):
    device = LocalDevice([leveled()], [True])
    runner = engine(device, dry_run=False)
    before = device.capture()
    device.capture_confirmation = lambda b: replace(b, confirmation_only=True, visibility={'confirmation_anchor': False})
    frame, result, plan = local_target_check(runner, before, leveled().nodes[1])
    assert result is None and plan is None
