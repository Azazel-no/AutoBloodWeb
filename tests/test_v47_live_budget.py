import threading
import pytest
from scripts.live_test_v47 import InputBudget
from src.core.screen_manager import ScreenError

def test_permission_budget_counts_retries_and_survives_new_worker():
    budget=InputBudget(50);calls=[]
    def purchase(*args):calls.append(args)
    for i in range(50):budget.invoke(purchase,(i,0),.25,threading.Event(),lambda:None)
    with pytest.raises(ScreenError,match='action_limit'):budget.invoke(purchase,(51,0),.25,threading.Event(),lambda:None)
    assert len(calls)==50
    # A new worker obtains the same session budget, never another 50 calls.
    with pytest.raises(ScreenError,match='action_limit'):budget.invoke(lambda *a:None,(52,0),.25,threading.Event(),lambda:None)
    assert budget.used==50

def test_permission_ledger_survives_process_restart_and_counts_failed_call(tmp_path):
    import json
    path=tmp_path/'permission.json';path.write_text(json.dumps(dict(authorized_limit=50,used=20)))
    budget=InputBudget(ledger=path)
    def failed(*args):raise RuntimeError('input outcome not known')
    with pytest.raises(RuntimeError):budget.invoke(failed,(1,1),.25,threading.Event(),lambda:None)
    assert InputBudget(ledger=path).used==21
    for i in range(29):budget.invoke(lambda *a:None,(1,1),.25,threading.Event(),lambda:None)
    reloaded=InputBudget(ledger=path)
    with pytest.raises(ScreenError,match='action_limit'):reloaded.invoke(lambda *a:None,(1,1),.25,threading.Event(),lambda:None)
    assert reloaded.used==50
