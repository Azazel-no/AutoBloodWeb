"""Recover a historical input method only from specific audited evidence."""
import copy,hashlib,json
from datetime import datetime


def recovery_evidence(receipt,settings,payload):
    if not receipt or receipt.get('blocked_reason')!='purchase_not_confirmed':return None,'input_recovery_not_allowed'
    if receipt.get('mode')!=settings['purchase_mode'] or receipt.get('hold_seconds')!=settings['hold_seconds']:return None,'input_recovery_method_changed'
    entries=payload.get('entries',[])
    settings_records=[e.get('settings',e.get('values',{})) for e in entries if e.get('stage')=='run_settings']
    if settings_records and any(s.get('purchase_mode')!=receipt['mode'] or s.get('hold_seconds')!=receipt['hold_seconds'] for s in settings_records):return None,'input_recovery_method_changed'
    failures=[e.get('reason') for e in entries if e.get('stage')=='failure']
    if not failures or any(r!='purchase_not_confirmed' for r in failures):return None,'input_recovery_input_error'
    moves=[e for e in entries if e.get('stage')=='GetPhysicalCursorPos']
    buttons=[e for e in entries if e.get('stage')=='SendInput']
    if not buttons or len(buttons)%2 or not moves:return None,'input_recovery_no_report'
    if any(e.get('result')!=1 or e.get('actual')!=e.get('requested') for e in moves):return None,'input_recovery_input_error'
    if any(e.get('result')!=1 for e in buttons):return None,'input_recovery_input_error'
    for down,up in zip(buttons[::2],buttons[1::2]):
        if down.get('flag')!=2 or up.get('flag')!=4:return None,'input_recovery_input_error'
        try:seconds=(datetime.fromisoformat(up['time'])-datetime.fromisoformat(down['time'])).total_seconds()
        except (KeyError,ValueError):return None,'input_recovery_no_report'
        if receipt['mode']=='click' and not .025<=seconds<=.2:return None,'input_recovery_method_changed'
        if receipt['mode']=='hold' and abs(seconds-receipt['hold_seconds'])>max(.15,receipt['hold_seconds']*.25):return None,'input_recovery_method_changed'
    confirmed=False;reds=0
    for e in entries:
        if e.get('stage')=='before_purchase':reds=0
        elif e.get('stage')=='purchase_observation':
            if e.get('after')=='purchased':reds+=1;confirmed=confirmed or reds>=2
            elif e.get('after')!='missing':reds=0
    if not confirmed:return None,'input_recovery_no_success'
    if len([e for e in entries if e.get('stage')=='physical_action_complete' and e.get('mouse_released')])!=len(buttons)//2:
        return None,'input_recovery_input_error'
    return dict(accepted_commands=len(buttons),confirmed_purchase=True,method=receipt['mode']),None


def legacy_purchase_scene(payload):
    """Keep the last uncertain target blocked when recovering an old method."""
    entries=payload.get('entries',[])
    before=next((i for i in range(len(entries)-1,-1,-1) if entries[i].get('stage')=='before_purchase'),None)
    if before is None:return None
    analysis=next((e for e in reversed(entries[:before]) if e.get('stage')=='analysis' and e.get('nodes') and e.get('frame')),None)
    if not analysis:return None
    frame=analysis['frame'];roi=frame['roi'];size=frame['image_size'];window=frame['window']
    def point(x,y):return [round(window['client']['x']+roi['x']+x*roi['width']/size[0]),round(window['client']['y']+roi['y']+y*roi['height']/size[1])]
    target=next((n for n in analysis['nodes'] if n['id']==entries[before].get('node')),None)
    if target is None:return None
    attempted=[e['requested'] for e in entries if e.get('stage')=='GetPhysicalCursorPos' and e.get('result')==1]
    if not attempted:return None
    return dict(window=window,attempted=attempted,nodes=[dict(point=point(n['x'],n['y']),radius=n.get('radius',32),state=n['state'],icon=n.get('icon')) for n in analysis['nodes']],
        pending=dict(point=point(target['x'],target['y']),radius=target.get('radius',32),icon=target.get('icon')),source_analysis=analysis.get('analysis_id'),legacy=True)


def propose_method_recovery(root,prefs):
    receipt=prefs.recovery_calibration()
    if receipt is None:
        old=prefs.data.get('calibration',{})
        changed=old.get('blocked_reason')=='purchase_not_confirmed' and (old.get('mode')!=prefs.settings['purchase_mode'] or old.get('hold_seconds')!=prefs.settings['hold_seconds'])
        return None,'input_recovery_method_changed' if changed else 'input_recovery_not_allowed'
    folder=root/'debug/actions'
    paths=sorted((p for p in folder.glob('*.json') if not p.name.endswith('.performance.json') and p.name[:8].isdigit()),key=lambda p:p.stat().st_mtime,reverse=True)[:20]
    for path in paths:
        try:
            data=path.read_bytes();payload=json.loads(data)
        except (OSError,ValueError):continue
        if not any(e.get('stage')=='failure' for e in payload.get('entries',[])):continue
        evidence,reason=recovery_evidence(receipt,prefs.settings,payload)
        if evidence is None:return None,reason
        try:scene=legacy_purchase_scene(payload)
        except (KeyError,TypeError,ValueError):scene=None
        if scene is None:return None,'input_recovery_no_report'
        restored=copy.deepcopy(receipt);restored.pop('blocked_reason',None)
        restored['previous_purchase_failure']='purchase_not_confirmed'
        restored['method_recovery']=dict(**evidence,report=path.relative_to(root).as_posix(),sha256=hashlib.sha256(data).hexdigest(),requires_new_physical_f9=True)
        restored['recovered_purchase_scene']=scene
        restored['recovered_scene_key']=receipt.get('role','survivor')+'/'+(receipt.get('character','') if receipt.get('role')=='killer' else '')
        return restored,None
    return None,'input_recovery_no_report'
