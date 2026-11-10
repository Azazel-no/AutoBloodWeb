"""Adaptive radial groups; purchase eligibility is independent of enumeration."""
import math
import numpy as np
from src.core.models import NodeState,NodeType


def organize_rings(nodes):
    centers=[n for n in nodes if n.type==NodeType.BLOODWEB_CENTER or n.state==NodeState.CENTER]
    if not centers: return nodes
    center=centers[0]
    others=[n for n in nodes if n not in centers]
    ordered=sorted(others,key=lambda n:math.hypot(n.x-center.x,n.y-center.y))
    gap=max(8,float(np.median([n.radius for n in others]))*.9) if others else 8
    ring=0; previous=None
    for node in ordered:
        radius=math.hypot(node.x-center.x,node.y-center.y)
        if previous is None or radius-previous>gap: ring+=1
        node.ring=ring; node.angle=math.degrees(math.atan2(node.x-center.x,center.y-node.y))%360
        previous=radius
    ordered.sort(key=lambda n:(n.ring,n.angle))
    for ident,node in enumerate(centers+ordered): node.id=ident
    return centers+ordered


def purchasable(node,nodes,edges=()):
    if (node.type in (NodeType.BLOODWEB_CENTER,NodeType.PRESTIGE) or node.state!=NodeState.AVAILABLE
            or node.state_confidence<.8 or node.geometry_confidence<.65):
        return False
    centers=[n for n in nodes if n.type==NodeType.BLOODWEB_CENTER or n.state==NodeState.CENTER]
    if not centers or any(math.hypot(node.x-c.x,node.y-c.y)<=max(c.radius,node.radius)*1.5 for c in centers):
        return False
    # A highlighted outer ring can show a whole branch's purchase preview.
    # A single-node action needs an observed connection to an owned node.
    owned={n.id for n in nodes if n.state in (NodeState.CENTER,NodeState.PURCHASED)}
    return any((a==node.id and b in owned) or (b==node.id and a in owned) for a,b in edges)
