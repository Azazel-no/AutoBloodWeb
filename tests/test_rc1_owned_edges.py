import copy
from dataclasses import replace
import pytest
from tests.test_v49_opening import opening_scene
from src.core.persistent_route import PersistentVision
from src.core.fast_opening import OpeningFallback
from src.core.models import NodeState
from src.core.geometry import purchasable


@pytest.mark.parametrize('owned_link',[True,False])
def test_missing_owned_link_never_recreates_an_edge_or_unlocks_a_target(root,library,monkeypatch,owned_link):
    _,frame,result,detector=opening_scene(root,library)
    opening=PersistentVision();assert opening.begin(frame,result,detector,[],'strict',0)
    edge=next(e for e in result.edges if 0 in e);owned={0,*edge}
    opening.expected_purchases.update(owned);opening.observed_red.update(owned-{0})
    positions={(n.x,n.y):n.id for n in result.nodes}
    def fractions(hsv,x,y,r):return (1.,0.) if positions[(x,y)] in owned else (0.,.9)
    monkeypatch.setattr('src.core.bloodweb_detector.ring_fractions',fractions)
    monkeypatch.setattr(opening,'evidence',lambda f,n,d:opening.proofs[n.id])
    missing=edge if owned_link else next(e for e in result.edges if any(i not in owned for i in e))
    fresh_edges=[e for e in result.edges if e!=missing]
    monkeypatch.setattr(detector,'_edges',lambda *a,**k:fresh_edges)
    def forbidden(*a,**k):raise AssertionError('Full perception for an owned-to-owned line')
    monkeypatch.setattr(detector,'analyze_live',forbidden)
    monkeypatch.setattr(detector.matcher,'match',forbidden)
    fresh=replace(frame,captured_at=frame.captured_at+.3)
    if not owned_link:
        with pytest.raises(OpeningFallback,match='connection_changed'):opening.analyze(fresh,detector,[],'strict',0)
    else:
        compact=opening.analyze(fresh,detector,[],'strict',0)
        assert missing not in compact.edges and compact.edges==fresh_edges
        old=copy.deepcopy(result)
        for n in old.nodes:
            if n.id in owned and n.id!=0:n.state=NodeState.PURCHASED
        assert {n.id for n in compact.nodes if purchasable(n,compact.nodes,compact.edges)}=={
            n.id for n in old.nodes if purchasable(n,old.nodes,old.edges)}
        assert opening.quick_checks==1 and opening.full_scans==0
