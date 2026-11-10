"""Shared, idempotent observed outcomes. Never grants input or refunds budgets."""
import hashlib,json,os,math
from pathlib import Path
from contextlib import contextmanager
from src.core.screen_manager import ScreenError


def operation_id(scene):
    pending=scene.get('pending') or {}
    if pending.get('operation_id'):return pending['operation_id']
    chain=pending.get('chain')
    key=(dict(chain=chain.get('map_id'),stamps=chain.get('preview_stamps'),points=chain.get('physical_points')) if chain else
         dict(window=scene.get('window'),level=scene.get('level'),stamp=scene.get('captured_at'),pending=pending))
    return hashlib.sha256(json.dumps(key,sort_keys=True).encode()).hexdigest()[:32]


class ActionOutcomes:
    def __init__(self,path):self.path=Path(path)

    @contextmanager
    def locked(self):
        import msvcrt
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.path.with_suffix('.lock').open('a+b') as handle:
            handle.seek(0,2)
            if not handle.tell():handle.write(b'0');handle.flush()
            handle.seek(0)
            try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            except OSError as exc:raise ScreenError('outcome_journal_busy') from exc
            yield

    def reconcile(self,scene,observations,source):
        """Two real independent clean observations of every physical point."""
        from src.core.scene_memory import same_window
        from src.core.models import ScreenState,NodeState
        pending=scene.get('pending') or {};chain=pending.get('chain')
        if pending.get('chain_blocked_reason'):raise ScreenError(pending['chain_blocked_reason'])
        points=(chain or pending.get('batch') or {}).get('physical_points') if chain or pending.get('batch') else [pending.get('point')]
        if not points or any(p is None for p in points) or len(observations)!=2:raise ScreenError('outcome_evidence_required')
        points=[list(p) for p in points]
        stamps=[f.captured_at for f,r in observations]
        if not stamps[0]<stamps[1]:raise ScreenError('outcome_evidence_required')
        states=[];ring_certificates=[]
        for frame,result in observations:
            if not frame.live or frame.probe_only or not same_window(scene,frame) or result.level!=scene['level'] or result.screen_state!=ScreenState.BLOODWEB:
                raise ScreenError('outcome_context_changed')
            current=[]
            if chain:
                from src.core.scene_memory import chain_matching_node,chain_ring_certificate,chain_unexpected_points
                if chain_unexpected_points(scene,frame,result):raise ScreenError('chain_unexpected_purchase')
            for point in points:
                old=[n for n in scene['nodes'] if math.dist(n['point'],point)<5]
                # Match ChainEvidence's original-ring tolerance, rather than
                # rejecting its already certified shrinking purchased ring.
                tolerance=(max(5,old[0]['radius']*.2) if chain else max(8,old[0]['radius']*.4)) if len(old)==1 else 5
                found=([n] if (n:=chain_matching_node(scene,frame,result,point)) is not None else []) if chain else [n for n in result.nodes if math.dist(frame.to_screen(n.x,n.y),point)<tolerance]
                if len(old)!=1 or len(found)!=1:raise ScreenError('outcome_evidence_required')
                node=found[0]
                certificate=chain_ring_certificate(scene,frame,node,point) if chain else None
                if certificate:ring_certificates.append(certificate)
                if old[0].get('icon') and node.icon_id and old[0]['icon']!=node.icon_id:raise ScreenError('outcome_identity_changed')
                batch=pending.get('batch') or {}
                from src.core.geometry import purchasable
                missed=(batch.get('deferred_verification') is True and type(batch.get('inputs_completed_at')) in (int,float)
                    and frame.captured_at-batch['inputs_completed_at']>=1.5 and node.state in (NodeState.AVAILABLE,NodeState.LOCKED)
                    and node.state_confidence>=.8 and node.geometry_confidence>=.7
                    and (node.state==NodeState.LOCKED or purchasable(node,result.nodes,result.edges)))
                if not (node.state==NodeState.PURCHASED and node.state_confidence>=.8 or node.state==NodeState.ABSORBED and node.state_confidence>=.75 or missed):
                    raise ScreenError('outcome_evidence_required')
                current.append(node.state.value)
            states.append(current)
        if states[0]!=states[1]:raise ScreenError('outcome_evidence_required')
        ident=operation_id(scene)
        row=dict(operation_id=ident,points=points,states=states[-1],stamps=stamps,source=source,
            confirmed_nodes=states[-1].count('purchased'),absorbed_nodes=states[-1].count('absorbed'),
            missed_nodes=states[-1].count('available')+states[-1].count('locked'),
            bp_delta=None,reservation_refunded=False,authorizes_input=False)
        if ring_certificates:row['contained_ring_observations']=ring_certificates
        if pending.get('chain_geometry_review'):row['chain_geometry_review']=pending['chain_geometry_review']
        before=scene.get('bp_before');after=observations[-1][0].bloodpoints
        if type(before) is int and type(after) is int:row['bp_delta']=before-after
        with self.locked():
            try:data=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else dict(version=1,operations={})
            except (ValueError,OSError) as exc:raise ScreenError('invalid_outcome_journal') from exc
            if data.get('version')!=1 or not isinstance(data.get('operations'),dict):raise ScreenError('invalid_outcome_journal')
            old=data['operations'].get(ident)
            if old:
                if old['states']!=row['states'] or old['points']!=row['points']:raise ScreenError('outcome_confirmed_state_contradiction')
                return old
            data['operations'][ident]=row;temp=self.path.with_suffix('.tmp')
            with temp.open('w',encoding='utf-8') as stream:
                json.dump(data,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
            os.replace(temp,self.path)
        return row
