"""Opt-in ordinary-worker bridge to the existing native preview adapter."""
import json,time
from src.core.chain_plan import native_candidate_route
from src.core.screen_manager import ScreenError
from src.core.chain_trial import execute_short_chain


def install(engine,root):
    # These are reviewed glyph locations/source hashes, never saved prices.
    calibration=root/'data/native_total_price_locator.json'
    single=root/'data/native_price_locator.json'
    if not calibration.is_file() or not single.is_file():return
    from scripts.native_price_v55 import NativePriceLocator
    try:
        engine.native_price_locator=NativePriceLocator(root,json.loads(single.read_text(encoding='utf-8')),engine.screen.resources)
    except (OSError,ValueError,KeyError):
        engine.trace('chain_skipped',reason='native_price_calibration_unavailable',paid_inputs=0,source='ordinary_start')
        return
    folder=engine.report.path.with_suffix('');folder.mkdir(parents=True,exist_ok=True)
    (folder/'native_total_price_locator.json').write_bytes(calibration.read_bytes())
    started=time.time();scope=engine.report.path.stem
    def execute(plan,permit,previous,latest):
        engine.native_chain_permit=permit
        try:return execute_short_chain(engine,plan,permit,previous,latest)
        finally:engine.native_chain_permit=None
    engine.execute_native_chain=execute
    def dispatch(frame,result):
        engine.native_chain_fallback=None
        from src.core.direct_buy import chain_probe_allowed,reject_chain
        if not chain_probe_allowed(engine,frame,result):return None
        if len(native_candidate_route(result,engine.priorities,engine.settings.priority_mode).route)<2:return None
        # GUI presentation is removed before new clean/BP/preview evidence.
        engine.event(engine.state,'hide_for_dialog',None)
        engine.wait(.02)
        frame,result,plan=engine.recover_read(retry_stale=True)
        engine.native_chain_fallback=(frame,result,plan)
        if len(native_candidate_route(result,engine.priorities,engine.settings.priority_mode).route)<2:return None
        request=dict(execute=True,continuous=True,approved=True,acknowledge_queue_cancellation_unverified=True,
            scope_id=scope,ledger='chain_ledger.json',max_commands=30,max_nodes=60,max_bp=150000,expires_at=started+600,
            max_chain_items=5 if engine.settings.turbo_burst else 3)
        (folder/'chain_probe_requested.json').write_text(json.dumps(request),encoding='utf-8')
        # Reuse the tested parser/probe and native input path; no second Mouse.
        from scripts.native_chain_v55 import probe_if_requested
        try:return probe_if_requested(engine,folder,frame,result)
        except ScreenError as exc:
            if engine.pending_action or engine.stop.is_set():raise
            if exc.code not in ('chain_preview_or_price_unproven','chain_map_changed','chain_geometry_changed',
                    'chain_two_clean_frames_required','unsafe_node','runtime_limit','chain_budget_limit'):
                raise
            engine.trace('chain_skipped',reason=exc.code,paid_inputs=0,source='ordinary_start')
            engine.native_chain_fallback=engine.recover_read(retry_stale=True)
            reject_chain(engine,*engine.native_chain_fallback[:2])
            return None
    engine.native_chain_dispatch=dispatch
