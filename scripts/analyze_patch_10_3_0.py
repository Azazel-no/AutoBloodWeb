"""Offline: run the production Bloodweb reader on the patch 10.3.0 frames. No input, no game."""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.core.bloodweb_detector import BloodwebDetector,find_center,roi_reach
from src.core.icon_library import IconLibrary
from src.core.models import NodeState
from src.core.path_planner import plan_route

FRAMES={'terrifier_level5.png':('killer','terrifier'),'terrifier_level6.png':('killer','terrifier'),
        'cheryl_level34.png':('survivor','survivor'),'dwight_level43.png':('survivor','survivor')}


def priorities(role,character):
    prefs=json.loads((ROOT/'data/preferences.json').read_text(encoding='utf-8'))
    if role=='killer':return prefs.get('killer_priorities',{}).get(character,['any_perk'])
    return prefs.get('priorities',{}).get('survivor',['any_perk'])


def analyze(folder,library=None,output=None,priorities_for=None):
    priorities_for=priorities_for or priorities
    library=library or IconLibrary(ROOT).build();rows=[]
    for name,(role,character) in FRAMES.items():
        path=Path(folder)/name
        if not path.is_file():continue
        detector=BloodwebDetector(library,role,character);detector.prepare()
        from src.core.bloodweb_header import read_level
        from src.utils.images import read_image
        full=read_image(path)
        # Live capture region (screen_manager): the centre found on the client +- ROI_REACH radii.
        found=find_center(full,library.root)
        if found is None:
            rows.append(dict(frame=name,role=role,character=character,screen='unknown',diagnostics=['center_not_found_on_client']));continue
        cx,cy,cr=found;reach=round(cr*roi_reach(cr,full.shape[0]));h,w=full.shape[:2]
        x,y=max(0,cx-reach),max(0,cy-reach)
        crop=full[y:min(h,cy+reach),x:min(w,cx+reach)].copy()
        result=detector.analyze(crop,str(path))
        result.level,result.level_confidence=read_level(full,library.root)
        if output:detector.save_debug(crop,result,Path(output)/(path.stem+'_analysis.png'))
        center=next((n for n in result.nodes if n.state==NodeState.CENTER),None)
        plan=plan_route(result,priorities_for(role,character))
        target=next((n for n in result.nodes if n.id==plan.next_node),None)
        rows.append(dict(frame=name,role=role,character=character,screen=result.screen_state.value,level=result.level,
            level_confidence=round(result.level_confidence,3),center=(center.x+x,center.y+y,center.radius) if center else None,
            nodes=len(result.nodes)-(1 if center else 0),states=dict(Counter(n.state.value for n in result.nodes)),
            types=dict(Counter(n.type.value for n in result.nodes)),rarities=dict(Counter(n.rarity for n in result.nodes)),
            unknown=[n.id for n in result.nodes if n.state!=NodeState.CENTER and not n.icon_id],
            safe=result.safe_to_automate,diagnostics=result.diagnostics,
            next=dict(node=plan.next_node,reason=plan.reason,icon=getattr(target,'icon_id',None),
                xy=(target.x+x,target.y+y) if target else None),
            node_list=[dict(id=n.id,xy=(n.x+x,n.y+y),r=n.radius,state=n.state.value,type=n.type.value,rarity=n.rarity,
                icon=n.icon_id,conf=round(n.confidence,3),status=n.recognition_status.value) for n in result.nodes]))
    return rows


if __name__=='__main__':
    folder=sys.argv[1] if len(sys.argv)>1 else ROOT/'debug/patch_10_3_0/bloodweb'
    out=ROOT/'debug/patch_10_3_0/analysis';out.mkdir(parents=True,exist_ok=True)
    rows=analyze(folder,output=out)
    (out/'analysis.json').write_text(json.dumps(rows,ensure_ascii=False,indent=1),encoding='utf-8')
    for r in rows:print(json.dumps({k:v for k,v in r.items() if k!='node_list'},ensure_ascii=False))
