"""Real user pixels and injected devices are separate evidence; no game input."""
import copy,json,hashlib,time
from dataclasses import replace
from types import SimpleNamespace
import pytest
from src.core.models import NodeState,NodeType,ScreenState,Node
from src.core.automation import Automation,Settings
from src.core.early_unlock import eligible,purchase_level
from src.core.path_planner import plan_route
from src.core.priority_targets import PERK_TARGET
from src.core.preferences import Preferences
from src.core.level_transition import header_transition
from src.core.scene_memory import record_scene
from src.core.bloodweb_detector import BloodwebDetector
from src.core.bloodweb_header import read_level,automatic_tooltip
from src.core.screen_manager import ScreenError,ScreenManager
from src.utils.images import read_image
from tests.test_automation import Device,web,engine
from tests.test_v46 import Clock

def perk_web():
    result=web();result.nodes[1]=replace(result.nodes[1],type=NodeType.PERK,icon_id=None,category_hint='perk',category_confidence=.97,category_candidates=['perk'])
    result.nodes.append(Node(2,150,280,20,NodeState.AVAILABLE,NodeType.ITEM,'specific',state_confidence=.95,geometry_confidence=.95))
    result.edges.append((0,2));return result

@pytest.mark.parametrize('mode',['strict','smart'])
@pytest.mark.parametrize('priorities,target',[( ['any_perk','specific'],'any_perk'),(['specific','any_perk'],'specific')])
def test_perk_priority_order_and_no_identity_assignment(mode,priorities,target):
    result=perk_web();plan=plan_route(result,priorities,mode)
    assert plan.target_icon==target and result.nodes[1].icon_id is None

def test_ambiguous_perk_and_attempted_path_are_not_forced():
    result=perk_web();result.nodes[1].category_confidence=.8
    assert plan_route(result,['any_perk']).next_node==2
    result.nodes[1].category_confidence=.97
    assert plan_route(result,['any_perk'],excluded={1}).next_node==2
    result.nodes[2].state=NodeState.ABSORBED
    assert plan_route(result,['any_perk'],excluded={1}).next_node is None

def test_perk_preferences_migrate_without_losing_other_order(tmp_path,library):
    source=next(e.id for e in library.filter('survivor'))
    path=tmp_path/'prefs.json';path.write_text(json.dumps({'priorities':{'survivor':[source,'event_offering_orange','any_perk']}}),encoding='utf-8')
    prefs=Preferences(path,library.entries);assert prefs.priorities['survivor']==[source,PERK_TARGET]
    assert prefs.data['retired_priority_targets']['event_offering_orange']['priorities']['survivor'][1]=='event_offering_orange'
    prefs.save('ru','survivor');assert Preferences(path,library.entries).priorities['survivor']==[source,PERK_TARGET]

@pytest.mark.parametrize('folder,character,count',[('v48_deccc99c','unknown',19),('v48_4585d2d0','singularity',5)])
def test_real_moving_background_and_lower_nodes(root,library,folder,character,count):
    detector=BloodwebDetector(library,'killer',character)
    for path in sorted((root/'references/bloodweb'/folder).glob('raw_*.png')):
        result=detector.analyze(read_image(path));assert len(result.nodes)==count
        if character=='unknown':assert any(abs(n.y-920)<22 and abs(n.x-684)<22 for n in result.nodes)
        else:
            perk=next(n for n in result.nodes if n.type==NodeType.PERK)
            assert perk.icon_id is None and perk.category_confidence>=.95

def test_mastermind_perks_are_distinct_from_purple_offering(root,library):
    result=BloodwebDetector(library,'killer','mastermind').analyze(read_image(root/'references/bloodweb/v48_32f8799b/raw_0.png'))
    assert len(result.nodes)==16
    assert any(abs(n.x-374)<20 and abs(n.y-736)<20 for n in result.nodes)
    nearest=lambda x,y:min(result.nodes,key=lambda n:(n.x-x)**2+(n.y-y)**2)
    assert nearest(374,389).type==NodeType.PERK and nearest(684,920).type==NodeType.PERK
    assert nearest(684,213).type==NodeType.OFFERING
    assert nearest(789,500).icon_id in library.event_metadata

