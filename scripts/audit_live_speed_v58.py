"""Exclusive wall-time accounting from retained exact intervals, no input."""
import argparse,csv,json
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path


def exclusive(samples,start,end):
    intervals=[]
    for index,s in enumerate(samples):
        if s.get('kind')=='background_io':continue
        if 'started_at' not in s or 'ended_at' not in s:continue
        a=max(start,s['started_at']);b=min(end,s['ended_at'])
        if b<=a:continue
        name=s['stage']
        if name=='detector_total':name=s.get('scan_kind','SCAN_KIND_UNKNOWN')
        intervals.append((a,b,name,index,s))
    boundaries=sorted({start,end,*[x for a,b,*_ in intervals for x in (a,b)]})
    totals=Counter();segments=[];calls={}
    for a,b in zip(boundaries,boundaries[1:]):
        middle=(a+b)/2;active=[i for i in intervals if i[0]<=middle<i[1]]
        # The shortest containing actual interval owns this piece. Parent
        # preparation/recovery/map-read intervals keep only their self time.
        chosen=min(active,key=lambda i:i[4]['ended_at']-i[4]['started_at']) if active else None
        stage=chosen[2] if chosen else 'UNATTRIBUTED'
        totals[stage]+=b-a
        if chosen:calls.setdefault(stage,set()).add(chosen[3])
        segments.append(dict(start=a,end=b,stage=stage,seconds=b-a))
    assert abs(sum(totals.values())-(end-start))<1e-6
    return dict(totals),{k:len(v) for k,v in calls.items()},segments


def report(folder,destination):
    destination.mkdir(parents=True,exist_ok=True)
    events=[json.loads(l) for l in (folder/'events.jsonl').read_text(encoding='utf-8').splitlines()]
    sessions=[json.loads(p.read_text(encoding='utf-8')) for p in [folder/'session.json',*sorted((folder/'runs').glob('*_session.json'))]]
    rows=[]
    for session in sessions:
        if not session.get('action_report'):continue
        path=Path(session['action_report']).with_suffix('.performance.json')
        snapshot=path if path.is_file() else path.with_suffix('.checkpoint.json')
        if not snapshot.is_file():continue
        perf=json.loads(snapshot.read_text(encoding='utf-8'));stream=path.with_suffix('.timeline.jsonl')
        if not stream.is_file():continue
        samples=[json.loads(l) for l in stream.read_text(encoding='utf-8').splitlines()]
        anchor=perf['clock_anchor'];clock_offset=anchor['unix']-anchor['perf']
        down=None;input_intervals=[]
        for event in events:
            if event['session']!=session['pid'] or event['stage']!='SendInput':continue
            stamp=datetime.fromisoformat(event['time']).timestamp()-clock_offset
            if event.get('flag')==2:down=stamp
            elif event.get('flag')==4 and down is not None:
                input_intervals.append(dict(stage='MouseDown_to_MouseUp',started_at=down,ended_at=stamp));down=None
        for level in perf['levels']['records']:
            if not(level.get('complete') and level.get('observed_from_transition') and not level['pauses']):continue
            start,end=level['started_at'],level['ended_at']
            totals,calls,segments=exclusive(samples+input_intervals,start,end)
            selected=[e for e in events if e['session']==session['pid'] and e['stage']=='analysis'
                      and start<=e['frame']['captured_at']<end]
            scan_counts=dict(Counter(e['scan_kind'] for e in selected))
            known_captures={e['frame']['captured_at'] for e in selected}
            overlay_intervals=[s for s in samples if s['stage'] in ('preview_capture','overlay_capture')
                               and 'started_at' in s and start<=s['started_at']<end]
            captures=len(known_captures)+len(overlay_intervals)
            row=dict(session=session['pid'],level=level['level'],elapsed=level['elapsed'],exclusive_seconds=totals,
                exclusive_owner_counts=calls,scan_counts_in_physical_window=scan_counts,
                recorded_full=level['full'],recorded_compact=level['fast'],replans=level['replans'],
                captures_lower_bound=captures,repeat_captures_lower_bound=max(0,captures-1),
                captures_do_not_include_all_failed_or_target_confirmation_captures=True,
                commands=level['commands'],purchases=level['purchases'],chains=level['chains'],
                purchases_per_command=level['purchases']/level['commands'] if level['commands'] else None,
                total_matches_level_interval=abs(sum(totals.values())-level['elapsed'])<1e-6,
                source=str(snapshot),timeline=str(stream),recovered_from_checkpoint=snapshot!=path)
            rows.append(row)
            for segment in segments:
                segment.update(start_ms=round((segment['start']-start)*1000,3),end_ms=round((segment['end']-start)*1000,3),
                    utc=datetime.fromtimestamp(segment['start']+clock_offset,timezone.utc).isoformat(timespec='milliseconds'))
            out=destination/f'{session["pid"]}_level_{level["level"]}_exclusive.csv'
            with out.open('w',encoding='utf-8-sig',newline='') as f:
                writer=csv.DictWriter(f,list(segments[0]) if segments else []);writer.writeheader();writer.writerows(segments)
    result=dict(levels=rows,paid_inputs=0,accounting='Exact intervals partition each full level; nested parents retain self time only. Duration-only historical samples excluded.')
    (destination/'live_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps([dict(level=r['level'],elapsed=round(r['elapsed'],3),full=r['recorded_full'],compact=r['recorded_compact'],
        commands=r['commands'],purchases=r['purchases'],top=sorted(r['exclusive_seconds'].items(),key=lambda x:-x[1])[:4]) for r in rows]))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);p.add_argument('destination',type=Path)
    a=p.parse_args();report(a.folder,a.destination)
