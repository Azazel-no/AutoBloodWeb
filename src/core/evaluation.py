"""Offline metrics: ground truth stays out of the live detector."""
import json,math
from pathlib import Path
from src.utils.images import read_image


def evaluate(detector,geometry_path,icons_path):
    geometry=json.loads(Path(geometry_path).read_text(encoding='utf-8'))
    geometry.update(json.loads(Path(geometry_path).with_name('bloodweb_v2_annotations.json').read_text(encoding='utf-8')))
    icons=json.loads(Path(icons_path).read_text(encoding='utf-8'))
    counts=dict(expected_nodes=0,found_nodes=0,correct_states=0,labeled_icons=0,
        known_icons=0,correct_ids=0,accepted_ids=0,false_positives=0,unknown=0)
    cases=[]
    for name,truth in geometry.items():
        result=detector.analyze(read_image(detector.library.root/'references/bloodweb'/name),name)
        for x,y,state in truth['nodes']:
            counts['expected_nodes']+=1
            node=min(result.nodes,key=lambda n:math.hypot(n.x-x,n.y-y),default=None)
            if node and math.hypot(node.x-x,node.y-y)<20:
                counts['found_nodes']+=1; counts['correct_states']+=int(node.state==state)
        for x,y,ident in icons.get(name,{}).get('icons',[]):
            counts['labeled_icons']+=1; counts['known_icons']+=int(ident is not None)
            node=min(result.nodes,key=lambda n:math.hypot(n.x-x,n.y-y),default=None)
            prediction=node.icon_id if node and math.hypot(node.x-x,node.y-y)<20 else None
            counts['accepted_ids']+=int(prediction is not None)
            counts['correct_ids']+=int(prediction is not None and prediction==ident)
            counts['false_positives']+=int(prediction is not None and prediction!=ident)
            counts['unknown']+=int(prediction is None)
            cases.append(dict(source=name,x=x,y=y,expected=ident,predicted=prediction,
                score=node.confidence if node else 0,reason=node.match_reason if node else 'not_found'))
    return dict(counts=counts,metrics=dict(node_recall=counts['found_nodes']/max(1,counts['expected_nodes']),
        state_accuracy=counts['correct_states']/max(1,counts['found_nodes']),
        icon_precision=counts['correct_ids']/max(1,counts['accepted_ids']),
        known_icon_recall=counts['correct_ids']/max(1,counts['known_icons']),
        unknown_fraction=counts['unknown']/max(1,counts['labeled_icons'])),cases=cases)
