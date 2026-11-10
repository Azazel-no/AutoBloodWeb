import time
import pytest
from src.core.series_budget import SeriesBudget
from src.core.screen_manager import ScreenError
from src.core.models import NodeState
from tests.test_automation import Device,web,engine
from tests.test_v46 import Clock




def test_hover_fade_retries_local_anchor_then_restores_full_search_at_4000ms(monkeypatch):
    import numpy as np
    from src.core.screen_manager import ScreenManager,Rect,WindowInfo
    from tests.test_screen_manager import Backend
    clock=Clock();window=WindowInfo(1,'DBD',Rect(0,0,900,800),Rect(0,0,900,800),96,True)
    # Arbitrary numerical array for the mocked anchor-search contract.
    screen=ScreenManager(Backend(window,np.zeros((800,900,3),np.uint8)),clock=lambda:clock.now)
    screen._center_hint=(window.signature,(320,400,32));screen.fast_center_retry=True;calls=[]
    def find(*args,**kwargs):calls.append(kwargs.get('local_only',False));return None
    monkeypatch.setattr('src.core.bloodweb_detector.find_center',find)
    with pytest.raises(ScreenError,match='frame_unstable'):screen.capture()
    clock.advance(1.19)
    with pytest.raises(ScreenError,match='frame_unstable'):screen.capture()
    assert calls==[True,True]
    clock.advance(.02)
    with pytest.raises(ScreenError,match='frame_unstable'):screen.capture()
    assert calls==[True,True,True]
    clock.advance(2.8)
    with pytest.raises(ScreenError,match='bloodweb_not_found'):screen.capture()
    assert calls==[True,True,True,True,False]


@pytest.mark.parametrize('cause',[None,'focus','window','covered','stale','stop'])
def test_tooltip_recovery_parks_only_on_fresh_owned_game_without_button_input(cause):
    import threading
    from dataclasses import asdict,replace
    from src.core.screen_manager import ScreenManager,Rect,WindowInfo
    from src.core.input_control import Mouse
    from tests.test_input_control import InputStub
    from tests.test_screen_manager import Backend
    window=WindowInfo(1,'DBD',Rect(0,0,900,800),Rect(0,0,900,800),96,True)
    backend=Backend(window);screen=ScreenManager(backend,clock=lambda:10)
    screen.last_capture=dict(window=asdict(window),captured_at=10)
    mouse=Mouse();stub=InputStub();mouse.u=stub;stop=threading.Event()
    if cause=='focus':backend.window=replace(window,foreground=False)
    elif cause=='window':backend.window=replace(window,hwnd=2)
    elif cause=='covered':backend.owner=False
    elif cause=='stale':screen.last_capture['captured_at']=7
    elif cause=='stop':stop.set()
    if cause is None:
        assert screen.park_hover(mouse,stop,lambda:None)
        assert stub.positions[-1]==(8,400)
    elif cause=='stale':assert not screen.park_hover(mouse,stop,lambda:None)
    else:
        with pytest.raises((ScreenError,InterruptedError)):screen.park_hover(mouse,stop,lambda:None)
    assert not stub.flags and not mouse.held


def test_saved_scene_seeds_fresh_vision_once_without_resolving_pending_or_allowing_input(root,library,monkeypatch):
    from dataclasses import replace
    from tests.test_v49_opening import opening_scene
    from src.core.automation import Automation,Settings
    from src.core.scene_memory import record_scene
    _,frame,scene,detector=opening_scene(root,library)
    d=Device([]);d.capture=lambda:replace(frame,captured_at=time.monotonic())
    r=Automation(d,detector,d,[],Settings(dry_run=False,startup_delay=0,fast_early_levels=False),approved=True,hotkeys_ready=True)
    node=next(n for n in scene.nodes if n.state==NodeState.AVAILABLE)
    point=frame.to_screen(node.x,node.y)
    r.persisted_scene=record_scene(frame,scene,[point],dict(point=point,radius=node.radius,icon=node.icon_id))
    r.scene_record=r.persisted_scene;r.attempted=[point]
    fresh,result,plan=r.read()
    assert r.opening.active and r.opening.full_scans==1 and r.persisted_scene and r.attempted==[point]
    def forbidden(*args,**kwargs):raise AssertionError('redundant full analysis during scene validation')
    monkeypatch.setattr(detector,'analyze_live',forbidden)
    fresh,result,plan=r.read()
    assert r.opening.full_scans==1 and r.opening.quick_checks==1
    with pytest.raises(ScreenError,match='purchase_result_unknown'):r._buy_once(fresh,result,node=result.nodes[1])
    assert r.persisted_scene and r.attempted==[point] and not d.presses