def test_unknown19_raws_do_not_offer_pumpkin_target(root,library):
    truth=[(684,562),(579,500),(789,500),(789,624),(620,795),(579,624),(744,328),(864,258),(864,389),(995,389),(925,500),(1047,562)]
    import math
    detector=BloodwebDetector(library,'killer','unknown')
    for path in sorted((root/'references/bloodweb/v48_unknown19').glob('raw_*.png')):
        result=detector.analyze(read_image(path))
        assert len(result.nodes)==12
        assert all(min(math.dist((n.x,n.y),point) for point in truth)<22 for n in result.nodes)
        plan=plan_route(result,[]);target=next(n for n in result.nodes if n.id==plan.next_node)
        assert min(math.dist((target.x,target.y),point) for point in truth)<22

def test_real_unknown40_sleeve_cannot_become_available_node(root,library):
    # These two independent MSS frames contain 21 real nodes. In the first,
    # an unframed sleeve fragment inherited three apparent graph segments.
    truth=[(684,562),(684,440),(789,500),(789,624),(684,685),(579,624),(579,500),
           (746,327),(862,390),(925,501),(862,733),(746,796),(504,730),(622,329),
           (862,258),(995,388),(995,737),(865,868),(504,868),(372,737),(507,257)]
    import math
    detector=BloodwebDetector(library,'killer','unknown')
    for name in ('raw_1.png','raw_2.png'):
        result=detector.analyze(read_image(root/'references/bloodweb/v48_c28c47c1'/name))
        assert len(result.nodes)==len(truth)
        assert all(min(math.dist((n.x,n.y),p) for p in truth)<22 for n in result.nodes)
        assert sum(n.type==NodeType.PERK for n in result.nodes)==3

def test_new_krasue_background_arc_does_not_establish_node(root,library):
    path=next((root/'references/bloodweb').glob('*2026-10-14 083550.png'))
    result=BloodwebDetector(library,'killer','krasue').analyze(read_image(path))
    # A dark patch on one side of this scenic fragment is not a circular rim.
    assert not any(abs(n.x-185)<22 and abs(n.y-892)<22 for n in result.nodes)
    assert any(abs(n.x-145)<22 and abs(n.y-725)<22 for n in result.nodes)

def test_real_header_and_automatic_tooltip_have_provenance(root):
    assert '0' in json.loads((root/'assets/ui/bloodweb_header/manifest.json').read_text(encoding='utf-8'))['digits']
    assert read_level(read_image(root/'references/bloodweb/v48_4585d2d0/raw_0.png'),root)[0]==1
    assert read_level(read_image(root/'references/bloodweb/v48_32f8799b/raw_0.png'),root)[0]==33
    assert automatic_tooltip(read_image(root/'references/bloodweb/v48_f412772c/unvalidated_raw_0.png'),root)
    assert not automatic_tooltip(read_image(root/'references/bloodweb/v48_4585d2d0/raw_0.png'),root)
    for name in ('manifest','automatic'):
        manifest=json.loads((root/f'assets/ui/bloodweb_header/{name}.json').read_text(encoding='utf-8'))
        records=[manifest['label'],*manifest['digits'].values()] if name=='manifest' else [manifest['title'],manifest['description']]
        for record in records:
            assert hashlib.sha256((root/record['path']).read_bytes()).hexdigest()==record['sha256']
            assert hashlib.sha256((root/record['source']).read_bytes()).hexdigest()==record['source_sha256']

