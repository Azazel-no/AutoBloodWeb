"""TASKS 2026-11-08b K: one read, several clicks, also after the opening of a level."""
from types import SimpleNamespace

from src.core import turbo_burst
from src.core.automation import Settings
from src.core.models import NodeState


def node(id, state):
    return SimpleNamespace(id=id, state=state)


def engine(mid=True):
    return SimpleNamespace(settings=SimpleNamespace(burst_mid_level=mid))


def result(*states):
    return SimpleNamespace(nodes=[node(i, s) for i, s in enumerate(states)])


def test_mid_level_needs_the_option_and_a_changed_web():
    changed = result(NodeState.PURCHASED, NodeState.AVAILABLE)
    pristine = result(NodeState.AVAILABLE, NodeState.LOCKED)
    assert turbo_burst.mid_level(engine(), changed)
    assert not turbo_burst.mid_level(engine(mid=False), changed)
    assert not turbo_burst.mid_level(engine(), pristine)


def test_absorbed_node_also_counts_as_a_changed_web():
    assert turbo_burst.mid_level(engine(), result(NodeState.ABSORBED, NodeState.AVAILABLE))


def test_option_is_off_by_default_and_the_gap_is_bounded():
    assert Settings.burst_mid_level is False
    assert Settings.burst_gap == .55


def mid_setup(total=9):
    from tests.test_v57_turbo import setup
    from src.core.models import Node, NodeType
    e, d, f, r = setup()
    e.settings.burst_mid_level = True
    for i in range(7, total):
        r.nodes.append(Node(i, 70 + i * 35, 80 + i * 35, 20, NodeState.LOCKED, NodeType.ITEM, 'item' + str(i), confidence=.95,
                            state_confidence=.95, geometry_confidence=.95, contour_score=.9, rarity='brown'))
        r.edges.append((i - 1, i))
    d.matcher.by_id.update({n.icon_id: object() for n in r.nodes if n.icon_id})
    r.nodes[1].state = NodeState.PURCHASED
    r.nodes[2].state = NodeState.AVAILABLE
    return e, d, f, r


def targets(steps):
    return [n.id for _, n in steps]


def test_a_changed_web_gets_several_clicks_from_one_read():
    e, _, f, r = mid_setup(total=13)
    assert targets(turbo_burst.prepare(e, f, r)) == [2, 3, 4, 5, 6]


def test_the_level_cannot_end_during_the_clicks():
    e, _, f, r = mid_setup(total=7)  # Five nodes left: two clicks at most, the Entity may take as many.
    assert targets(turbo_burst.prepare(e, f, r)) == [2, 3]
    r.nodes.pop()
    r.nodes.pop()  # Three left.
    assert turbo_burst.prepare(e, f, r) is None


def test_an_unproven_later_target_ends_the_burst_instead_of_rejecting_it():
    e, _, f, r = mid_setup(total=13)
    r.nodes[4].icon_id = None
    assert targets(turbo_burst.prepare(e, f, r)) == [2, 3]


def test_an_unproven_second_target_still_rejects():
    e, d, f, r = mid_setup(total=13)
    r.nodes[3].icon_id = None
    assert turbo_burst.prepare(e, f, r) is None and not d.presses


def test_uncertain_nodes_reject_mid_level_too():
    e, d, f, r = mid_setup(total=13)
    r.nodes[6].state = NodeState.UNCERTAIN
    assert turbo_burst.prepare(e, f, r) is None and not d.presses


def test_without_the_option_a_changed_web_stays_single():
    e, d, f, r = mid_setup(total=13)
    e.settings.burst_mid_level = False
    assert turbo_burst.prepare(e, f, r) is None and not d.presses
