"""Bounded dismissal of a positively recognized reward dialog, never unknown UI."""
import time
from src.core.models import ScreenState
from src.core.screen_manager import ScreenError
from src.core.click_lifecycle import transaction


def tutorial_target(frame,result):
    """Read-only certificate shared by session preparation and the controller."""
    proof=result.details.get('tutorial') or {}
    generic=proof.get('kind')=='continue'
    regions={'continue'} if generic else {'title','body','continue'}
    if (result.screen_state!=ScreenState.TUTORIAL or
            (not generic and (result.level!=2 or result.level_confidence<.95 or frame.level!=2 or frame.level_confidence<.95)) or
            set(proof.get('regions',{}))!=regions or
            set(proof.get('scores',{}))!=regions or
            min(proof['scores'].values())<.96 or not proof.get('source_sha256') or
            tuple(proof['regions']['continue'])!=result.ok_box):return None
    x,y,w,h=result.ok_box
    return x+w/2,y+h/2


@transaction('tutorial',intent='MODAL_DISMISS')
def dismiss_tutorial(engine,frame,result,deadline=None):
    """One unpaid continuation command, preserving any pending purchase."""
    from src.core.automation import State
    if not engine.approved or engine.settings.dry_run:raise ScreenError('calibration_required')
    deadline=min(deadline or float('inf'),time.monotonic()+min(15,engine.settings.refresh_timeout))
    current=(frame,result,None);previous=None;last_stamp=float('-inf');clicked=0;source=None;last_click=0.
    old_region=getattr(engine.mouse,'target_region',None)
    engine.mouse.release()
    try:
        while time.monotonic()<deadline:
            engine.gate();fresh,web,plan=current;observed=(fresh,web,None)
            if (not fresh.live or fresh.probe_only or not fresh.window.foreground or
                    fresh.window.signature!=frame.window.signature):raise ScreenError('live_frame_required')
            if not 0<=time.monotonic()-fresh.captured_at<=2:raise ScreenError('stale_frame')
            if fresh.captured_at<=last_stamp:
                engine.wait(.1);current=engine.recover_read(deadline,retry_stale=True);continue
            last_stamp=fresh.captured_at
            if web.screen_state==ScreenState.BLOODWEB:
                if engine.stable_scene(previous,observed):
                    engine.trace('tutorial_dismissed',commands=clicked,analysis_id=web.analysis_id)
                    return current
                previous=observed
            elif web.screen_state==ScreenState.TUTORIAL:
                proof=web.details.get('tutorial') or {}
                if tutorial_target(fresh,web) is None:raise ScreenError('unknown_screen')
                # Native antialiasing variants certify the same CONTINUE word.
                # Their source SHA identifies the reference, not a new dialog.
                if source is not None and source!=proof['source_sha256'] and proof.get('kind')!='continue':raise ScreenError('frame_unstable')
                source=proof['source_sha256']
                if engine.stable_scene(previous,observed):
                    if clicked>=3:raise ScreenError('tutorial_not_dismissed')
                    if clicked and time.monotonic()-last_click<.5:
                        previous=None;engine.wait(.1);current=engine.recover_read(deadline,retry_stale=True);continue
                    engine.check_input_budget();x,y,w,h=web.ok_box
                    point=engine.check(fresh,x+w/2,y+h/2)
                    engine.mouse.target_region=(point,min(w,h)*.35)
                    engine.emit(State.RECOVERING,'tutorial_dismissing')
                    engine.attempts+=1;clicked+=1;last_click=time.monotonic();engine.performance.levels.input()
                    engine.trace('modal_input',kind='bloodweb_tutorial',attempt=clicked,point=point,analysis_id=web.analysis_id)
                    engine.mouse.purchase(point,.05,engine.stop,lambda:engine.check_held(fresh,x+w/2,y+h/2))
                    engine.mouse.release()
                    if hasattr(engine.screen,'parking_point') and hasattr(engine.mouse,'park'):
                        parked=engine.screen.parking_point(fresh)
                        if parked:engine.mouse.park(parked,engine.stop,lambda:engine.check_held(fresh,x+w/2,y+h/2))
                    previous=None
                else:previous=observed
            else:previous=None  # An animation/unknown UI never receives a click.
            engine.wait(max(0,.3-(time.monotonic()-fresh.captured_at)))
            current=engine.recover_read(deadline,retry_stale=True)
        raise ScreenError('modal_recovery_timeout')
    finally:
        engine.mouse.release();engine.mouse.target_region=old_region


@transaction('reward',intent='MODAL_DISMISS')
def dismiss_reward(engine,frame,result,external_ready=False,expected_level=None):
    from src.core.automation import State
    if (not engine.approved or engine.settings.dry_run or
            (not external_ready and (not engine.prestige_approved or not engine.settings.auto_prestige))
            or engine.pending_action or engine.persisted_scene):
        raise ScreenError('prestige_manual')
    if result.screen_state!=ScreenState.REWARD or not result.ok_box:raise ScreenError('unknown_screen')
    deadline=time.monotonic()+min(15,engine.settings.refresh_timeout)
    current=(frame,result,None);last_click=0.;clicks=0;previous=None;last_stamp=float('-inf')
    engine.mouse.release()
    try:
        while time.monotonic()<deadline:
            engine.gate()
            fresh,web,plan=current
            observed=(fresh,web,None)
            if (not fresh.live or fresh.probe_only or not fresh.window.foreground or
                    fresh.window.signature!=frame.window.signature):raise ScreenError('live_frame_required')
            if fresh.captured_at<=last_stamp:
                engine.wait(.5);current=engine.recover_read(deadline,retry_stale=True);continue
            last_stamp=fresh.captured_at
            if web.screen_state==ScreenState.BLOODWEB:
                if expected_level is not None and (web.level!=expected_level or web.level_confidence<.95
                        or fresh.level!=expected_level or fresh.level_confidence<.95):
                    previous=None;engine.poll();current=engine.recover_read(deadline,retry_stale=True);continue
                if engine.stable_scene(previous,observed):
                    engine.trace('reward_dismissed',commands=clicks,analysis_id=web.analysis_id)
                    return current
                previous=observed
            elif web.screen_state==ScreenState.REWARD and web.ok_box:
                if engine.stable_scene(previous,observed):
                    if clicks>=5:raise ScreenError('modal_attempt_limit')
                    engine.check_input_budget()
                    x,y,w,h=web.ok_box
                    point=engine.check(fresh,x+w/2,y+h/2)
                    if time.monotonic()-last_click<.5:
                        engine.wait(.5-(time.monotonic()-last_click))
                        # The next command always uses another fresh observation.
                        previous=None;current=engine.recover_read(deadline,retry_stale=True);continue
                    engine.emit(State.REWARD,'reward_dismissing')
                    engine.mouse.target_region=(point,min(w,h)*.35)
                    engine.attempts+=1;clicks+=1;last_click=time.monotonic()
                    engine.performance.levels.input()
                    engine.trace('modal_input',kind='rewards_unlocked',attempt=clicks,point=point,analysis_id=web.analysis_id)
                    engine.mouse.purchase(point,.05,engine.stop,lambda:engine.check_held(fresh,x+w/2,y+h/2))
                    engine.mouse.release();previous=None
                else:previous=observed
            else:
                # No repeated click when a different screen/animation appears.
                previous=None
            engine.wait(max(0,.5-(time.monotonic()-fresh.captured_at)))
            current=engine.recover_read(deadline,retry_stale=True)
        raise ScreenError('modal_recovery_timeout')
    finally:engine.mouse.release()
