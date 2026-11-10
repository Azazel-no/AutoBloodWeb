"""Original native price crops; no constructed game pictures or relaxed OCR."""
import hashlib,json
import pytest
from scripts.native_price_v55 import NativePriceLocator
from src.core.resources import ResourceReader
from src.utils.images import read_image


def locator(root):
    data=json.loads((root/'tests/fixtures/v55/native_prices/manifest.json').read_text())
    row=data['currency']
    calibration=dict(source=row['path'],sha256=row['sha256'],currency_box=[0,0,28,29],client_size=[1920,1080])
    return NativePriceLocator(root,calibration,ResourceReader(root)),data


@pytest.mark.parametrize('name,price',[('faint',2000),('serum_first',2000),('serum_second',2000),('clean',None)])
def test_real_moving_native_price_and_clean_negative(root,name,price):
    reader,data=locator(root);row=data[name];path=root/row['path']
    assert hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256']
    result=reader.locate_region(read_image(path),row['box'][:2])
    assert (result['value'] if result else None)==price
    if result:assert result['score']>=.96


def test_native_price_source_tamper_and_wrong_client_are_rejected(root):
    reader,data=locator(root);row=data['currency']
    with pytest.raises(ValueError,match='native_price_source_changed'):
        NativePriceLocator(root,dict(source=row['path'],sha256='0'*64,currency_box=[0,0,28,29],client_size=[1920,1080]),ResourceReader(root))
    assert reader.locate(read_image(root/data['faint']['path']),(100,100)) is None


@pytest.mark.parametrize('index',[0,1])
def test_unknown_native_left_card_total_price(root,index):
    from scripts.native_price_v55 import NativeTotalPriceLocator
    currency,_=locator(root)
    total=NativeTotalPriceLocator(root,json.loads((root/'data/native_total_price_locator.json').read_text()),currency)
    request=json.loads((root/'debug/v56/unknown_20261017/price_review_002/request.json').read_text())
    target=next(t for t in request['targets'] if t['node']==4);source=target['frames'][index]
    path=root/source['source'];assert hashlib.sha256(path.read_bytes()).hexdigest()==source['sha256']
    result=total.locate(read_image(path),target['point'])
    assert result['value']==5250 and result['web_badge'][0]<target['point'][0]
    assert result['score']>=.96 and result['badge_score']>=.96


def test_reobserve_original_native_box_excludes_scenery_but_requires_currency(root):
    reader,_=locator(root)
    folder=root/'debug/v56/unknown_20261017/price_review_004'
    review=json.loads((folder/'review.json').read_text())
    row=next(r for r in review['prices'] if r['node']==3)
    pixels=read_image(folder/'current_3_3.png')
    assert reader.locate(pixels,(579,624))['value']==2000
    assert reader.reobserve_box(pixels,row['box'])==2000
    x,y,w,h=row['box']
    assert reader.reobserve_box(pixels,[x+50,y,w,h]) is None


def test_total_price_does_not_match_left_navigation_web_icon(root):
    from scripts.native_price_v55 import NativeTotalPriceLocator
    currency,_=locator(root)
    total=NativeTotalPriceLocator(root,json.loads((root/'data/native_total_price_locator.json').read_text()),currency)
    pixels=read_image(root/'debug/v56/unknown_20261017/price_review_006/node_4_3.png')
    found=total.locate(pixels,(623,328))
    assert found['value']==6000 and found['web_badge'][0]>623


@pytest.mark.parametrize('change',['accepted_id','available','recrop','ring_glow','contradiction','absorbed','new_point'])
def test_center_price_scope_tracks_all_physical_unowned_points(change):
    from tests.test_automation import Device,web
    from scripts.live_test_v54 import scene_key
    from scripts.native_price_v55 import center_scene_valid
    from src.core.models import NodeState
    scene=web();scene.level=3;scene.level_confidence=.99
    scene.nodes[0].geometry_confidence=.95
    scene.nodes[1].icon_id=None;scene.nodes[1].state=NodeState.LOCKED
    frame=Device([scene]).capture();key=scene_key(frame,scene)
    if change=='accepted_id':scene.nodes[1].icon_id='target'
    if change=='available':scene.nodes[1].state=NodeState.AVAILABLE
    if change=='ring_glow':scene.nodes[1].x+=6;scene.nodes[1].y+=4
    if change=='recrop':
        from dataclasses import replace
        from src.core.screen_manager import Rect
        # Same client pixels/physical nodes, a tighter fresh crop after hover.
        frame=replace(frame,roi=Rect(10,10,400,400))
        for node in scene.nodes:node.x-=10;node.y-=10
    if change=='contradiction':key['nodes'][1]['icon']='old';scene.nodes[1].icon_id='different'
    if change=='absorbed':scene.nodes[1].state=NodeState.ABSORBED
    if change=='new_point':scene.nodes[1].x+=20
    assert center_scene_valid(key,frame,scene)==(change in ('accepted_id','available','recrop','ring_glow'))


