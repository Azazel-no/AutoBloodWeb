"""Read original native hover pixels. No capture or input devices."""
import copy,json,hashlib,sys
from pathlib import Path
from dataclasses import replace
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.core.models import Analysis,Node,NodeState,NodeType,RecognitionStatus,ScreenState
from src.core.screen_manager import CapturedFrame,Rect,WindowInfo
from src.core.chain_plan import PriceRegion,read_preview
from src.core.resources import ResourceReader
from src.utils.images import read_image


def load(folder):
    data=json.loads((folder/'status.json').read_text(encoding='utf-8'))
    a=copy.deepcopy(data['clean_analysis']);a['screen_state']=ScreenState(a['screen_state'])
    for n in a['nodes']:
        n['state']=NodeState(n['state']);n['type']=NodeType(n['type']);n['recognition_status']=RecognitionStatus(n['recognition_status'])
    a['nodes']=[Node(**n) for n in a['nodes']];analysis=Analysis(**a)
    meta=data['clean_frame'];w=meta['window'];roi=Rect(**meta['roi'])
    window=WindowInfo(w['hwnd'],w['title'],Rect(**w['client']),Rect(**w['monitor']),w['dpi'],w['foreground'],w['pid'])
    path=ROOT/data['clean_source'];assert hashlib.sha256(path.read_bytes()).hexdigest()==data['clean_sha256']
    raw=read_image(path)
    clean=CapturedFrame(raw[roi.y:roi.y+roi.height,roi.x:roi.x+roi.width],window,roi,meta['captured_at'],
        raw_image=raw,level=meta['level'],level_confidence=meta['level_confidence'],bloodpoints=meta['bloodpoints'])
    frames=[]
    for record in data['observations']:
        path=ROOT/record['source'];assert hashlib.sha256(path.read_bytes()).hexdigest()==record['sha256']
        raw=read_image(path)
        frames.append(replace(clean,image=raw[roi.y:roi.y+roi.height,roi.x:roi.x+roi.width],raw_image=raw,
            probe_only=True,captured_at=record['frame']['captured_at']))
    return data,clean,analysis,frames


def main():
    reader=ResourceReader(ROOT);evidence=[]
    for name,box in [('hover_02',(947,695,60,23)),('hover_05',(1365,736,63,24))]:
        data,clean,result,frames=load(ROOT/'debug/v52'/name)
        region=PriceRegion(box,data['observations'][1]['sha256'],True,'client')
        observations=[read_preview(f,result,data['target']['id'],data['observations'][0]['path']['map_id'],reader,region,clean) for f in frames]
        evidence.append(dict(session=name,price_region=vars(region),observations=[vars(o) for o in observations],
            original_sources=data['observations'],mouse_inputs=0,keyboard=False,actual_nodes_acquired=0))
    (ROOT/'debug/v52/native_preview.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    print([{k:e[k] for k in ('session','observations')} for e in evidence])


if __name__=='__main__':main()
