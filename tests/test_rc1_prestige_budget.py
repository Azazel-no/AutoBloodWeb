"""One authorized prestige operation, including proven no-debit rechecks."""
import json,time
from dataclasses import replace
import pytest
from src.core.live_session import LiveSession
from src.core.models import ScreenState,NodeType
from src.core.scene_memory import record_scene
from src.core.screen_manager import ScreenError
from tests.test_automation import Device,engine,web


def setup(tmp_path):
    now=time.time();a=dict(role='killer',character='executioner',max_bp=1000000,max_nodes=300,max_commands=500,
        allow_prestige=True,max_prestiges=1,allow_chain=True,allow_turbo_burst=True,started_at=now,expires_at=now+60)
    path=tmp_path/'authorization.json';path.write_text(json.dumps(a),encoding='utf-8');s=LiveSession(path)
    r=web();r.screen_state=ScreenState.PRESTIGE_READY;r.level=50;r.level_confidence=.99
    r.nodes=r.nodes[:1];r.nodes[0].type=NodeType.PRESTIGE
    d=Device([r]);d.role='killer';e=engine(d,dry_run=False,auto_prestige=True);e.settings.character='executioner'
    f=replace(d.capture(),bloodpoints=50000);point=f.to_screen(r.nodes[0].x,r.nodes[0].y)
    e.recent_frames.append((f,r,None));e.scene_record=record_scene(f,r,[],dict(point=list(point),radius=20,prestige=True,price=20000))
    e.prestige_ui_proof=None;e.pending_action=True
    future=[(f,r,None)];e.read=lambda:future[0];s.install(e)
    return s,e,d,f,point,future


def test_consumed_ui_proof_price_is_reserved_once_for_one_prestige(tmp_path):
    s,e,d,f,p,future=setup(tmp_path)
    e.mouse.purchase(p,.05,e.stop,lambda:None)
    # Actual activate_ready permits a further pulse only after two fresh,
    # unchanged ready screens and no debit; that gate remains in production.
    e.mouse.purchase(p,.05,e.stop,lambda:None)
    assert len(d.presses)==2 and s.budget.data['commands']==2
    assert len(s.budget.data['reservations'])==1 and s.budget.data['reservations'][0]['upper_bound']==20000
    future[0]=(replace(f,bloodpoints=30000),web(),None)
    e.pending_action=False;e.scene_record=None;e.prestige_actions=1;e.read()
    assert s.budget.data['spent']==20000 and s.budget.data['reservations'][0]['settled']
    with pytest.raises(ScreenError,match='prestige_manual'):s.budget.reserve(30000,20000,'prestige',[p])
    s.lock.close()


@pytest.mark.parametrize('change',['debit','price','point'])
def test_prestige_repeat_cannot_reuse_a_reserve_after_evidence_changes(tmp_path,change):
    s,e,d,f,p,future=setup(tmp_path);e.mouse.purchase(p,.05,e.stop,lambda:None)
    if change=='debit':e.recent_frames[-1]=(replace(f,bloodpoints=49999),web(),None)
    if change=='price':e.scene_record['pending']['price']=19999
    if change=='point':p=(p[0]+1,p[1])
    with pytest.raises(ScreenError,match='prestige_price_unproven'):e.mouse.purchase(p,.05,e.stop,lambda:None)
    assert len(d.presses)==1 and len(s.budget.data['reservations'])==1
    s.lock.close()


@pytest.mark.parametrize('price',[None,0,True,20001])
def test_first_prestige_cannot_exceed_the_explicit_price_cap(tmp_path,price):
    s,e,d,f,p,future=setup(tmp_path);e.scene_record['pending']['price']=price
    with pytest.raises(ScreenError,match='prestige_price_unproven'):e.mouse.purchase(p,.05,e.stop,lambda:None)
    assert not d.presses and not s.budget.data['reservations']
    s.lock.close()
