from src.core.models import Analysis, Node, NodeState, NodeType, ScreenState
from src.core.path_planner import plan_route


def web():
    return Analysis(900,800,ScreenState.BLOODWEB,[
        Node(0,450,400,40,NodeState.CENTER,NodeType.CENTER,state_confidence=.9),
        Node(1,550,400,40,NodeState.AVAILABLE,NodeType.ADDON,'filler',state_confidence=.9,geometry_confidence=.9),
        Node(2,650,400,40,NodeState.LOCKED,NodeType.ITEM,'target',state_confidence=.9,geometry_confidence=.9),
        Node(3,450,500,40,NodeState.AVAILABLE,NodeType.ITEM,'other',state_confidence=.9,geometry_confidence=.9),
    ],[(0,1),(1,2),(0,3)])


def test_priority_route_and_replan_after_purchase():
    analysis=web()
    plan=plan_route(analysis,{'target'})
    assert plan.route==[1,2] and plan.next_node==1 and plan.reason=='priority'
    analysis.nodes[1].state=NodeState.PURCHASED
    analysis.nodes[2].state=NodeState.AVAILABLE
    plan=plan_route(analysis,{'target'})
    assert plan.route==[2] and plan.next_node==2


def test_entity_blocks_target_route():
    analysis=web(); analysis.nodes[1].state=NodeState.ABSORBED
    plan=plan_route(analysis,{'target'})
    assert plan.next_node==3 and plan.reason=='filler'


def test_no_click_on_uncertainty_or_special_states():
    analysis=web()
    for node in analysis.nodes[1:]: node.state=NodeState.UNCERTAIN
    assert plan_route(analysis,{'target'}).next_node is None
    for state in (ScreenState.UNKNOWN,ScreenState.PRESTIGE,ScreenState.REWARD,ScreenState.REFRESH):
        analysis.screen_state=state
        assert plan_route(analysis,{'target'}).next_node is None
