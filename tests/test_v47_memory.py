import copy,time
from dataclasses import replace
import numpy as np
from src.core.bloodweb_detector import BloodwebDetector
from src.core.models import RecognitionStatus,Match,Node,NodeState,NodeType
from src.core.bloodweb_memory import BloodwebMemory
from src.core.screen_manager import CapturedFrame,WindowInfo,Rect
from src.core.node_classifier import IconMatcher
from src.utils.images import read_image

def test_level_map_reuses_only_confirmed_identity_and_keeps_fresh_nodes(root,library):
    image=read_image(root/'tests/fixtures/v43/raw_1791793636036233900.png');h,w=image.shape[:2]
    window=WindowInfo(1,'Replay',Rect(0,0,w,h),Rect(0,0,w,h),96,True)
    frame=CapturedFrame(image,window,Rect(0,0,w,h),time.monotonic())
    d=BloodwebDetector(library);first=d.analyze_live(frame);generation=d.memory.generation
    again=d.analyze_live(frame)
    assert d.memory.generation==generation and d.memory.hits==0
    second=d.analyze_live(replace(frame,captured_at=frame.captured_at+1))
    third=d.analyze_live(replace(frame,captured_at=frame.captured_at+2))
    assert d.memory.hits>0
    assert [(n.x,n.y,n.state,n.icon_id) for n in first.nodes]==[(n.x,n.y,n.state,n.icon_id) for n in third.nodes]
    assert len(d.memory.records)==len(third.nodes)-1 and d.memory.graph==third.edges
    d.analyze_live(replace(frame,roi=Rect(1,0,w,h),captured_at=frame.captured_at+3))
    assert d.memory.generation>generation and d.memory.hits==0
    d.analyze_live(replace(frame,image=np.zeros_like(image),captured_at=frame.captured_at+4))
    assert not d.memory.records

def test_ambiguous_and_changed_pixels_cannot_reuse_recognized_id(root):
    raw=read_image(root/'references/bloodweb/bloodweb_level43_start.png')
    patch=raw[370:449,423:502];memory=BloodwebMemory('survivor',None)
    memory.begin(('window','roi'),(462,531),[(462,409,39)])
    feature,key,_,_=memory.identity(462,409,patch,39)
    node=Node(8,462,409,39,NodeState.AVAILABLE,NodeType.ITEM,'verified')
    known=Match(icon_id='verified',recognition_status=RecognitionStatus.RECOGNIZED)
    memory.update([node],{(462,409):(key,known,None)},[])
    previous=memory.records[0];memory.update([node],{(462,409):(key,known,previous)},[])
    assert memory.identity(462,409,patch,39)[2].icon_id=='verified'
    changed=raw[430:509,528:607]
    assert memory.identity(462,409,changed,39)[2] is None
    ambiguous=Match(reason='ambiguous_candidates',recognition_status=RecognitionStatus.AMBIGUOUS)
    node.icon_id=None;node.recognition_status=RecognitionStatus.AMBIGUOUS
    memory.update([node],{(462,409):(key,ambiguous,previous)},[])
    assert memory.identity(462,409,patch,39)[2] is None
    node.state=NodeState.ABSORBED;memory.update([node],{(462,409):(key,ambiguous,memory.records[0])},[(0,8)])
    assert memory.records[0]['node']['state']==NodeState.ABSORBED and memory.graph==[(0,8)]

def test_gradient_descriptor_is_independent_of_soft_overlap():
    horizontal=np.zeros((64,64),np.uint8);horizontal[28:36,8:56]=255
    vertical=horizontal.T.copy()
    a=IconMatcher.gradient_descriptor(horizontal);b=IconMatcher.gradient_descriptor(vertical)
    assert len(a)==512 and np.dot(a,b)<.5

def test_top10_scope_and_ambiguous_status(root,library):
    path=root/'references/bloodweb/Снимок экрана 2026-10-13 090856.png'
    result=BloodwebDetector(library).analyze(read_image(path),str(path))
    bottle=min(result.nodes,key=lambda n:(n.x-547)**2+(n.y-416)**2)
    assert bottle.recognition_status==RecognitionStatus.AMBIGUOUS and bottle.icon_id is None
    assert len(bottle.top10)==10 and len(bottle.candidates)==3 and bottle.reranked
    entries={e.id:e for e in library.entries}
    assert all(entries[ident].role=='survivor' for ident,score in bottle.top10)
    killer=BloodwebDetector(library,'killer',character='slasher').analyze(read_image(root/'references/bloodweb/Снимок экрана 2026-10-13 090417.png'))
    assert all(entries[ident].character in (None,'slasher') for n in killer.nodes for ident,score in n.top10)

def test_changed_category_invalidates_exact_inner_glyph_memory(root):
    from types import SimpleNamespace
    raw=read_image(root/'references/bloodweb/bloodweb_level43_start.png');patch=raw[370:449,423:502]
    memory=BloodwebMemory('survivor',None);memory.begin(('window','roi'),(462,531),[(462,409,39)])
    a=SimpleNamespace(category='unknown',candidates=['item','addon'],confidence=.5,event_offering=False)
    b=SimpleNamespace(category='offering',candidates=['offering'],confidence=.99,event_offering=False)
    _,key,_,_=memory.identity(462,409,patch,39,a)
    node=Node(8,462,409,39,NodeState.AVAILABLE,NodeType.ITEM,'verified');known=Match(icon_id='verified',recognition_status=RecognitionStatus.RECOGNIZED)
    memory.update([node],{(462,409):(key,known,None)},[])
    memory.update([node],{(462,409):(key,known,memory.records[0])},[])
    assert memory.identity(462,409,patch,39,a)[2] is not None
    assert memory.identity(462,409,patch,39,b)[2] is None
