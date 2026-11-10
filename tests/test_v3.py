import json,ctypes,threading,time
from pathlib import Path
from types import SimpleNamespace
import pytest,numpy as np
from src.core.models import Node,NodeState,NodeType,ScreenState,Analysis
from src.core.geometry import organize_rings,purchasable
from src.core.path_planner import plan_route
from src.core.input_control import Mouse
from src.core.screen_manager import ScreenError
from src.core.evaluation import evaluate
from src.core.hotkey_config import parse_hotkey,validate_bindings
from src.core.review_store import ReviewStore
from tests.test_automation import Device,engine,web
from tests.test_input_control import InputStub


def node(i,x,y,ident=None,state=NodeState.AVAILABLE,kind=NodeType.ITEM):
    return Node(i,x,y,20,state,kind,ident,state_confidence=.95,geometry_confidence=.9)


def test_manual_icon_precision_and_geometry_preserved(detector,root):
    report=evaluate(detector,root/'tests/fixtures/bloodweb_annotations.json',root/'tests/fixtures/icon_validation_v3.json')
    assert report['counts']['found_nodes']==report['counts']['correct_states']==94
    assert report['counts']['false_positives']==0
    assert report['counts']['correct_ids']>=26
    # Four formerly unlabelled slots now have visually confirmed Wiki IDs.
    assert report['counts']['known_icons']==32 and report['counts']['labeled_icons']==39


def test_ring_order_adapts_and_starts_at_twelve():
    nodes=[node(0,200,200,state=NodeState.CENTER,kind=NodeType.BLOODWEB_CENTER),
        node(1,200,100),node(2,300,200),node(3,200,300),node(4,100,200),node(5,200,400)]
    result=organize_rings(nodes)
    assert [n.angle for n in result[1:5]]==[0,90,180,270]
    assert [n.ring for n in result]==[0,1,1,1,1,2]
    assert len({n.ring for n in organize_rings(nodes[:2])})==2


@pytest.mark.parametrize('kind,state,offset',[(NodeType.BLOODWEB_CENTER,NodeState.AVAILABLE,100),
    (NodeType.ITEM,NodeState.AVAILABLE,0),(NodeType.PRESTIGE,NodeState.AVAILABLE,100)])
def test_center_and_coincident_false_node_cannot_be_clicked(kind,state,offset):
    result=web(); bad=node(5,150+offset,200,'target',state,kind)
    result.nodes.append(bad); result.edges.append((0,5))
    assert not purchasable(bad,result.nodes)
    assert plan_route(result,['target']).next_node!=5
    device=Device([result]); e=engine(device,dry_run=False)
    with pytest.raises(ScreenError,match='unsafe_node'):
        e.buy(device.capture(),result,bad)
    assert not device.presses


def test_smart_branch_value_differs_from_strict_and_honors_extreme_priority():
    result=Analysis(700,700,ScreenState.BLOODWEB,[
        node(0,300,300,state=NodeState.CENTER,kind=NodeType.BLOODWEB_CENTER),
        node(1,400,300,'first'),node(2,200,300,'second'),
        node(3,100,300,'second',NodeState.LOCKED),node(4,100,200,'second',NodeState.LOCKED)],
        [(0,1),(0,2),(2,3),(3,4)])
    assert plan_route(result,['first','second'],'strict').next_node==1
    smart=plan_route(result,['first','second'],'smart')
    assert smart.next_node==2 and smart.benefit==12 and smart.cost_units==3
    assert 'entity_future_unknown' in smart.assumptions
    assert plan_route(result,['first','absent','second'],'smart').next_node==1
    result.nodes[2].state=NodeState.ABSORBED
    assert plan_route(result,['first','second'],'smart').next_node==1


@pytest.mark.parametrize('text,expected',[('F8',(0,0x77)),('Ctrl+Alt+F9',(3,0x78)),
    ('Shift+5',(4,0x35)),('Alt+F24',(1,0x87))])
def test_hotkey_parser(text,expected): assert parse_hotkey(text)==expected


@pytest.mark.parametrize('bindings',[{'toggle':'F8','stop':'F8'},
    {'toggle':'Ctrl+Alt+F9','stop':'F9'},{'toggle':'F25','stop':'F9'}])
def test_hotkey_conflicts_are_rejected(bindings):
    with pytest.raises(ValueError): validate_bindings(bindings)


