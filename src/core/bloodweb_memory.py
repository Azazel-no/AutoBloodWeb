"""One level's map. Identity reuse requires two observations and fresh evidence."""
import copy,hashlib,math,time
from dataclasses import asdict,replace
from src.core.models import RecognitionStatus
from src.core.node_classifier import glyph,node_mask,rarity_evidence,node_glyph

class BloodwebMemory:
    def __init__(self,role,character):
        self.role,self.character=role,character;self.generation=0;self.context=None;self.center=None;self.records=[]
        self.hits=0;self.misses=0;self.full_reason='initial';self.graph=[]
        self.route_plan=None

    def clear(self,reason='invalidated'):
        self.records=[];self.graph=[];self.context=None;self.center=None;self.generation+=1;self.full_reason=reason
        self.route_plan=None

    def begin(self,context,center,geometry):
        changed=self.context is not None and self.context!=context
        old=[r['point'] for r in self.records]
        if self.center and math.dist(self.center,center)>8:changed=True
        if old and geometry:
            matches=sum(any(math.dist(p,(x,y))<8 for x,y,r in geometry) for p in old)
            new_matches=sum(any(math.dist(p,(x,y))<8 for p in old) for x,y,r in geometry)
            if matches<len(old)*.7 or new_matches<len(geometry)*.7:changed=True
        if changed:self.clear('context_or_geometry_changed')
        self.context=context;self.center=center;self.generation=max(1,self.generation)
        self.hits=self.misses=0

    def identity(self,x,y,patch,radius,category=None):
        mask=node_mask(radius,patch.shape[:2]);feature=node_glyph(patch,radius)
        rarity,color=rarity_evidence(patch,mask)
        self.last_rarity=(rarity,color)
        category_key=(category.category,tuple(category.candidates),category.confidence>=.95,category.event_offering) if category else None
        fingerprint=hashlib.sha256(feature.tobytes()+str((rarity,color>=.7,radius,patch.shape,category_key)).encode()).hexdigest() if feature is not None else None
        candidates=[r for r in self.records if math.dist(r['point'],(x,y))<8]
        previous=candidates[0] if len(candidates)==1 else None
        saved=None
        if previous and fingerprint and previous['fingerprint']==fingerprint and previous['observations']>=2 and previous['match'].recognition_status==RecognitionStatus.RECOGNIZED:
            saved=replace(copy.deepcopy(previous['match']),rarity_confidence=color);self.hits+=1
            saved._opening_overlaps=getattr(previous['match'],'_opening_overlaps',None)
        else:self.misses+=1
        return feature,fingerprint,saved,previous

    def update(self,nodes,observations,edges,frame=None):
        records=[];now=time.monotonic();stamp=frame.captured_at if frame else now
        for node in nodes:
            item=observations.get((node.x,node.y))
            if item is None:continue
            fingerprint,match,previous=item
            same=previous and previous['fingerprint']==fingerprint and previous['match'].icon_id==match.icon_id and match.recognition_status==RecognitionStatus.RECOGNIZED
            count=previous['observations']+(stamp>previous['capture_stamp']) if same else 1
            records.append(dict(point=(node.x,node.y),fingerprint=fingerprint,observations=count,match=copy.deepcopy(match),node=asdict(node),
                relative=(node.x-self.center[0],node.y-self.center[1]),physical=frame.to_screen(node.x,node.y) if frame else None,
                neighbors=[b if a==node.id else a for a,b in edges if node.id in (a,b)],last_confirmed_at=now if count>=2 and node.icon_id else None,capture_stamp=stamp))
        self.records=records;self.graph=list(edges)

    def summary(self):
        return dict(generation=self.generation,role=self.role,character=self.character if self.role=='killer' else None,center=self.center,
            route_plan=copy.deepcopy(self.route_plan),
            context=self.context,graph=self.graph,identity_hits=self.hits,identity_misses=self.misses,full_reason=self.full_reason,
            nodes=[dict(local_id=r['node']['id'],**{k:v for k,v in r.items() if k not in ('match','node')}) for r in self.records])
