"""New explicitly authorized single-node scope, reusing the native GUI stand.

No chain, centre, prestige, keyboard injection, or resetting old ledgers.
The 5,000 BP reservation is a conservative published bound for standard nodes,
not an observed price. Event and anonymous targets cannot use this bound.
https://forums.bhvr.com/dead-by-daylight/kb/articles/167-bloodwebs
"""
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.core.runtime_paths import project_root
ROOT=project_root()
from src.core.models import NodeState,NodeType
from src.core.screen_manager import ScreenError


def validate_authorization(data):
    if (data.get('version')!='v53' or data.get('approved') is not True
            or type(data.get('authorized_limit')) is not int or not 1<=data['authorized_limit']<=50
            or type(data.get('max_nodes')) is not int or not 1<=data['max_nodes']<=50
            or type(data.get('max_bp')) is not int or not 1<=data['max_bp']<=250000
            or type(data.get('max_seconds')) is not int or not 1<=data['max_seconds']<=600
            or data.get('role')!='killer' or data.get('character')!='animatronic'
            or data.get('allow_prestige') is not False or data.get('allow_central_unlock') is not False
            or data.get('allow_chain') is not False or not isinstance(data.get('priorities'),list)):
        raise ValueError('invalid_v53_authorization')
    return data


class SingleBudget:
    def __init__(self,path,authorization,deadline):
        self.path=Path(path);self.deadline=deadline;self.authorization=validate_authorization(authorization)
        self.sha=hashlib.sha256(json.dumps(authorization,sort_keys=True).encode()).hexdigest()
        if self.path.exists():
            try:self.data=json.loads(self.path.read_text(encoding='utf-8'))
            except (ValueError,OSError) as exc:raise ScreenError('invalid_chain_ledger') from exc
            if self.data.get('authorization_sha256')!=self.sha:raise ScreenError('chain_ledger_scope_mismatch')
            used=self.data.get('reserved_commands')
            if type(used) is not int or not 0<=used<=authorization['authorized_limit']:raise ScreenError('invalid_chain_ledger')
            if self.data.get('reserved_nodes')!=used or self.data.get('reserved_bp')!=used*5000:raise ScreenError('invalid_chain_ledger')
        else:
            self.data=dict(authorization_sha256=self.sha,reserved_commands=0,reserved_nodes=0,reserved_bp=0,
                initial_bp=None,max_observed_drawdown=0,reservations=[],
                price_kind='published_standard_node_upper_bound_not_native_price',price_bound=5000,
                price_source='https://forums.bhvr.com/dead-by-daylight/kb/articles/167-bloodwebs')

    def check(self,bp):
        if time.monotonic()>=self.deadline:raise ScreenError('runtime_limit')
        if type(bp) is not int or bp<0:raise ScreenError('chain_bp_unproven')
        a=self.authorization;d=self.data
        if d['reserved_commands']>=a['authorized_limit'] or d['reserved_nodes']>=a['max_nodes']:raise ScreenError('action_limit')
        if d['reserved_bp']+5000>a['max_bp'] or bp<5000:raise ScreenError('chain_budget_limit')
        if d['initial_bp'] is not None:
            observed=max(0,d['initial_bp']-bp)
            if observed>d['reserved_bp'] or observed>a['max_bp']:raise ScreenError('chain_bp_exceeded')

    def reserve(self,bp,point,icon):
        self.check(bp);d=self.data
        if d['initial_bp'] is None:d['initial_bp']=bp
        d['max_observed_drawdown']=max(d['max_observed_drawdown'],d['initial_bp']-bp)
        d['reserved_commands']+=1;d['reserved_nodes']+=1;d['reserved_bp']+=5000
        d['reservations'].append(dict(point=list(point),icon=icon,bp_before=bp,at=time.monotonic()))
        self.path.parent.mkdir(parents=True,exist_ok=True);temp=self.path.with_suffix('.tmp')
        with temp.open('w',encoding='utf-8') as stream:
            json.dump(d,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,self.path)


def standard_target(engine,node):
    from src.core.direct_buy import standard_target as validate
    return validate(engine,node)