@pytest.mark.parametrize('level,confidence,priorities,state,allowed',[(1,.96,[],NodeState.AVAILABLE,True),(9,.96,['any_perk'],NodeState.AVAILABLE,True),(10,.99,[],NodeState.AVAILABLE,True),(10,.99,['target'],NodeState.AVAILABLE,False),(None,0,[],NodeState.AVAILABLE,False),(1,.94,[],NodeState.AVAILABLE,False),(1,.99,['specific'],NodeState.AVAILABLE,True),(1,.99,[],NodeState.ABSORBED,False)])
def test_early_mode_requires_level_priority_and_state(level,confidence,priorities,state,allowed):
    d=Device([web()]);runner=engine(d,dry_run=False);runner.priorities=priorities
    result=web(state);result.level=level;result.level_confidence=confidence
    assert eligible(runner,result)==allowed

def test_overlay_frame_never_allows_input():
    d=Device([web()]);frame=replace(d.capture(),probe_only=True)
    manager=ScreenManager.__new__(ScreenManager)
    with pytest.raises(ScreenError,match='live_frame_required'):manager.validate_click(frame,150,200)

def test_reused_stamp_and_changed_level_cannot_confirm_stability():
    d=Device([web()]);f=d.capture();r=web();r.level=1
    assert not Automation.stable_scene((f,r,None),(f,r,None))
    r2=copy.deepcopy(r);r2.level=2
    assert not Automation.stable_scene((f,r,None),(replace(f,captured_at=f.captured_at+1),r2,None))

def test_header_transition_needs_scope_and_increment():
    d=Device([web()]);f=d.capture();old=web();old.level=18;old.level_confidence=.99
    record=record_scene(f,old,[]);new=web();new.level=19;new.level_confidence=.99
    assert header_transition(record,f,new)
    assert not header_transition(record,replace(f,window=replace(f.window,pid=9)),new)
    new.level=20;assert not header_transition(record,f,new)
    old.level=9;record=record_scene(f,old,[]);new.level=10
    assert header_transition(record,f,new)

@pytest.mark.parametrize('priorities,enabled',[(['target'],True),([],False)])
def test_early_unlock_above_level_nine_never_clicks_with_a_priority_or_when_off(priorities,enabled):
    """BLOODWEB_FIXES B: above level 9 the centre is used only when the level has no priority target."""
    d=Device([web()]);runner=engine(d,dry_run=False,center_auto_above_9=enabled);runner.priorities=priorities
    result=web();result.level=10;result.level_confidence=.99
    with pytest.raises(ScreenError,match='automatic_unlock_unproven'):purchase_level(runner,d.capture(),result)
    assert not d.presses

@pytest.mark.parametrize('dry,approved',[(True,True),(False,False)])
def test_central_purchase_requires_live_approved_run(dry,approved):
    d=Device([web()]);runner=engine(d,dry_run=dry);runner.approved=approved;runner.priorities=[]
    result=web();result.level=1;result.level_confidence=.99;frame=d.capture()
    runner.automatic_proof=(frame.window.signature,1,frame.to_screen(150,200),time.monotonic())
    with pytest.raises(ScreenError,match='automatic_unlock_unproven'):purchase_level(runner,frame,result)
    assert not d.presses

@pytest.mark.parametrize('failure',['expired','window','level','stop'])
def test_early_unlock_rejects_invalid_proof(failure):
    d=Device([web()]);runner=engine(d,dry_run=False);runner.priorities=[];result=web();result.level=1;result.level_confidence=.99;frame=d.capture()
    runner.automatic_proof=(frame.window.signature,1,frame.to_screen(150,200),time.monotonic()-10 if failure=='expired' else time.monotonic())
    if failure=='window':frame=replace(frame,window=replace(frame.window,pid=99))
    if failure=='level':result.level=2
    if failure=='stop':runner.halt()
    with pytest.raises((ScreenError,InterruptedError)):purchase_level(runner,frame,result)
    assert not d.presses

