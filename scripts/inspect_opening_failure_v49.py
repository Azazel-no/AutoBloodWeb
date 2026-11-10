"""Inspect the actual first rejected quick frame; never capture or send input."""
import sys,json,copy,hashlib
from pathlib import Path
import numpy as np,cv2
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.core.models import Analysis,Node,NodeState,NodeType,RecognitionStatus,ScreenState
from src.core.screen_manager import CapturedFrame,WindowInfo,Rect
from src.core.icon_library import IconLibrary
from src.core.bloodweb_detector import BloodwebDetector
from src.core.node_classifier import node_glyph,node_mask,rarity_evidence
from src.core.node_category import classify_category
from src.utils.images import read_image

def stored_frame(item):
    f=item['frame'];w=f['window'];roi=f['roi'];raw=read_image(ROOT/item['source'])
    assert hashlib.sha256((ROOT/item['source']).read_bytes()).hexdigest()==item['sha256']
    return CapturedFrame(raw[roi['y']:roi['y']+roi['height'],roi['x']:roi['x']+roi['width']],
        WindowInfo(w['hwnd'],w['title'],Rect(**w['client']),Rect(**w['monitor']),w['dpi'],w['foreground'],w['pid']),Rect(**roi),f['captured_at'],level=f['level'],level_confidence=f['level_confidence'])

def stored_analysis(identity):
    a=json.loads((ROOT/'debug/analysis_runs'/identity/'analysis.json').read_text(encoding='utf-8'))
    a['screen_state']=ScreenState(a['screen_state']);nodes=[]
    for n in a['nodes']:
        n['state']=NodeState(n['state']);n['type']=NodeType(n['type']);n['recognition_status']=RecognitionStatus(n['recognition_status']);nodes.append(Node(**n))
    a['nodes']=nodes;return Analysis(**a)

def main():
    folder=ROOT/'debug/v49/live_session_260641ab'
    records=json.loads((folder/'frames.json').read_text(encoding='utf-8'));by_id={r['analysis_id']:r for r in records}
    ids=['1791982834830409400_be9f1fe0','1791982835616945200_9d9a2b97']
    frames=[stored_frame(by_id[i]) for i in ids];web=stored_analysis(ids[0])
    output=[]
    for n in web.nodes[1:]:
        r=n.content_radius;patches=[f.image[n.y-r:n.y+r+1,n.x-r:n.x+r+1] for f in frames]
        features=[node_glyph(p,r) for p in patches];soft=[cv2.GaussianBlur(a,(3,3),.7).astype(np.float32)/255 for a in features]
        cats=[classify_category(p,r) for p in patches];rare=[rarity_evidence(p,node_mask(r,p.shape[:2])) for p in patches]
        output.append(dict(id=n.id,icon=n.icon_id,method=n.recognition_method,margin=n.icon_margin,confidence=n.confidence,
            feature_exact=np.array_equal(*features),soft_dice=float(2*np.sum(soft[0]*soft[1])/(np.sum(soft[0]**2)+np.sum(soft[1]**2))),
            feature_difference=float(np.linalg.norm(soft[1]-soft[0])),rarity=rare,
            category=[dict(name=c.category,candidates=c.candidates,confidence=c.confidence) for c in cats]))
    (ROOT/'debug/v49/opening_failure.json').write_text(json.dumps(dict(frames=[by_id[i] for i in ids],nodes=output),ensure_ascii=False,indent=2),encoding='utf-8')
    print(output)
if __name__=='__main__':main()
