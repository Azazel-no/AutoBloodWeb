"""Recheck an unconfirmed, already sent purchase using fresh frames only.

There is no mouse/keyboard device in this module. A saved report identifies the
attempt, not the current game state or a substitute for a fresh capture.
"""
from dataclasses import asdict
from datetime import datetime
import hashlib,json,math,time
from pathlib import Path
from src.core.models import NodeState,ScreenState
from src.core.screen_manager import ScreenError


def previous_attempt(folder):
    for path in sorted(Path(folder).glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True):
        if path.name.startswith(('calibration_gate_','recheck_')): continue
        try:
            events=json.loads(path.read_text(encoding='utf-8'))['entries']
            purchases=[e for e in events if e['stage']=='before_purchase']
            buttons=[e for e in events if e['stage']=='SendInput']
            if not buttons:continue
            # Later physical actions invalidate attribution to an older failed
            # attempt. Never fall back past a successful calibration or series
            # to unlock using a node that was bought by a different action.
            if not any(e['stage']=='failure' and e.get('reason')=='purchase_not_confirmed' for e in events): return None
            if len(purchases)!=1 or [e.get('flag') for e in buttons]!=[2,4] or any(e.get('result')!=1 for e in buttons): return None
            before=purchases[0]
            analysis=next(e for e in reversed(events) if e['stage']=='analysis' and e['time']<before['time'])
            target=next(n for n in analysis['nodes'] if n['id']==before['node'])
            if not target.get('icon') or target['state']!='available': return None
            frame=analysis['frame']; x,y=before['position']; width,height=frame['image_size']; roi=frame['roi']; client=frame['window']['client']
            point=[round(client['x']+roi['x']+x*roi['width']/width),round(client['y']+roi['y']+y*roi['height']/height)]
            cursor=next(e for e in events if e['stage']=='GetPhysicalCursorPos')
            if cursor.get('result')!=1 or cursor['actual']!=point: return None
            duration=(datetime.fromisoformat(buttons[1]['time'])-datetime.fromisoformat(buttons[0]['time'])).total_seconds()
            snapshot=next(e['path'] for e in events if e['stage']=='snapshot' and Path(e['path']).name=='before.png')
            return dict(report=str(path),frame=frame,target=target,point=point,nodes=analysis['nodes'],
                mode='click' if duration<.2 else 'hold',duration=duration,
                before=hashlib.sha256(Path(snapshot).read_bytes()).hexdigest())
        except (OSError,ValueError,KeyError,StopIteration,TypeError): return None
    return None


def verify_previous(context,screen,detector,mode,hold_seconds,stop,report,timeout=12):
    if context['mode']!=mode or (mode=='hold' and abs(context['duration']-hold_seconds)>.25):
        raise ScreenError('recheck_mode_changed')
    expected_window=context['frame']['window']; confirmations=0; deadline=time.monotonic()+timeout
    def same_geometry(window):
        current=asdict(window)
        return all(current[k]==expected_window[k] for k in ('hwnd','pid','client','monitor','dpi'))
    def previous_point(node):
        old=context['frame']; w,h=old['image_size']; r=old['roi']; c=old['window']['client']
        return (c['x']+r['x']+node['x']*r['width']/w,c['y']+r['y']+node['y']*r['height']/h)
    while time.monotonic()<deadline:
        if stop.is_set(): raise InterruptedError
        frame=screen.capture()
        if not frame.live or not same_geometry(frame.window): raise ScreenError('window_changed')
        if hasattr(screen.backend,'access_status'):
            access=screen.backend.access_status(frame.window)
            if not access['allowed']: raise ScreenError(access['reason'],str(access))
        result=detector.analyze(frame.image,'purchase_recheck')
        def find(point):
            found=[n for n in result.nodes if math.dist(frame.to_screen(n.x,n.y),point)<20]
            return min(found,key=lambda n:math.dist(frame.to_screen(n.x,n.y),point)) if found else None
        target=find(context['point'])
        anchors=0
        for node in context['nodes']:
            if node['id']==context['target']['id'] or not node.get('icon'): continue
            found=find(previous_point(node))
            anchors+=int(found is not None and found.icon_id==node['icon'])
        success=(result.screen_state==ScreenState.BLOODWEB and anchors>=3 and target is not None and
            target.state==NodeState.PURCHASED and target.state_confidence>=.8 and target.icon_id==context['target']['icon'])
        report.add('fresh_recheck',mouse_actions=0,anchors=anchors,after=target.state.value if target else 'missing',
            expected=context['target']['icon'],predicted=target.icon_id if target else None,frame=frame.metadata())
        if target and target.state==NodeState.ABSORBED: raise ScreenError('calibration_node_absorbed')
        if success: confirmations+=1
        elif target is None and result.screen_state==ScreenState.BLOODWEB and anchors>=3: pass
        else: confirmations=0
        if confirmations>=2:
            receipt=dict(verified=True,user_confirmed=False,window=frame.metadata()['window'],roi=asdict(frame.roi),point=context['point'],
                mode=mode,hold_seconds=hold_seconds,before=context['before'],after=hashlib.sha256(frame.image.tobytes()).hexdigest(),
                verification='two_fresh_frames_same_item_and_three_scene_anchors',source_attempt=context['report'],mouse_actions=0)
            return receipt,frame,result
        if stop.wait(.3): raise InterruptedError
    raise ScreenError('purchase_not_confirmed')
