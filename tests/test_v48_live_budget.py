"""Bounded launcher permission checks only; no GUI or native mouse creation."""
import json
from dataclasses import replace
import pytest
from scripts.live_test_v48 import read_authorization,acquire_scope,local_point
from scripts.live_test_v47 import InputBudget
from tests.test_automation import Device,web
from src.core.screen_manager import Rect

def authorization():return dict(approved=True,authorized_limit=20,max_seconds=300,allow_prestige=False,allow_central_unlock=True,role='killer',character='singularity')

@pytest.mark.parametrize('field,value',[('approved',False),('authorized_limit',21),('authorized_limit',True),('max_seconds',301),('allow_prestige',True),('role','unknown')])
def test_new_permission_is_required_and_cannot_expand_scope(tmp_path,field,value):
    path=tmp_path/'authorization.json';data=authorization();data[field]=value;path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError):read_authorization(path)

def test_missing_and_valid_bounded_permission(tmp_path):
    path=tmp_path/'authorization.json'
    with pytest.raises(ValueError,match='new_v48_authorization_required'):read_authorization(path)
    path.write_text(json.dumps(authorization()),encoding='utf-8');assert read_authorization(path)['authorized_limit']==20
    old=tmp_path/'old_budget.json';old.write_text(json.dumps(dict(authorized_limit=50,used=50)),encoding='utf-8')
    with pytest.raises(ValueError):InputBudget(20,old)

def test_one_process_owns_the_permission_scope(tmp_path):
    path=tmp_path/'authorization.json';first=acquire_scope(path)
    try:
        with pytest.raises(ValueError,match='permission_scope_already_running'):acquire_scope(path)
    finally:first.close()
    next_handle=acquire_scope(path);next_handle.close()

def test_evidence_io_revalidation_keeps_negative_and_scaled_coordinates():
    frame=Device([web()]).capture();frame=replace(frame,roi=Rect(15,20,360,360))
    point=frame.to_screen(230,200);assert frame.to_screen(*local_point(frame,point))==point

def test_opening_only_series_cannot_buy_after_fallback():
    from types import SimpleNamespace
    from scripts.live_test_v48 import check_opening_scope
    from src.core.screen_manager import ScreenError
    engine=SimpleNamespace(opening=SimpleNamespace(active=False))
    with pytest.raises(ScreenError,match='fast_opening_required'):
        check_opening_scope(dict(require_fast_opening=True),engine)
    engine.opening.active=True
    check_opening_scope(dict(require_fast_opening=True),engine)
    engine.opening.active=False
    check_opening_scope({},engine)

def test_opening_guard_precedes_pending_scene_and_attempt_count():
    from types import SimpleNamespace
    from scripts.live_test_v48 import opening_scoped_buy
    from src.core.screen_manager import ScreenError
    engine=SimpleNamespace(opening=SimpleNamespace(active=False),attempts=0,pending=False)
    def buy():engine.attempts+=1;engine.pending=True
    wrapped=opening_scoped_buy(dict(require_fast_opening=True),engine,buy)
    with pytest.raises(ScreenError):wrapped()
    assert engine.attempts==0 and not engine.pending
    engine.opening.active=True;wrapped()
    assert engine.attempts==1 and engine.pending
