"""No game input: lifecycle/ownership contracts with the production Mouse."""
import json,threading,time
import pytest
from src.core.click_lifecycle import ClickLifecycle
from src.core.input_control import Mouse
from src.core.screen_manager import ScreenError
from tests.test_input_control import InputStub


def device(tmp_path):
    mouse=Mouse();mouse.u=InputStub();mouse.lifecycle=ClickLifecycle(tmp_path/'clicks.jsonl')
    return mouse


def test_every_actual_aim_reaches_down_and_up_in_same_operation(tmp_path):
    m=device(tmp_path);m.purchase((120,240),.05,threading.Event(),lambda:None)
    rows=[json.loads(x) for x in m.lifecycle.path.read_text().splitlines()]
    stages=[r['stage'] for r in rows]
    assert stages==['SELECT','PREPARE','PREPARED','AIM','CURSOR_ARRIVED','INPUT_READY','MOUSE_DOWN','MOUSE_UP','FINISH']
    assert len({r['operation_id'] for r in rows})==1 and m.u.flags==[2,4]
    assert rows[-1]['result']=='InputSentUnconfirmed'  # API success is not a purchase.


def test_guard_cancellation_records_reason_without_down_or_double_cancellation(tmp_path):
    m=device(tmp_path);checks=[]
    def guard():
        checks.append(1)
        if len(checks)>1:raise ScreenError('stale_frame')
    with pytest.raises(ScreenError,match='stale_frame'):m.purchase((120,240),.05,threading.Event(),guard)
    rows=m.lifecycle.rows;cancel=[r for r in rows if r['stage']=='input_cancelled']
    assert not m.u.flags and len(cancel)==1 and cancel[0]['cancel_reason']=='stale_frame'
    assert cancel[0]['elapsed_since_preparation']>=.1 and rows[-1]['result']=='CancelledByGuard'
    m.purchase((120,240),.05,threading.Event(),lambda:None)
    assert m.u.flags==[2,4]  # A fresh operation can proceed; no automatic blind retry.


def test_other_thread_cannot_park_or_click_while_commit_owns_mouse(tmp_path):
    m=device(tmp_path);other=Mouse();other.u=InputStub();errors=[];calls=[]
    def check():
        calls.append(1)
        if len(calls)==2:
            def interfere():
                for method in (lambda:m.park((9,9),threading.Event(),lambda:None),
                               lambda:other.purchase((7,7),.05,threading.Event(),lambda:None)):
                    try:method()
                    except ScreenError as exc:errors.append(exc.code)
            t=threading.Thread(target=interfere);t.start();t.join(1);assert not t.is_alive()
    m.purchase((120,240),.05,threading.Event(),check)
    assert errors==['mouse_operation_busy']*2 and m.u.positions==[(120,240)]
    assert not other.u.positions and m.u.flags==[2,4]


def test_emergency_release_from_other_thread_keeps_operation_identity(tmp_path):
    m=device(tmp_path);stop=threading.Event()
    t=threading.Thread(target=lambda:m.purchase((120,240),1,stop,lambda:None));t.start()
    end=time.monotonic()+2
    while not m.held and time.monotonic()<end:time.sleep(.002)
    assert m.held;stop.set();m.release();t.join(1)
    down=next(r for r in m.lifecycle.rows if r['stage']=='MOUSE_DOWN')
    up=next(r for r in m.lifecycle.rows if r['stage']=='MOUSE_UP')
    assert down['operation_id']==up['operation_id'] and not m.held and not t.is_alive()


def test_probe_and_parking_never_claim_actual_purchase(tmp_path):
    m=device(tmp_path)
    with m.lifecycle.operation('PRICE_PROBE',dict(point=[120,240])):
        m.park((120,240),threading.Event(),lambda:None)
        m.park((8,540),threading.Event(),lambda:None)
    moves=[r for r in m.lifecycle.rows if r['stage']=='MOVE']
    assert [r['intent'] for r in moves]==['PRICE_PROBE','CURSOR_PARKING'] and not m.u.flags


def test_failed_journal_blocks_input_but_never_blocks_release(tmp_path,monkeypatch):
    m=device(tmp_path);original=m.lifecycle.input_event
    def record(stage,**kw):
        value=original(stage,**kw)
        if stage=='INPUT_READY':m.lifecycle.error='simulated disk failure'
        return value
    monkeypatch.setattr(m.lifecycle,'input_event',record)
    with pytest.raises(ScreenError,match='click_journal_failed'):m.purchase((120,240),.05,threading.Event(),lambda:None)
    assert not m.u.flags and not m.held


@pytest.mark.parametrize('fault',['none','priority','unknown','identity','geometry'])
def test_selected_known_filler_survives_probe_but_never_bypasses_priority(fault):
    import copy
    from tests.test_v51_hybrid import scene
    from tests.test_automation import Device,engine
    from src.core.models import NodeState
    from src.core.path_planner import RoutePlan
    f,r=scene(2);r.edges=[(0,1),(0,2)];r.nodes[2].state=NodeState.AVAILABLE
    for n in r.nodes[1:]:n.icon_id='known_'+str(n.id);n.confidence=.95;n.contour_score=.9;n.icon_margin=.1
    fresh=copy.deepcopy(r)
    if fault=='unknown':r.nodes[1].icon_id=None
    if fault=='identity':fresh.nodes[1].icon_id='changed'
    if fault=='geometry':fresh.nodes[1].geometry_confidence=.7
    e=engine(Device([r]),dry_run=False);e.priorities=[]
    plan=RoutePlan(2,[2],reason='priority' if fault=='priority' else 'filler')
    _,_,target=e.single_after_probe(f,r,r.nodes[1],(f,fresh,plan))
    assert target.id==(1 if fault=='none' else 2)
    assert bool(e.retained_single_target)==(fault=='none')


