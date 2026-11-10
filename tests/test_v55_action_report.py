import json
from src.core.action_report import ActionReport


def test_cursor_details_are_batched_but_every_button_and_final_outcome_flushes(monkeypatch,tmp_path):
    now=[10.];monkeypatch.setattr('src.core.action_report.time.monotonic',lambda:now[0])
    report=ActionReport(tmp_path/'report.json',flush_interval=.2)
    report.add('before_purchase',point=[1,2])
    for i in range(20):report.add('cursor_api',ordinal=i)
    assert len(json.loads(report.path.read_text())['entries'])==1
    report.add('SendInput',flag=2,result=1)
    assert len(json.loads(report.path.read_text())['entries'])==22
    report.add('SendInput',flag=4,result=1)
    assert json.loads(report.path.read_text())['entries'][-1]['flag']==4
    report.add('cursor_api',ordinal=20);report.add('run_counters',attempts=1)
    assert len(json.loads(report.path.read_text())['entries'])==25


def test_periodic_flush_and_legacy_immediate_mode(monkeypatch,tmp_path):
    now=[10.];monkeypatch.setattr('src.core.action_report.time.monotonic',lambda:now[0])
    report=ActionReport(tmp_path/'batched.json',flush_interval=.2)
    report.add('cursor_api',ordinal=0);now[0]+=.21;report.add('cursor_api',ordinal=1)
    assert len(json.loads(report.path.read_text())['entries'])==2
    legacy=ActionReport(tmp_path/'legacy.json')
    legacy.add('cursor_api',ordinal=0);legacy.add('cursor_api',ordinal=1)
    assert len(json.loads(legacy.path.read_text())['entries'])==2