@pytest.mark.parametrize('name,value',[('event_card',4000),('event_card_threshold',4000),('faint_card',2000)])
def test_real_event_price_excludes_bright_scenery_outside_native_card(root,name,value):
    reader,data=locator(root);row=data[name];path=root/row['path']
    assert hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256']
    result=reader.locate_region(read_image(path),row['box'][:2],[row['tooltip']])
    assert result['value']==value and result['score']>=.96
    assert result['box'][2]<=105


def test_conflicting_readable_price_regions_never_supply_a_budget_bound(root):
    reader,data=locator(root);row=data['event_card'];pixels=read_image(root/row['path'])
    class Conflict:
        def decode(self,image):return 4000 if image.shape[1]==105 else 40000
    reader.reader=Conflict()
    assert reader.locate_region(pixels,row['box'][:2],[row['tooltip']]) is None


@pytest.mark.parametrize('values,stamps,success',[
    ([None,2000,2000],[1,2,3],True),
    ([2000,None,2000,2000],[1,2,3,4],True),
    ([None,None,None,None],[1,2,3,4],False),
    ([2000,2500,2000],[1,2,3],False),
    ([2000,2000],[1,1],False)])
def test_current_native_price_needs_two_independent_agreements_with_bounded_unknown(tmp_path,values,stamps,success):
    from types import SimpleNamespace
    from scripts.live_test_v54 import reobserve_price
    from tests.test_automation import Device,web
    from dataclasses import replace
    from src.core.screen_manager import ScreenError
    d=Device([web()]);frame=d.capture();observations=iter(zip(values,stamps));seen=[]
    def capture(_):
        value,stamp=next(observations);seen.append(value)
        return replace(frame,captured_at=stamp)
    e=SimpleNamespace(wait=lambda _:None,screen=SimpleNamespace(capture_overlay=capture))
    if success:
        result,proofs=reobserve_price(e,frame,lambda _:seen[-1],2000,tmp_path,1)
        assert result==[2000,2000] and proofs[1]>proofs[0]
    else:
        with pytest.raises(ScreenError,match='chain_price_unproven'):
            reobserve_price(e,frame,lambda _:seen[-1],2000,tmp_path,1)
    assert len(seen)<=4


@pytest.mark.parametrize('change',[None,'unrelated_entity','recrop','target_owned','target_identity','edge','priority','window','duplicate'])
def test_single_price_is_bound_to_fresh_direct_target_and_selected_priority(change):
    from dataclasses import replace
    from types import SimpleNamespace
    from tests.test_automation import Device,web
    from src.core.models import NodeState,Node,NodeType
    from scripts.live_test_v54 import scene_key
    from scripts.native_price_v55 import single_scene_valid
    scene=web();d=Device([scene]);frame=d.capture();point=frame.to_screen(scene.nodes[1].x,scene.nodes[1].y)
    key=scene_key(frame,scene);plan=SimpleNamespace(next_node=scene.nodes[1].id)
    if change=='unrelated_entity':scene.nodes.append(Node(99,50,50,20,NodeState.ABSORBED,NodeType.UNKNOWN))
    elif change=='recrop':
        from src.core.screen_manager import Rect
        frame=replace(frame,image=frame.image[:,10:],roi=Rect(10,0,390,400))
        for n in scene.nodes:n.x-=10
    elif change=='target_owned':scene.nodes[1].state=NodeState.PURCHASED
    elif change=='target_identity':scene.nodes[1].icon_id='contradiction'
    elif change=='edge':scene.edges=[]
    elif change=='priority':plan.next_node=999
    elif change=='window':frame=replace(frame,window=replace(frame.window,hwnd=999))
    elif change=='duplicate':scene.nodes.append(replace(scene.nodes[1],id=99))
    assert single_scene_valid(key,frame,scene,plan,point)==(change in (None,'unrelated_entity','recrop'))