def test_failed_down_api_is_not_misreported_as_guard_cancellation(tmp_path):
    m=device(tmp_path);m.u.SendInput=lambda *args:0
    with pytest.raises(ScreenError,match='sendinput_failed'):m.purchase((120,240),.05,threading.Event(),lambda:None)
    assert m.lifecycle.rows[-1]['result']=='InputSentUnconfirmed'
    assert not any(r['stage']=='MOUSE_DOWN' for r in m.lifecycle.rows)
    assert any(r['stage']=='INPUT_API_FAILED' for r in m.lifecycle.rows)


def test_preinput_map_guard_recovers_with_new_read_without_assuming_purchase():
    from tests.test_automation import Device,web,engine
    from src.core.path_planner import RoutePlan
    d=Device([web()]);d.down_attempt_sequence=0;e=engine(d,dry_run=False,max_actions=1)
    calls=[]
    def buy(f,r,**kwargs):
        calls.append(1)
        if len(calls)==1:raise ScreenError('chain_map_changed')
        e.actions+=1;return f,r,RoutePlan()
    e.buy=buy;e.run()
    assert len(calls)==2 and not d.presses and e.preparation_recoveries==1
    assert any(r['stage']=='preinput_guard_recovery' and r['down_api_attempted'] is False for r in e.report.entries)


def test_partial_chain_gets_only_one_readonly_recheck_without_repeating_input():
    from tests.test_automation import Device,web,engine
    from src.core.path_planner import RoutePlan
    d=Device([web()]);e=engine(d,dry_run=False);e.pending_action=True
    e.scene_record=dict(pending=dict(operation_id='pending-chain',chain={'ordered_nodes':[1,2,3]},chain_outcome={'confirmed':[1,2]}))
    calls=[];e.recover_read=lambda *a,**k:(d.capture(),web(),RoutePlan())
    def resolve(*args):
        calls.append(1)
        if len(calls)<2:return False
        e.scene_record=dict(pending=None,last_outcome={'confirmed_nodes':3});e.pending_action=False;e.persisted_scene=None
        return True
    e.recheck_saved_scene=resolve
    assert e.recover_chain_readonly() and len(calls)==2 and not d.presses and e.actions==1
    e.pending_action=True;e.scene_record=dict(pending=dict(operation_id='pending-chain',chain={1:1}))
    assert e.recover_chain_readonly() is None and len(calls)==2


def test_price_preparation_returns_two_current_higher_priority_maps_instead_of_rehover(root):
    from tests.test_v59_frame import filler_price_case
    from types import SimpleNamespace
    from src.core.path_planner import RoutePlan
    from src.core.automation import Automation
    from scripts.live_test_v54 import wait_price_scene
    request,observations=filler_price_case(root);reads=iter([(f,r,RoutePlan(3,[3],reason='smart_priority')) for f,r in observations])
    e=SimpleNamespace(settings=SimpleNamespace(purchase_timeout=1),runtime_deadline=time.monotonic()+2,
        gate=lambda:None,recover_read=lambda *a,**k:next(reads),poll=lambda:None,trace=lambda *a,**k:None,stable_scene=Automation.stable_scene)
    with pytest.raises(ScreenError,match='purchase_preparation_changed'):
        wait_price_scene(e,request['scene'],request['targets'][0]['point'],True)
    assert e.preparation_changed_without_input and e.preparation_replacement[0].captured_at==observations[-1][0].captured_at


def test_diagnostic_renumbering_is_not_reported_as_a_physical_target_change():
    from tests.test_automation import Device,web,engine
    from src.core.models import NodeState
    d=Device([web(NodeState.PURCHASED),web(NodeState.PURCHASED)]);e=engine(d,dry_run=False)
    f=d.capture();r=web();n=r.nodes[1];e.signature=f.window.signature
    selected=dict(node=99,icon=n.icon_id,point=f.to_screen(n.x,n.y),level=r.level,
        window=f.window.signature,frame_stamp=f.captured_at-.1)
    with e.click_lifecycle.operation('ACTUAL_PURCHASE',selected):e._buy_once(f,r,node=n)
    assert any(x['stage']=='TARGET_REVALIDATED' for x in e.click_lifecycle.rows)
    assert not any(x['stage']=='TARGET_CHANGED' for x in e.click_lifecycle.rows)
    assert e.actions==1 and len(d.presses)==1


def test_critical_chain_block_never_enters_late_recovery():
    from tests.test_automation import Device,web,engine
    d=Device([web()]);e=engine(d,dry_run=False);e.pending_action=True
    e.scene_record=dict(pending=dict(operation_id='blocked',chain={'ordered_nodes':[1,2]},chain_blocked_reason='chain_bp_exceeded'))
    assert e.recover_chain_readonly() is None and d.index==0 and not d.presses
