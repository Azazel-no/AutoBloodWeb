"""Renewed scope charges every possible centre node before input. No game IO."""
import json,time
import pytest
from scripts.live_test_v54 import OperationBudget,NativeReview,validate_authorization
from scripts.live_test_v48 import read_authorization
from src.core.screen_manager import ScreenError


def grant():return dict(version='v54',approved=True,authorized_limit=48,max_nodes=48,max_bp=240000,max_seconds=600,
    role='killer',character='animatronic',priorities=[],allow_prestige=False,allow_chain=False,allow_central_unlock=True)


def test_restarted_price_review_preserves_previous_evidence(tmp_path):
    old=tmp_path/'price_review_001';old.mkdir()
    source=old/'request.json';source.write_text('original evidence',encoding='utf-8')
    review=NativeReview(None,tmp_path)
    assert review.serial==1
    assert source.read_text(encoding='utf-8')=='original evidence'


def test_native_price_survey_hides_panel_and_requires_new_clean_capture(tmp_path):
    from types import SimpleNamespace
    calls=[]
    def recover(**kwargs):
        assert kwargs.get('retry_stale') is True
        calls.append('fresh_capture')
        raise ScreenError('game_focus_lost')
    engine=SimpleNamespace(state='wait_stable',event=lambda *args:calls.append(args),
        wait=lambda delay:calls.append(delay),recover_read=recover)
    review=NativeReview(engine,tmp_path)
    with pytest.raises(ScreenError,match='game_focus_lost'):
        review.prepare(None,None,None,[],'early_center')
    assert calls==[('wait_stable','hide_for_dialog',None),.4,'fresh_capture']
    assert review.permit is None
    assert list(tmp_path.iterdir())==[]


def test_centre_reserves_all_nodes_and_price_atomically_and_keeps_them_after_restart(tmp_path):
    p=tmp_path/'budget.json';a=grant();b=OperationBudget(p,a,time.monotonic()+10)
    b.reserve(3000000,[[1,2],[3,4],[5,6]],10000,'early_center',[])
    restarted=OperationBudget(p,a,time.monotonic()+10)
    assert (restarted.data['commands'],restarted.data['nodes'],restarted.data['bp'])==(1,3,10000)
    assert restarted.data['reservations'][0]['points']==[[1,2],[3,4],[5,6]]
    with pytest.raises(ScreenError,match='chain_bp_exceeded'):restarted.check(2989999,1,2000)


@pytest.mark.parametrize('field,value',[('max_nodes',49),('max_bp',240001),('max_seconds',601),('allow_prestige',True),('allow_chain',True),('allow_central_unlock',False)])
def test_renewal_cannot_expand_authorization(field,value):
    a=grant();a[field]=value
    with pytest.raises(ValueError):validate_authorization(a)


def test_limits_unknown_bp_and_corrupt_restart_fail_before_reservation(tmp_path):
    a=grant();a['max_nodes']=2;a['max_bp']=5000;p=tmp_path/'b.json';b=OperationBudget(p,a,time.monotonic()+10)
    for bp,nodes,price,reason in [(None,1,2000,'chain_bp_unproven'),(3000000,3,2000,'action_limit'),(3000000,2,6000,'chain_budget_limit')]:
        with pytest.raises(ScreenError,match=reason):b.reserve(bp,[[1,2]]*nodes,price,'center',[])
    assert not p.exists()
    p.write_text('broken',encoding='utf-8')
    with pytest.raises(ScreenError,match='invalid_chain_ledger'):OperationBudget(p,a,time.monotonic()+10)


def test_expired_deadline_cannot_charge_and_launcher_requires_new_scope(tmp_path):
    a=grant();p=tmp_path/'grant.json';p.write_text(json.dumps(a),encoding='utf-8')
    assert read_authorization(p)==a
    b=OperationBudget(tmp_path/'b.json',a,time.monotonic()-1)
    with pytest.raises(ScreenError,match='runtime_limit'):b.reserve(3000000,[[1,2]],2000,'single',[])
