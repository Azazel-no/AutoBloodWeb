"""One priority behaviour: the top-ranked present item comes first."""
from src.core.models import Analysis,Node,NodeState,NodeType,ScreenState
from src.core.path_planner import plan_route
from src.core.priority_targets import PERK_TARGET


def node(i,x,y,ident=None,state=NodeState.AVAILABLE,kind=NodeType.ITEM):
    return Node(i,x,y,20,state,kind,ident,state_confidence=.95,geometry_confidence=.9)


def perk(i,x,y,state=NodeState.AVAILABLE):
    n=node(i,x,y,state=state,kind=NodeType.PERK);n.category_hint='perk';n.category_confidence=.99
    return n


def scene():
    """A perk three steps away against a branch holding two rank-2 items."""
    return Analysis(700,700,ScreenState.BLOODWEB,[
        node(0,300,300,state=NodeState.CENTER,kind=NodeType.BLOODWEB_CENTER),
        node(1,400,300),node(5,450,300,state=NodeState.LOCKED),perk(2,500,300,NodeState.LOCKED),
        node(3,200,300,'second'),node(4,100,300,'second',NodeState.LOCKED)],
        [(0,1),(1,5),(5,2),(0,3),(3,4)])


def test_perks_first_beats_a_branch_of_two_rank_two_items():
    plan=plan_route(scene(),[PERK_TARGET,'second'],'top_first')
    assert plan.route==[1,5,2] and plan.target_icon==PERK_TARGET and plan.reason=='priority'
    assert plan_route(scene(),[PERK_TARGET,'second'],'smart').next_node==3


def test_absent_top_priority_moves_on_to_the_next_present_one():
    plan=plan_route(scene(),['absent','second'],'top_first')
    assert plan.next_node==3 and plan.target_icon=='second'


def test_cut_off_top_priority_uses_branch_search_for_the_rest():
    result=scene();result.nodes[1].state=NodeState.ABSORBED
    plan=plan_route(result,[PERK_TARGET,'second'],'top_first')
    assert plan.reason=='smart_priority' and plan.next_node==3


def test_no_priorities_still_fills():
    assert plan_route(scene(),[],'top_first').reason=='filler'