from dataclasses import replace
import pytest
from tests.test_automation import Device,web,engine
from src.core.bloodpoint_evidence import BloodpointEvidence
from src.core.screen_manager import ScreenError


def test_single_4bp_frame_cannot_poison_the_aggregate_ledger():
    d=Device([web()]);f=d.capture();evidence=BloodpointEvidence(4169449)
    with pytest.raises(ScreenError,match='frame_unstable'):
        evidence.observe(replace(f,bloodpoints=4),5750)
    assert evidence.last==4169449
    evidence.observe(replace(f,bloodpoints=4163699,captured_at=f.captured_at+.1),5750)
    assert evidence.last==4163699 and evidence.candidate is None


def test_real_repeated_excess_debit_is_critical_and_never_authorizes_input():
    d=Device([web()]);f=replace(d.capture(),bloodpoints=4);evidence=BloodpointEvidence(4169449)
    for offset in (0,.1,.2):
        with pytest.raises(ScreenError,match='frame_unstable'):evidence.observe(replace(f,captured_at=f.captured_at+offset),5750)
    with pytest.raises(ScreenError,match='bp_changed_unexpectedly'):
        evidence.observe(replace(f,captured_at=f.captured_at+.7),5750)
    assert evidence.last==4169449 and not d.presses


def test_duplicate_capture_does_not_confirm_bp_anomaly():
    d=Device([web()]);f=replace(d.capture(),bloodpoints=4);evidence=BloodpointEvidence(4169449)
    for _ in range(5):
        with pytest.raises(ScreenError,match='frame_unstable'):evidence.observe(f,5750)
    assert evidence.candidate['count']==1


def test_ordinary_capture_guard_uses_native_chain_total_without_guessing_bp():
    d=Device([web()]);e=engine(d,dry_run=False);e.bp_evidence=BloodpointEvidence(4169449)
    e.scene_record={'pending':{'chain':{'total_bp':5750}}}
    with pytest.raises(ScreenError,match='frame_unstable'):
        e.check_bloodpoint_observation(replace(d.capture(),bloodpoints=4))
    assert e.bp_evidence.last==4169449 and not d.presses