def auth():return dict(role='killer',character='dark_lord',max_bp=2000000,allow_prestige=False,allow_chain=False)


def test_unknown_character_budget_keeps_scope_binding_and_other_saved_scenes(tmp_path):
    import json
    from scripts.live_optimize_v55 import save_scene
    scope=dict(auth(),character='unknown')
    ledger=tmp_path/'ledger.json';budget=SeriesBudget(ledger,scope)
    budget.reserve(10000,2500,'single',[(4,5)]);budget.command()
    assert SeriesBudget(ledger,scope).data['commands']==1
    with pytest.raises(ScreenError,match='chain_ledger_scope_mismatch'):
        SeriesBudget(ledger,auth())
    profile=tmp_path/'preferences.json'
    original={'purchase_scenes':{'killer/dark_lord':{'pending':'preserve'}},'settings':{'custom':True}}
    profile.write_text(json.dumps(original),encoding='utf-8')
    save_scene(profile,{'pending':'unknown'},'unknown')
    saved=json.loads(profile.read_text())
    assert saved['purchase_scenes']=={'killer/dark_lord':{'pending':'preserve'},'killer/unknown':{'pending':'unknown'}}
    assert saved['settings']==original['settings']


def test_series_persists_spent_and_unresolved_reservations_without_reset(tmp_path):
    path=tmp_path/'ledger.json';b=SeriesBudget(path,auth());b.observe(2500000)
    index=b.reserve(2500000,5000,'single',[(1,2)]);b.command();b.settle(index,2498000)
    b=SeriesBudget(path,auth());assert b.data['spent']==2000 and b.data['commands']==1
    b.reserve(2498000,1998000,'centre',[(2,3)])
    with pytest.raises(ScreenError,match='chain_budget_limit'):b.reserve(2498000,1,'single',[(3,4)])


def test_bp_rewards_do_not_replenish_budget_and_overruns_stop(tmp_path):
    b=SeriesBudget(tmp_path/'ledger.json',auth());b.observe(2500000);b.observe(2400000);b.observe(2600000)
    assert b.data['spent']==100000
    index=b.reserve(2600000,5000,'single',[(1,2)])
    with pytest.raises(ScreenError,match='chain_bp_exceeded'):b.settle(index,2594000)
    assert not b.data['reservations'][index]['settled']


def test_unknown_balance_never_reserves(tmp_path):
    b=SeriesBudget(tmp_path/'ledger.json',auth())
    with pytest.raises(ScreenError,match='chain_bp_unproven'):b.reserve(None,5000,'single',[(1,2)])
    assert not b.data['reservations']


def test_overrun_is_durably_blocked_after_process_restart(tmp_path):
    path=tmp_path/'ledger.json';b=SeriesBudget(path,auth())
    index=b.reserve(2500000,5000,'single',[(1,2)])
    with pytest.raises(ScreenError,match='chain_bp_exceeded'):b.settle(index,2494000)
    with pytest.raises(ScreenError,match='chain_bp_exceeded'):SeriesBudget(path,auth())


@pytest.mark.parametrize('changes',[{'spent':-1},{'commands':'0'},{'reservations':[{}]},{'last_bp':True}])
def test_corrupted_series_ledger_cannot_create_new_budget(tmp_path,changes):
    path=tmp_path/'ledger.json';b=SeriesBudget(path,auth());b.save()
    import json
    data=json.loads(path.read_text());data.update(changes);path.write_text(json.dumps(data))
    with pytest.raises(ScreenError,match='invalid_chain_ledger'):SeriesBudget(path,auth())


def test_settlement_cannot_be_reused_to_release_another_reservation(tmp_path):
    b=SeriesBudget(tmp_path/'ledger.json',auth());index=b.reserve(10000,5000,'single',[(1,2)])
    b.settle(index,8000)
    with pytest.raises(ScreenError,match='invalid_chain_ledger'):b.settle(index,8000)


