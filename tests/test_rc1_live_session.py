import json
import time
from dataclasses import replace
import pytest
from tests.test_automation import Device,engine,web
from src.core.live_session import LiveSession
from src.core.scene_memory import record_scene
from src.core.screen_manager import ScreenError


def session(tmp_path,max_bp=10000,max_nodes=2,character='dark_lord'):
    a=dict(role='killer',character=character,max_bp=max_bp,max_nodes=max_nodes,max_commands=4,
           allow_prestige=False,allow_chain=True,allow_turbo_burst=True,
           started_at=time.time(),expires_at=time.time()+60)
    path=tmp_path/'authorization.json';path.write_text(json.dumps(a),encoding='utf-8')
    s=LiveSession(path);d=Device([web()]);d.role='killer'
    e=engine(d,dry_run=False,native_chain=True,turbo_burst=True);e.settings.character=character
    f=replace(d.capture(),bloodpoints=10000)
    e.recent_frames.append((f,web(),None));e.direct_single_price_bound=5000
    e.scene_record=record_scene(f,web(),[],dict(point=list(f.to_screen(230,200)),radius=20,icon='target'))
    s.install(e)
    return s,e,d,f


def test_gui_limits_commit_before_input_and_survive_restart(tmp_path):
    s,e,d,f=session(tmp_path,max_nodes=1)
    e.mouse.purchase(f.to_screen(230,200),.05,e.stop,lambda:None)
    assert len(d.presses)==1 and s.budget.data['commands']==1
    assert len(s.budget.data['reservations'][0]['points'])==1
    s.lock.close()
    replacement=LiveSession(s.path)
    assert replacement.budget.data['commands']==1
    with pytest.raises(ScreenError,match='action_limit'):
        replacement.budget.reserve(10000,5000,'single',[(1,2)])
    assert not replacement.budget.data['reservations'][0]['settled']
    replacement.lock.close()


@pytest.mark.parametrize('reason',['time','scope','bp','nodes'])
def test_gui_scope_failure_sends_no_input(tmp_path,reason):
    s,e,d,f=session(tmp_path,max_bp=4000 if reason=='bp' else 10000)
    if reason=='time':s.authorization['expires_at']=time.time()-1
    if reason=='scope':e.settings.character='knight'
    if reason=='nodes':s.budget.max_nodes=0
    if reason=='bp':  # Developer option: a BP cap applies only with the BP counter on.
        e.settings.bloodpoint_checks=True;s.budget.authorization=dict(s.budget.authorization,max_bp=4000)
    with pytest.raises(ScreenError):e.mouse.purchase(f.to_screen(230,200),.05,e.stop,lambda:None)
    assert not d.presses and s.budget.data['commands']==0
    s.lock.close()


def test_finish_waits_for_pending_but_stop_remains_immediate(tmp_path):
    s,e,d,f=session(tmp_path)
    (tmp_path/'finish.request').write_text('finish',encoding='utf-8')
    e.pending_action=True;e.live_safety()
    e.pending_action=False
    with pytest.raises(InterruptedError):e.live_safety()
    e.pending_action=True;(tmp_path/'stop.request').write_text('stop',encoding='utf-8')
    with pytest.raises(InterruptedError):e.live_safety()
    assert not d.presses
    s.lock.close()


def test_final_window_event_does_not_erase_the_actual_pause_reason(tmp_path):
    s,e,d,f=session(tmp_path)
    s.status(e,'paused','chain_unexpected_purchase');s.status(e,'paused','restore_window')
    record=json.loads((tmp_path/'status.json').read_text())
    assert record['message']=='chain_unexpected_purchase' and record['ui_event']=='restore_window'
    s.last_status=0;s.status(e,'automation_ready','')
    assert json.loads((tmp_path/'status.json').read_text())['message']==''
    assert not d.presses
    s.lock.close()


@pytest.mark.parametrize('character',['skull_merchant','executioner','dredge'])
def test_explicit_skull_merchant_scope_uses_same_durable_limits(tmp_path,character):
    s,e,d,f=session(tmp_path,max_nodes=1,character=character)
    e.mouse.purchase(f.to_screen(230,200),.05,e.stop,lambda:None)
    s.lock.close()
    resumed=LiveSession(s.path)
    assert resumed.authorization['character']==character
    assert resumed.budget.data['commands']==1 and len(d.presses)==1
    with pytest.raises(ScreenError,match='action_limit'):
        resumed.budget.reserve(10000,5000,'single',[(1,2)])
    resumed.lock.close()


