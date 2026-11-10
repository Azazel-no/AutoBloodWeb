"""Durable BP ceiling for a newly authorized native optimization series."""
import hashlib,json,os,time
from pathlib import Path
from src.core.screen_manager import ScreenError


class SeriesBudget:
    def __init__(self,path,authorization):
        self.path=Path(path);self.authorization=authorization
        authorized_character=(authorization.get('role')=='killer' and type(authorization.get('character')) is str and bool(authorization.get('character')) or
            # Survivor runs carry the killer picker's saved value, not the survivor on screen
            # (live 7.11: Cheryl Mason refused as 'singularity'); the role is what is authorised.
            authorization.get('role')=='survivor' and type(authorization.get('character')) is str and bool(authorization.get('character')))
        if (not authorized_character or
                (authorization.get('max_bp') is not None and (type(authorization.get('max_bp')) is not int or authorization.get('max_bp')<=0)) or
                type(authorization.get('allow_prestige')) is not bool or
                type(authorization.get('allow_chain')) is not bool):raise ValueError('series_scope_invalid')
        for name,default in (('max_commands',4000),('max_nodes',100000),('max_prestiges',1 if authorization['allow_prestige'] else 0)):
            value=authorization.get(name,default)
            if type(value) is not int or value<(0 if name=='max_prestiges' else 1):raise ValueError('series_scope_invalid')
            setattr(self,name,value)
        self.sha=hashlib.sha256(json.dumps(authorization,sort_keys=True).encode()).hexdigest()
        if self.path.exists():
            try:self.data=json.loads(self.path.read_text(encoding='utf-8'))
            except (ValueError,OSError) as exc:raise ScreenError('invalid_chain_ledger') from exc
            if self.data.get('authorization_sha256')!=self.sha:raise ScreenError('chain_ledger_scope_mismatch')
            self.validate()
            if self.data.get('violation'):raise ScreenError(self.data['violation'])
        else:self.data=dict(authorization_sha256=self.sha,initial_bp=None,last_bp=None,spent=0,commands=0,reservations=[])

    def validate(self):
        d=self.data
        if (any(type(d.get(k)) is not int or d[k]<0 for k in ('spent','commands')) or
                any(d.get(k) is not None and (type(d[k]) is not int or d[k]<0) for k in ('initial_bp','last_bp')) or
                not isinstance(d.get('reservations'),list)):
            raise ScreenError('invalid_chain_ledger')
        for row in d['reservations']:
            if (not isinstance(row,dict) or
                    row.get('upper_bound') is not None and (type(row['upper_bound']) is not int or row['upper_bound']<=0 or
                        type(row.get('bp_before')) is not int or row['bp_before']<row['upper_bound']) or
                    type(row.get('settled')) is not bool or not isinstance(row.get('points'),list) or
                    not row['points'] or not isinstance(row.get('kind'),str)):
                raise ScreenError('invalid_chain_ledger')
        if self.over_bp() or d['commands']>self.max_commands:
            if not d.get('violation'):raise ScreenError('invalid_chain_ledger')

    def over_bp(self):
        limit=self.authorization.get('max_bp')
        return limit is not None and self.data['spent']>limit

    def fail(self,code):
        self.data['violation']=code;self.save();raise ScreenError(code)

    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True);temp=self.path.with_suffix('.tmp')
        with temp.open('w',encoding='utf-8') as stream:
            json.dump(self.data,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
        for attempt in range(7):
            try:os.replace(temp,self.path);return
            except PermissionError:
                # A Windows observer can briefly hold the destination without
                # FILE_SHARE_DELETE. No input is allowed before this commit.
                if attempt==6:raise
                time.sleep(.025)

    def observe(self,bp):
        if type(bp) is not int or bp<0:raise ScreenError('chain_bp_unproven')
        d=self.data
        if d['initial_bp'] is None:d['initial_bp']=bp
        if d['last_bp'] is not None and bp<d['last_bp']:d['spent']+=d['last_bp']-bp
        d['last_bp']=bp
        if d.get('violation'):raise ScreenError(d['violation'])
        if self.over_bp():self.fail('chain_bp_exceeded')
        self.save()

    def reserve(self,bp,upper_bound,kind,points,operation_id=None):
        if bp is not None:self.observe(bp)
        d=self.data;limit=self.authorization.get('max_bp')
        if limit is not None and upper_bound is None:
            # Costs are not tracked: stop once the observed spend reaches the cap.
            if d['spent']>=limit:raise ScreenError('chain_budget_limit')
            limit=None
        if limit is not None:
            if bp is None:raise ScreenError('chain_bp_unproven')
            if type(upper_bound) is not int or upper_bound<=0:raise ScreenError('chain_price_unproven')
        nodes=0 if kind=='prestige' else len(points)
        charged=sum(0 if r['kind']=='prestige' else len(r['points']) for r in d['reservations'])
        if charged+nodes>self.max_nodes:raise ScreenError('action_limit')
        if kind=='prestige' and (not self.authorization['allow_prestige'] or sum(r['kind']=='prestige' for r in d['reservations'])>=self.max_prestiges):
            raise ScreenError('prestige_manual')
        if limit is not None:
            # Node purchases without a tracked cost reserve nothing; known prices still do.
            outstanding=sum(r['upper_bound'] for r in d['reservations'] if not r['settled'] and r['upper_bound'] is not None)
            if d['spent']+outstanding+upper_bound>limit or bp<upper_bound:raise ScreenError('chain_budget_limit')
        reservation=dict(upper_bound=upper_bound,kind=kind,points=[list(p) for p in points],bp_before=bp,settled=False,at=time.time())
        if operation_id is not None:reservation['operation_id']=operation_id
        d['reservations'].append(reservation);self.save();return len(d['reservations'])-1

    def command(self):
        if self.data['commands']>=self.max_commands:self.fail('action_limit')
        self.data['commands']+=1
        if self.data.get('violation'):raise ScreenError(self.data['violation'])
        self.save()

    def settle(self,index,bp):
        if type(index) is not int or not 0<=index<len(self.data['reservations']):raise ScreenError('invalid_chain_ledger')
        self.observe(bp);item=self.data['reservations'][index]
        if item['settled']:raise ScreenError('invalid_chain_ledger')
        delta=max(0,(item['bp_before'] if item['bp_before'] is not None else bp)-bp)
        if item['upper_bound'] is not None and delta>item['upper_bound']:self.fail('chain_bp_exceeded')
        item.update(settled=True,observed_delta=delta);self.save()
