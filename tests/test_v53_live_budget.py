"""No-input scope and atomic reservation tests for the new Animatronic grant."""
import copy
import json
import time
from types import SimpleNamespace
import pytest
from scripts.live_test_v53 import SingleBudget,validate_authorization,standard_target,install_limits
from scripts.live_test_v48 import read_authorization,acquire_scope
from src.core.models import Node,NodeState,NodeType
from src.core.screen_manager import ScreenError


def grant():
    return dict(version='v53',approved=True,authorized_limit=50,max_nodes=50,max_bp=250000,max_seconds=600,
        role='killer',character='animatronic',priorities=[],allow_prestige=False,allow_central_unlock=False,allow_chain=False)


@pytest.mark.parametrize('field,value',[('authorized_limit',51),('max_nodes',51),('max_bp',250001),
    ('max_seconds',601),('character','unknown'),('allow_prestige',True),('allow_chain',True),('allow_central_unlock',True)])
def test_new_scope_cannot_expand_approval(field,value):
    data=grant();data[field]=value
    with pytest.raises(ValueError):validate_authorization(data)


def test_native_launcher_accepts_only_new_version_for_fifty(tmp_path):
    path=tmp_path/'authorization.json';path.write_text(json.dumps(grant()),encoding='utf-8')
    assert read_authorization(path)['authorized_limit']==50
    data=grant();data['version']='v52';path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError):read_authorization(path)


def test_all_fifty_reservations_persist_and_failed_input_is_not_refunded(tmp_path):
    path=tmp_path/'resource.json';authorization=grant();deadline=time.monotonic()+10
    first=SingleBudget(path,authorization,deadline)
    first.reserve(3000000,(100,200),'item')
    budget=SingleBudget(path,authorization,deadline)
    assert budget.data['reserved_commands']==1
    for _ in range(49):budget.reserve(2998000,(100,200),'item')
    with pytest.raises(ScreenError,match='action_limit'):SingleBudget(path,authorization,deadline).reserve(2998000,(100,200),'item')
    assert json.loads(path.read_text(encoding='utf-8'))['reserved_bp']==250000


@pytest.mark.parametrize('change,reason',[('unknown','chain_bp_unproven'),('time','runtime_limit'),
    ('bp','chain_budget_limit'),('corrupt','invalid_chain_ledger'),('scope','chain_ledger_scope_mismatch')])
def test_unknown_or_invalid_scope_never_reserves(tmp_path,change,reason):
    path=tmp_path/'budget.json';data=grant();deadline=time.monotonic()+10
    budget=SingleBudget(path,data,deadline);budget.reserve(3000000,(1,2),'known')
    if change=='time':deadline=time.monotonic()-1
    if change=='bp':data['max_bp']=5000;budget=SingleBudget(tmp_path/'small.json',data,deadline);budget.reserve(3000000,(1,2),'known')
    if change=='corrupt':path.write_text('invalid json',encoding='utf-8')
    if change=='scope':data['priorities']=['any_perk']
    with pytest.raises(ScreenError,match=reason):
        if change!='bp':budget=SingleBudget(path,data,deadline)
        budget.reserve(None if change=='unknown' else 3000000,(1,2),'known')


def test_event_and_anonymous_cannot_use_standard_node_price_bound():
    detector=SimpleNamespace(library=SimpleNamespace(event_metadata={'event':{}}),
        matcher=SimpleNamespace(by_id={'known':object(),'event':object()}))
    engine=SimpleNamespace(detector=detector)
    node=Node(1,100,100,20,NodeState.AVAILABLE,NodeType.OFFERING,'known',confidence=.9,
        contour_score=.8,rarity='green')
    standard_target(engine,node)
    for ident in (None,'event'):
        other=copy.deepcopy(node);other.icon_id=ident
        with pytest.raises(ScreenError,match='single_standard_price_bound_unproven'):standard_target(engine,other)
    node.type=NodeType.PERK;node.icon_id=None;node.category_confidence=.95
    standard_target(engine,node)
    node.orange_background=True
    with pytest.raises(ScreenError):standard_target(engine,node)


def test_user_can_change_priorities_within_animatronic_scope(tmp_path):
    entries={ident:SimpleNamespace(role='killer',character=character) for ident,character in
        [('known',None),('addon','animatronic'),('foreign','onryo')]}
    called=[]
    runner=SimpleNamespace(settings=SimpleNamespace(character='animatronic'),priorities=['addon','any_perk'],
        detector=SimpleNamespace(library=SimpleNamespace(event_metadata={}),matcher=SimpleNamespace(by_id=entries)),
        mouse=SimpleNamespace(purchase=lambda *a:None),buy=lambda *a,**kw:called.append(kw))
    install_limits(runner,grant(),tmp_path/'grant.json',time.monotonic()+10)
    node=Node(1,100,100,20,NodeState.AVAILABLE,NodeType.OFFERING,'known',confidence=.9,contour_score=.8,rarity='green')
    runner.buy(SimpleNamespace(bloodpoints=3000000),None,node=node)
    assert len(called)==1 and runner.priorities==['addon','any_perk']
    runner.priorities=['foreign']
    with pytest.raises(ScreenError,match='chain_scope_changed'):runner.buy(SimpleNamespace(bloodpoints=3000000),None,node=node)
    assert len(called)==1
