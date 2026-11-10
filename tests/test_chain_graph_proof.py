from types import SimpleNamespace as NS

from src.core.chain_plan import certified_graph_path
from src.core.models import NodeState,NodeType


def node(i,state=NodeState.LOCKED,rarity='brown',kind=NodeType.ADDON):
    return NS(id=i,state=state,rarity=rarity,type=kind)


def web(nodes,edges):
    return NS(nodes=nodes,edges=edges)


def probe():
    """Live 9.11 Nightmare: 0 centre, 4 brown available, 7 blue target, 8 locked."""
    return [node(0,NodeState.CENTER),node(4,NodeState.AVAILABLE),node(7,rarity='blue'),node(8)],[(0,4),(4,7),(4,8),(7,8)]


def test_the_live_probe_route_is_certified_by_its_total():
    nodes,edges=probe()
    assert certified_graph_path(web(nodes,edges),7,5250)==[4,7]


def test_a_wrong_total_means_no_chain():
    nodes,edges=probe()
    assert certified_graph_path(web(nodes,edges),7,6000)==[]


def test_two_equal_shortest_paths_mean_no_chain():
    nodes=[node(0,NodeState.CENTER),node(1,NodeState.AVAILABLE),node(2,NodeState.AVAILABLE),node(3)]
    assert certified_graph_path(web(nodes,[(0,1),(0,2),(1,3),(2,3)]),3,4000)==[]


def test_perks_and_unknown_rims_are_never_priced():
    nodes,edges=probe();nodes[1]=node(4,NodeState.AVAILABLE,kind=NodeType.PERK)
    assert certified_graph_path(web(nodes,edges),7,5250)==[]
    nodes,edges=probe();nodes[1]=node(4,NodeState.AVAILABLE,rarity='unknown')
    assert certified_graph_path(web(nodes,edges),7,5250)==[]


def test_a_single_node_is_not_a_chain():
    nodes,edges=probe()
    assert certified_graph_path(web(nodes,edges),4,2000)==[]