@pytest.mark.parametrize('stage',['move','down','cursor_read','clamped'])
def test_native_failure_stage_is_reported_without_blind_input(stage):
    mouse=Mouse(); stub=InputStub(); mouse.u=stub; report=[]
    mouse.audit=lambda name,**values:report.append((name,values))
    if stage=='move':
        stub.SetPhysicalCursorPos=lambda *args:0; reason='cursor_move_failed'
    elif stage=='down':
        stub.SendInput=lambda *args:0; reason='sendinput_failed'
    elif stage=='cursor_read':
        stub.GetPhysicalCursorPos=lambda *args:0; reason='cursor_read_failed'
    else:
        def clipped(pointer): pointer._obj.x=0; pointer._obj.y=0; return 1
        stub.GetPhysicalCursorPos=clipped; reason='cursor_position_mismatch'
    with pytest.raises(ScreenError,match=reason):
        mouse.purchase((250,200),.05,threading.Event(),lambda:None)
    assert report and not stub.flags and not mouse.held


def test_calibration_selection_survives_node_reordering_and_buys_exact_target():
    result=web(); alternative=node(2,150,300,'other'); result.nodes.append(alternative); result.edges.append((0,2))
    import copy
    purchased=copy.deepcopy(result); purchased.nodes[2].state=NodeState.PURCHASED
    device=Device([result,result,purchased,purchased]); e=engine(device,dry_run=False)
    frame=device.capture(); device.index=0
    e.calibration_target=(150,300,frame.window.signature,frame.roi)
    e.run(calibrate=True)
    assert e.receipt and device.presses[0][0]==(-250,300)


def test_prestige_requires_separate_approval_even_when_old_setting_is_enabled():
    prestige=Analysis(400,400,ScreenState.PRESTIGE,[node(0,200,200,kind=NodeType.PRESTIGE,state=NodeState.CENTER)],confidence=.9)
    device=Device([prestige]); e=engine(device,dry_run=False,auto_prestige=True); events=[]
    e.event=lambda state,message,packet:events.append(message); e.run()
    assert e.state.value=='paused' and 'prestige_manual' in events and not device.presses
    e.prestige_approved=True
    with pytest.raises(ScreenError): e.buy(device.capture(),web(),point=(150,200))
    assert not device.presses


def test_focus_wait_explains_timeout_and_sends_no_input():
    device=Device([web()]); device.backend=SimpleNamespace(info=lambda:SimpleNamespace(foreground=False))
    e=engine(device,dry_run=False,focus_timeout=1); e.run(calibrate=True)
    assert not device.presses
    assert any(v.get('reason')=='focus_wait_timeout' for v in e.report.entries)


def test_review_labels_are_reversible_and_do_not_change_prediction(tmp_path):
    store=ReviewStore(tmp_path/'review.json'); image=np.zeros((400,400,3),np.uint8); target=web().nodes[1]
    store.save(image,target,'corrected','addon','example'); store.save(image,target,None,'unknown','example')
    store.undo(image,target)
    assert store.data[store.key(image,target)]['current']['expected']=='corrected'
    store.undo(image,target)
    assert store.data[store.key(image,target)]['current'] is None
    assert target.icon_id=='target'


def test_per_node_debug_contains_original_normalized_and_acceptance_reason(root,detector,tmp_path):
    from src.utils.images import read_image
    image=read_image(root/'references/bloodweb/bloodweb_level43_start.png'); result=detector.analyze(image)
    detector.save_debug(image,result,tmp_path/'result.png')
    for n in result.nodes:
        assert Path(n.crop_path).is_file() and Path(n.normalized_path).is_file()
        assert n.match_reason and len(n.candidates)<=3
    false_hand=min(result.nodes,key=lambda n:np.hypot(n.x-357,n.y-593))
    assert false_hand.icon_id is None and false_hand.match_reason=='contour_mismatch'


def test_mouse_release_survives_a_failed_audit_callback():
    mouse=Mouse(); stub=InputStub(); mouse.u=stub
    def broken(*args,**kwargs): raise OSError('disk_unavailable')
    mouse.audit=broken
    mouse.purchase((250,200),.05,threading.Event(),lambda:None)
    assert stub.flags==[2,4] and not mouse.held