@pytest.mark.parametrize('outcome',['confirmed','unresolved','cap','scope'])
def test_live_series_adapter_reserves_whole_operation_before_each_real_call(monkeypatch,tmp_path,outcome):
    """Adapter contract only: Device never emits Windows input."""
    from dataclasses import replace
    from scripts.live_optimize_v55 import install_limits
    import src.core.early_unlock as early
    import scripts.live_optimize_v55 as trial
    # Register restoration because the isolated live process wraps this entry.
    monkeypatch.setattr(early,'purchase_level',early.purchase_level)
    scene=web();d=Device([scene]);r=engine(d,dry_run=False,character='dark_lord')
    a=dict(auth(),priorities=list(r.priorities),expires_at=time.time()+60)
    frame=replace(d.capture(),bloodpoints=2500000);r.recent_frames.append((frame,scene,None));calls=[]
    monkeypatch.setattr(trial,'standard_target',lambda *a:None)
    from src.core.direct_buy import PriceBound
    monkeypatch.setattr('src.core.direct_buy.known_price',lambda *a:PriceBound(5000,'injected_budget_contract',{}))
    def physical(*args):
        import json
        current=json.loads((tmp_path/'ledger.json').read_text())
        assert len(current['reservations'])==1 and current['commands']==len(calls)+1
        assert not current['reservations'][0]['settled']
        calls.append(args[0])
        if outcome=='unresolved':raise ScreenError('purchase_not_confirmed')
    r.mouse.purchase=physical
    def original(frame,result,node=None,**kwargs):
        for _ in range(3):r.mouse.purchase(frame.to_screen(node.x,node.y),.05,r.stop,lambda:None)
        return replace(frame,bloodpoints=2498000),result,None
    r.buy=original
    b=install_limits(r,a,tmp_path)
    if outcome=='cap':b.data['spent']=1999000;b.save()
    if outcome=='scope':r.priorities.append('different')
    try:
        if outcome=='confirmed':
            r.buy(frame,scene,node=scene.nodes[1])
            assert len(calls)==3 and b.data['commands']==3 and b.data['spent']==2000
            assert len(b.data['reservations'])==1 and b.data['reservations'][0]['settled']
        else:
            reason={'unresolved':'purchase_not_confirmed','cap':'chain_budget_limit','scope':'chain_scope_changed'}[outcome]
            with pytest.raises(ScreenError,match=reason):r.buy(frame,scene,node=scene.nodes[1])
            assert len(calls)==(1 if outcome=='unresolved' else 0)
            if outcome=='unresolved':assert not b.data['reservations'][0]['settled']
    finally:r.stop.set()


def test_observer_file_lock_does_not_interrupt_purchase_or_reset_budget(monkeypatch,tmp_path):
    from scripts.live_optimize_v55 import make_status_callback
    from src.core.automation import State
    from types import SimpleNamespace
    r=engine(Device([web()]),dry_run=False);budget=SimpleNamespace(data=dict(spent=1234))
    def locked(*args):raise PermissionError('Windows observer denies rename')
    monkeypatch.setattr('scripts.live_optimize_v55.save',locked)
    callback=make_status_callback(r,budget,tmp_path)
    callback(State.WAIT_PURCHASE,'',None)
    assert r.report.entries[-1]['stage']=='status_write_error' and not r.stop.is_set() and not r.pause.is_set()
    assert budget.data['spent']==1234


def test_scene_save_preserves_latest_profile_including_new_perks_priority(tmp_path):
    import json
    from scripts.live_optimize_v55 import save_scene
    path=tmp_path/'preferences.json'
    latest=dict(priorities={'killer':['any_perk','goal']},settings={'custom':'preserve'},purchase_scenes={'survivor/':{'pending':'other'}})
    path.write_text(json.dumps(latest),encoding='utf-8');save_scene(path,{'pending':'this'})
    observed=json.loads(path.read_text());assert observed==dict(latest,purchase_scenes={'survivor/':{'pending':'other'},'killer/dark_lord':{'pending':'this'}})


@pytest.mark.parametrize('persistent',[False,True])
def test_windows_observer_lock_retries_commit_before_input_and_still_fails_closed(monkeypatch,tmp_path,persistent):
    import os,json
    b=SeriesBudget(tmp_path/'ledger.json',auth());replace_file=os.replace;calls=[]
    def locked(source,target):
        calls.append(True)
        if persistent or len(calls)<3:raise PermissionError('sharing violation')
        return replace_file(source,target)
    monkeypatch.setattr('src.core.series_budget.os.replace',locked)
    monkeypatch.setattr('src.core.series_budget.time.sleep',lambda _:None)
    if persistent:
        with pytest.raises(PermissionError):b.observe(2000000)
        assert len(calls)==7 and not b.path.exists()
    else:
        b.observe(2000000)
        assert len(calls)==3 and json.loads(b.path.read_text())['last_bp']==2000000
