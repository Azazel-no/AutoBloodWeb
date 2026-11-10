"""Fresh physical observations separate purchase evidence from input receipt."""
import math
from src.core.models import NodeState,ScreenState
from src.core.geometry import purchasable


def physical_node(frame,result,point,radius):
    candidates=[n for n in result.nodes if n.state!=NodeState.CENTER and
                math.dist(frame.to_screen(n.x,n.y),point)<max(8,radius*.4)]
    return candidates[0] if len(candidates)==1 else None


def unchanged_scene(before,after):
    af,ar=before;bf,br=after
    if af.window.signature!=bf.window.signature or ar.screen_state!=br.screen_state or len(ar.nodes)!=len(br.nodes):return False
    mapping={}
    for a in ar.nodes:
        point=af.to_screen(a.x,a.y)
        matches=[b for b in br.nodes if math.dist(point,bf.to_screen(b.x,b.y))<max(5,a.radius*.2)]
        if len(matches)!=1:return False
        b=matches[0]
        if a.state!=b.state or (a.icon_id and b.icon_id and a.icon_id!=b.icon_id):return False
        mapping[a.id]=b.id
    if len(set(mapping.values()))!=len(mapping):return False
    return {tuple(sorted((mapping[a],mapping[b]))) for a,b in ar.edges}=={tuple(sorted(e)) for e in br.edges}


class PurchaseEvidence:
    def __init__(self,frame,result,node,started):
        self.before=(frame,result);self.node=node;self.point=frame.to_screen(node.x,node.y)
        self.started=started;self.red_count=0;self.absorbed_count=0;self.available_count=0
        self.available_since=None;self.last=None;self.uncertain=False;self.saw_red=False;self.saw_entity=False
        self.last_reason='purchase_result_unknown'
        self.last_stamp=frame.captured_at
        self.red_observations=[]

    def observe(self,frame,result,now):
        found=physical_node(frame,result,self.point,self.node.radius)
        if (not frame.live or frame.probe_only or frame.captured_at<=self.last_stamp
                or frame.window.signature!=self.before[0].window.signature):
            return found,False,False
        self.last_stamp=frame.captured_at
        if type(self.before[1].level) is int and result.level!=self.before[1].level:
            # A missing header is absence of context evidence. Keep reading;
            # never send these red pixels to a journal for another/None level.
            self.uncertain=True;self.last_reason='purchase_context_unconfirmed'
            return found,False,False
        if result.screen_state!=ScreenState.BLOODWEB:
            self.uncertain=True;self.last_reason='purchase_screen_unstable';return found,False,False
        if found is None:
            self.uncertain=True;self.available_count=0;self.last_reason='purchase_target_missing';return None,False,False
        same_id=not(self.node.icon_id and found.icon_id and self.node.icon_id!=found.icon_id)
        if found.state==NodeState.PURCHASED and found.state_confidence>=.8 and same_id:
            self.saw_red=True;self.red_count+=1;self.available_count=0
            self.red_observations=(self.red_observations+[(frame,result)])[-2:]
            return found,self.red_count>=2,False
        self.red_count=0
        self.red_observations=[]
        if found.state==NodeState.ABSORBED:
            self.saw_entity=True;self.absorbed_count+=1;self.last_reason='purchase_target_absorbed'
        else:self.absorbed_count=0
        if any(n.state==NodeState.ABSORBED for n in result.nodes)!=any(n.state==NodeState.ABSORBED for n in self.before[1].nodes):
            self.saw_entity=True
        changed=not unchanged_scene(self.before,(frame,result))
        if changed:self.uncertain=True
        unchanged_balance=(self.before[0].bloodpoints is None or frame.bloodpoints is None or
                           self.before[0].bloodpoints==frame.bloodpoints)
        available=(found.state==NodeState.AVAILABLE and found.state_confidence>=.88 and found.geometry_confidence>=.75 and
                   self.node.icon_id is not None and found.icon_id==self.node.icon_id and unchanged_balance and
                   purchasable(found,result.nodes,result.edges) and not changed and not self.saw_entity and not self.saw_red and not self.uncertain)
        if available:
            stable=self.last and unchanged_scene(self.last,(frame,result))
            self.available_count=self.available_count+1 if stable else 1
            if not stable:self.available_since=now
            self.last=(frame,result)
            missed=(self.available_count>=3 and now-self.started>=1.5 and now-self.available_since>=.6)
            if missed:self.last_reason='purchase_missed'
            return found,False,missed
        self.available_count=0;self.available_since=None;self.last=None
        if self.last_reason=='purchase_result_unknown' and found.state==NodeState.AVAILABLE:self.last_reason='purchase_ambiguous'
        return found,False,False