def install_limits(engine,authorization,path,deadline):
    budget=SingleBudget(path.with_suffix('.resources.json'),authorization,deadline)
    engine.settings.max_input_commands=authorization['authorized_limit']
    engine.settings.auto_prestige=False;engine.settings.fast_early_levels=False
    buy=engine.buy;purchase=engine.mouse.purchase
    def scoped_buy(frame,result,node=None,point=None,**kwargs):
        if point is not None:raise ScreenError('chain_explicit_permission_required')
        standard_target(engine,node);budget.check(frame.bloodpoints)
        if engine.settings.character!='animatronic':
            raise ScreenError('chain_scope_changed')
        for ident in engine.priorities:
            if ident=='any_perk':continue
            entry=engine.detector.matcher.by_id.get(ident)
            if entry is None or entry.role!='killer' or entry.character not in (None,'animatronic'):
                raise ScreenError('chain_scope_changed')
        return buy(frame,result,node=node,**kwargs)
    def scoped_mouse(point,duration,stop,check):
        frame,result,_=engine.recent_frames[-1]
        matches=[n for n in result.nodes if n.state!=NodeState.CENTER and math.dist(frame.to_screen(n.x,n.y),point)<max(8,n.radius*.4)]
        if len(matches)!=1:raise ScreenError('unsafe_node')
        standard_target(engine,matches[0]);check()
        budget.reserve(frame.bloodpoints,point,matches[0].icon_id)
        engine.trace('single_scope_reserved',commands=budget.data['reserved_commands'],nodes=budget.data['reserved_nodes'],
            reserved_bp=budget.data['reserved_bp'],native_price=None,published_upper_bound=5000)
        return purchase(point,duration,stop,check)
    engine.buy=scoped_buy;engine.mouse.purchase=scoped_mouse


if __name__=='__main__':
    import traceback
    def startup_error(kind,value,tb):
        (ROOT/'debug/v53/launcher_error.txt').write_text(''.join(traceback.format_exception(kind,value,tb)),encoding='utf-8')
    sys.excepthook=startup_error
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('authorization',type=Path)
    parser.add_argument('--replace-session',type=Path)
    args=parser.parse_args()
    if args.replace_session:
        # Gracefully close only this owned, idle, zero-input test window.
        # The existing authorization state/deadline/ledgers remain in place.
        import ctypes
        from ctypes import wintypes as W
        session=args.replace_session.resolve()
        if not session.is_relative_to((ROOT/'debug/v53').resolve()):raise ValueError('invalid_replacement_scope')
        current=json.loads(session.read_text(encoding='utf-8'))
        authorization=json.loads(args.authorization.read_text(encoding='utf-8'))
        if current['authorization']!=authorization or current['input_calls']!=0 or current['status']!='paused':raise ValueError('replacement_not_idle')
        if any(e['attempts'] or e['pending'] for e in current['engines']):raise ValueError('replacement_input_unresolved')
        u=ctypes.WinDLL('user32',use_last_error=True);u.IsWindow.argtypes=[W.HWND]
        u.GetWindowThreadProcessId.argtypes=[W.HWND,ctypes.POINTER(W.DWORD)]
        u.PostMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM]
        pid=W.DWORD();u.GetWindowThreadProcessId(current['hwnd'],ctypes.byref(pid))
        if pid.value!=current['pid']:raise ValueError('replacement_window_changed')
        seed=session.parent/'test_preferences.json'
        args.authorization.with_suffix('.profile_seed.json').write_bytes(seed.read_bytes())
        if not u.PostMessageW(current['hwnd'],0x10,0,0):raise OSError(ctypes.get_last_error(),'close_owned_test')
        until=time.monotonic()+10
        while u.IsWindow(current['hwnd']) and time.monotonic()<until:time.sleep(.1)
        if u.IsWindow(current['hwnd']):raise ValueError('old_test_still_running')
        from scripts.live_test_v48 import acquire_scope
        while True:
            try:probe_lock=acquire_scope(args.authorization);probe_lock.close();break
            except ValueError:
                if time.monotonic()>=until:raise
                time.sleep(.1)
    from scripts.live_test_v48 import main
    raise SystemExit(main(args.authorization))