@pytest.mark.parametrize('red_seen',[True,False])
def test_central_input_and_level_transition_do_not_invent_purchases(monkeypatch,red_seen):
    clock=Clock();monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    initial=web();initial.level=1;initial.level_confidence=.99
    initial.nodes[0].geometry_confidence=.95
    owned=copy.deepcopy(initial);owned.nodes[1].state=NodeState.PURCHASED
    following=copy.deepcopy(initial);following.level=2
    d=Device(([owned,owned] if red_seen else [])+[following,following]);runner=engine(d,dry_run=False);runner.wait=clock.advance;runner.priorities=[]
    d.parking_point=lambda frame:None
    frame=d.capture();d.index=0
    runner.automatic_proof=(frame.window.signature,1,frame.to_screen(150,200),clock.now)
    result=purchase_level(runner,frame,initial)
    assert len(d.presses)==runner.attempts==1 and runner.transitions==1 and not runner.pending_action
    assert runner.actions==int(red_seen) and runner.unconfirmed_level_actions==int(not red_seen)
    assert result[1].level==2 and d.releases>=2

def test_entity_during_central_unlock_releases_without_reclick(monkeypatch):
    clock=Clock();monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    initial=web();initial.level=1;initial.level_confidence=.99
    initial.nodes[0].geometry_confidence=.95
    absorbed=copy.deepcopy(initial);absorbed.nodes[1].state=NodeState.ABSORBED
    d=Device([absorbed]);runner=engine(d,dry_run=False);runner.priorities=[];runner.wait=clock.advance;frame=d.capture()
    d.parking_point=lambda frame:None
    runner.automatic_proof=(frame.window.signature,1,frame.to_screen(150,200),clock.now)
    with pytest.raises(ScreenError,match='automatic_unlock_entity'):purchase_level(runner,frame,initial)
    assert len(d.presses)==1 and runner.actions==0 and d.releases>=2

@pytest.mark.parametrize('radius,geometry',[(12,.95),(20,.6)])
def test_central_input_rejects_small_or_unproven_region(radius,geometry):
    d=Device([web()]);runner=engine(d,dry_run=False);runner.priorities=[];result=web();result.level=1;result.level_confidence=.99
    result.nodes[0].radius=radius;result.nodes[0].geometry_confidence=geometry;frame=d.capture()
    runner.automatic_proof=(frame.window.signature,1,frame.to_screen(150,200),time.monotonic())
    with pytest.raises(ScreenError,match='unsafe_node'):purchase_level(runner,frame,result)
    assert not d.presses

def test_known_transient_and_unknown_dialog_recover_without_click(monkeypatch):
    clock=Clock();monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    d=Device([web()]);runner=engine(d,dry_run=False);runner.wait=clock.advance
    frame=d.capture();unknown=replace(web(),screen_state=ScreenState.UNKNOWN,nodes=[])
    observations=iter([(frame,unknown,None),(replace(frame,captured_at=frame.captured_at+1),web(),plan_route(web(),[]))])
    runner.read=lambda:next(observations)
    assert runner.recover_read()[1].screen_state==ScreenState.BLOODWEB and not d.presses and clock.now>=.5

def test_event_catalog_scope_hashes_and_preserved_ids(root,library):
    catalog=json.loads((root/'assets/event_catalog.json').read_text(encoding='utf-8'))
    assert len(catalog['records'])==34
    assert {category:sum(r['category']==category for r in catalog['records']) for category in ('items','addons','offerings')}=={'items':16,'addons':3,'offerings':15}
    assert len(library.entries)==1114 and len(library.event_metadata)==49  # 1094 + 20 Terrifier add-ons.
    snapshot=json.loads((root/'debug/v48/baseline_sources.json').read_text(encoding='utf-8'));current={e.id:e for e in library.entries}
    from dataclasses import asdict
    assert all(asdict(current[entry['id']])==entry for entry in snapshot['entries'])
    for record in catalog['records']:
        for path,key in [('original','sha256'),('normalized','normalized_sha256'),('display','display_sha256')]:
            assert hashlib.sha256((root/record[path]).read_bytes()).hexdigest()==record[key]

def test_p95_interpolation_is_not_max():
    from src.core.performance import percentile
    assert percentile([1,2,3,4,5],.95)==pytest.approx(4.8)
