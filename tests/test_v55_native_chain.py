"""Real native highlight/total-price pixels; injected devices are separate."""
import copy,json,hashlib
from dataclasses import replace
import pytest
from src.core.models import Analysis,Node,NodeState,NodeType,ScreenState,RecognitionStatus
from src.core.screen_manager import CapturedFrame,WindowInfo,Rect,ScreenError
from src.core.chain_plan import read_preview,PriceRegion
from src.utils.images import read_image


def native(root):
    folder=root/'references/bloodweb/v55_chain';d=json.loads((folder/'manifest.json').read_text())
    def raw(name):
        row=d['files'][name];path=root/row['path']
        assert hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256']
        return read_image(path)
    meta=d['clean_frame'];window=dict(meta['window']);window['client']=Rect(**window['client']);window['monitor']=Rect(**window['monitor'])
    roi=Rect(**meta['roi']);image=raw('clean.png')
    clean=CapturedFrame(image[roi.y:roi.y+roi.height,roi.x:roi.x+roi.width],WindowInfo(**window),roi,meta['captured_at'],
        raw_image=image,level=meta['level'],level_confidence=meta['level_confidence'])
    a=copy.deepcopy(d['clean_analysis']);a['screen_state']=ScreenState(a['screen_state'])
    for n in a['nodes']:
        n['state']=NodeState(n['state']);n['type']=NodeType(n['type']);n['recognition_status']=RecognitionStatus(n['recognition_status'])
    a['nodes']=[Node(**n) for n in a['nodes']];analysis=Analysis(**a)
    previews=[]
    for i in (0,1,2):
        image=raw(f'preview_{i}.png')
        previews.append(replace(clean,image=image[roi.y:roi.y+roi.height,roi.x:roi.x+roi.width],raw_image=image,
            captured_at=d['observations'][i]['frame']['captured_at'],probe_only=True))
    return d,clean,analysis,previews


def total_reader(root):
    from tests.test_v55_native_price import locator
    from scripts.native_price_v55 import NativeTotalPriceLocator
    currency,_=locator(root);d=json.loads((root/'references/bloodweb/v55_chain/manifest.json').read_text());row=d['files']['preview_1.png']
    cal=dict(source=row['path'],sha256=row['sha256'],web_badge_box=[1418,684,60,55],currency_offset=[74,10],client_size=[1920,1080],reviewed_total_label=True)
    return NativeTotalPriceLocator(root,cal,currency)


@pytest.mark.parametrize('index',[1,2])
def test_native_game_shows_two_node_route_and_8000_total_not_4000_item_cost(root,index):
    d,clean,analysis,previews=native(root);reader=total_reader(root);preview=previews[index]
    total=reader.locate(preview.raw_image,(995,737))
    assert total['value']==8000 and total['badge_score']>=.96
    region=PriceRegion(tuple(total['box']),total['provenance'],True,'client')
    parsed=read_preview(preview,analysis,13,d['proposal']['map_id'],reader.currency.reader,region,clean)
    assert parsed.ordered_nodes==[7,13] and parsed.total_bp==8000 and parsed.reason=='observed_preview'
    assert parsed.hover_point is None  # old capture did not log pointer proof


def test_clean_and_not_yet_hovered_native_pixels_cannot_authorize_chain(root):
    d,clean,analysis,previews=native(root)
    assert not read_preview(previews[0],analysis,13,d['proposal']['map_id'],clean_frame=clean).ordered_nodes
    assert total_reader(root).locate(clean.raw_image,(995,737)) is None


@pytest.mark.parametrize('proof',[False,True,'wrong_point'])
def test_dim_locked_endpoint_needs_positive_native_hit_test_proof(tmp_path,monkeypatch,proof):
    from tests.test_v51_hybrid import trial_setup,bought
    from src.core.chain_trial import execute_short_chain
    from src.core.path_planner import RoutePlan
    e,plan,permit,previous,latest=trial_setup(tmp_path);frame,result=latest
    result.nodes[2].geometry_confidence=.67;previous=(previous[0],copy.deepcopy(result))
    if proof:plan.hover_points=[plan.physical_points[-1]]*2
    if proof=='wrong_point':plan.hover_points=[(1,2)]*2
    after=bought(bought(result,1),2);stamp=[frame.captured_at]
    def observe(deadline):
        stamp[0]+=.03
        return replace(frame,captured_at=stamp[0],bloodpoints=26000),copy.deepcopy(after),RoutePlan()
    monkeypatch.setattr(e,'recover_read',observe)
    if proof is True:
        assert len(execute_short_chain(e,plan,permit,previous,latest)['confirmed'])==2
        assert len(e.mouse.presses)==1
    else:
        with pytest.raises(ScreenError,match='unsafe_node'):execute_short_chain(e,plan,permit,previous,latest)
        assert not e.mouse.presses and not permit.ledger.exists()


@pytest.mark.parametrize('stale_count',[1,3])
def test_stale_chain_observations_never_repeat_native_command(tmp_path,monkeypatch,stale_count):
    from tests.test_v51_hybrid import trial_setup,bought
    from src.core.chain_trial import execute_short_chain
    from src.core.path_planner import RoutePlan
    e,plan,permit,previous,latest=trial_setup(tmp_path)
    frame,result=latest;after=bought(bought(result,1),2);calls=[0]
    def observe(deadline):
        calls[0]+=1
        if calls[0]<=stale_count:raise ScreenError('stale_frame')
        return replace(frame,captured_at=frame.captured_at+calls[0]*.03,bloodpoints=25500),copy.deepcopy(after),RoutePlan()
    monkeypatch.setattr(e,'recover_read',observe)
    if stale_count==1:
        outcome=execute_short_chain(e,plan,permit,previous,latest)
        assert outcome['confirmed']==[1,2] and calls[0]==3
        assert not e.pending_action
    else:
        with pytest.raises(ScreenError,match='stale_frame'):execute_short_chain(e,plan,permit,previous,latest)
        assert e.pending_action and e.stop.is_set()
        assert e.scene_record['pending']['chain_outcome']['unconfirmed']==[1,2]
    assert len(e.mouse.presses)==e.attempts==1
    assert json.loads(permit.ledger.read_text())['used']==dict(commands=1,nodes=2,bp=4500)
