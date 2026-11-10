"""Replay the addressed v5.2 failure through production read(); no input devices."""
import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.inspect_opening_failure_v49 import stored_frame
from src.core.automation import Automation,Settings
from src.core.bloodweb_detector import BloodwebDetector
from src.core.icon_library import IconLibrary


def replay(library=None):
    folder=ROOT/'debug/v52/live_session_a4df0fcf'
    session=json.loads((folder/'session.json').read_text(encoding='utf-8'))
    records={r['analysis_id']:r for r in json.loads((folder/'frames.json').read_text(encoding='utf-8'))}
    events=json.loads(Path(session['engines'][0]['report']).read_text(encoding='utf-8'))['entries']
    pixels={ident:stored_frame(item) for ident,item in records.items()}
    screen=SimpleNamespace()
    detector=BloodwebDetector(library or IconLibrary(ROOT).build(),'killer','unknown')
    detector.prepare()
    engine=Automation(screen,detector,SimpleNamespace(),session['authorization']['priorities'],
        Settings(dry_run=False,fast_early_levels=False,startup_delay=0),approved=True,hotkeys_ready=True)
    rows=[];latest=None
    for entry in events:
        if entry['stage']=='analysis':
            original=pixels[entry['analysis_id']]
            screen.capture=lambda original=original:replace(original,captured_at=time.monotonic())
            before=engine.opening.full_scans
            frame,result,plan=engine.read();latest=entry
            rows.append(dict(source=entry['analysis_id'],full=engine.opening.full_scans>before,
                mode=result.details.get('analysis_mode','FULL_SCAN'),next_node=plan.next_node,
                route=engine.opening.summary().get('persistent_route'),aborts=dict(engine.opening.aborts),
                fallback=next((e for e in reversed(engine.report.entries) if e['stage']=='opening_fallback'),None)))
        elif entry['stage']=='before_purchase':
            old=next(n for n in latest['nodes'] if n['id']==entry['node'])
            engine.attempted.append(original.to_screen(old['x'],old['y']));engine.pending_action=True
            if engine.opening.active:
                # Replay historical commands even when a new policy differs.
                point=engine.attempted[-1]
                import math
                engine.opening.expected_purchases.update(n.id for n in result.nodes
                    if math.dist(frame.to_screen(n.x,n.y),point)<8)
        elif entry['stage']=='purchase_outcome' and entry.get('outcome')=='purchased':
            engine.actions+=1;engine.pending_action=False
    output=dict(scope='historical pixels through production read; no new game inputs',
        historical_confirmed=engine.actions,new_inputs=0,full=engine.opening.full_scans,
        compact=engine.opening.quick_checks,local_library_matches=engine.opening.local_library_matches,
        aborts=engine.opening.aborts,frames=rows)
    return output


if __name__=='__main__':
    output=replay();folder=ROOT/'debug/v53';folder.mkdir(exist_ok=True)
    (folder/'production_replay.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in output.items() if k!='frames'},ensure_ascii=False))
    print(json.dumps([{k:r[k] for k in ('source','full','next_node','aborts')} for r in output['frames']],ensure_ascii=False))
