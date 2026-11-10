"""Planning experiment, isolated from production. Standard library only.

No mouse, capture, process, game-memory or production automation imports.
Entity distributions, preview routes, visibility and times are model assumptions.
"""
import copy,csv,heapq,json,math,random,statistics,time
from dataclasses import dataclass,field
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
COST={'brown':2000,'yellow':2500,'green':3250,'blue':3250,'purple':4000,'red':5000,'orange':2000}

@dataclass(frozen=True)
class Tile:
 id:int
 cost:int
 ring:int=1
 perk:bool=False
 rank:int|None=None
 uncertain_cost:bool=False

@dataclass
class Web:
 tiles:dict[int,Tile]
 edges:list[tuple[int,int]]
 level:int
 owned:set[int]=field(default_factory=lambda:{0})
 absorbed:set[int]=field(default_factory=set)
 purchases:int=0
 active:bool=False
 head:int|None=None
 spent:int=0
 entity_turns:int=0
 interruptions:int=0
 model:str='rare_preference'
 perk_schedule:str='replacement'
 seed:int=0
 budget:int=200000
 trace:list=field(default_factory=list)
 generic_perks:bool=False
 def __post_init__(self):
  self.graph={n:set() for n in self.tiles}
  self.graph[0]=set()
  for a,b in self.edges:
   if a not in self.graph or b not in self.graph:raise ValueError('edge outside map')
   self.graph[a].add(b);self.graph[b].add(a)
 def clone(self):return copy.deepcopy(self)
 def remaining(self):return set(self.tiles)-self.owned-self.absorbed
 def available(self):return {n for n in self.remaining() if self.graph[n]&self.owned}
 def paths(self,mode='cost'):
  distance={n:0 for n in self.owned};paths={n:[] for n in self.owned}
  queue=[(0,n) for n in self.owned];heapq.heapify(queue)
  while queue:
   value,node=heapq.heappop(queue)
   if value!=distance[node]:continue
   for other in sorted(self.graph[node]-self.absorbed):
    cost=0 if other in self.owned else 1 if mode=='fewest' else self.tiles[other].cost
    total=value+cost
    if total<distance.get(other,math.inf):
     distance[other]=total;paths[other]=paths[node]+([] if other in self.owned else [other]);heapq.heappush(queue,(total,other))
  return {n:p for n,p in paths.items() if n in self.remaining()}
 def orphaned(self,removed):
  blocked=self.absorbed|set(removed);seen=set(self.owned);todo=list(seen)
  while todo:
   for n in self.graph[todo.pop()]-blocked-seen:seen.add(n);todo.append(n)
  return self.remaining()-seen
 def priority_value(self,n):return 0 if self.tiles[n].rank is None else 2.0**(-self.tiles[n].rank)
 def eat(self,force_perk=False):
  remaining=self.remaining()
  if not remaining:return []
  if force_perk:candidates={n for n in remaining if self.tiles[n].perk}
  else:
   candidates=self.graph.get(self.head,set())&remaining
   respawn=not candidates
   if respawn:
    ring=max(self.tiles[n].ring for n in remaining)
    candidates={n for n in remaining if self.tiles[n].ring==ring}
   # The first available perk remains accessible according to the BHVR guide.
   if not any(self.tiles[n].perk for n in self.owned if n in self.tiles):
    candidates={n for n in candidates if not self.tiles[n].perk}
  if not candidates:return []
  # Do not consume a bottleneck that would end the whole web via a cascade.
  safe={n for n in candidates if len(self.orphaned({n}))<len(remaining) or len(remaining)==1}
  if not safe:
   candidates={n for n in remaining if len(self.orphaned({n}))<len(remaining) and (force_perk==False or self.tiles[n].perk)}
   if not candidates:return []
   ring=max(self.tiles[n].ring for n in candidates);safe={n for n in candidates if self.tiles[n].ring==ring}
  choices=sorted(safe)
  rng=random.Random(self.seed+self.entity_turns*1009)
  if self.model=='avoid_cascade':
   least=min(len(self.orphaned({n})) for n in choices);choices=[n for n in choices if len(self.orphaned({n}))==least]
  elif self.model=='rare_preference' and self.head is not None and not force_perk and self.graph.get(self.head,set())&remaining:
   highest=max(self.tiles[n].cost for n in choices);choices=[n for n in choices if self.tiles[n].cost==highest]
  node=rng.choice(choices);removed=self.orphaned({node})|{node}
  self.absorbed.update(removed);self.head=node;self.entity_turns+=1
  self.trace.append(dict(event='entity_model',primary=node,cascade=sorted(removed-{node})))
  return sorted(removed)
 def buy(self,node):
  if node not in self.available() or self.spent+self.tiles[node].cost>self.budget:return False
  self.owned.add(node);self.purchases+=1;self.spent+=self.tiles[node].cost
  self.trace.append(dict(event='model_purchase',node=node,cost=self.tiles[node].cost))
  if self.level<10:return True
  perk=self.tiles[node].perk and any(self.tiles[n].perk for n in self.remaining())
  if perk:
   self.active=True;self.eat(force_perk=True)
  elif self.purchases>=5:self.active=True
  if self.active and (not perk or self.perk_schedule=='additional'):self.eat()
  return True
 def chain(self,path):
  acquired=[]
  for node in path:
   if node in self.owned:continue
   if not self.buy(node):self.interruptions+=1;break
   acquired.append(node)
  return acquired

def target_path(web,nearest=True):
 paths=web.paths('fewest' if nearest else 'cost')
 priority=[n for n in paths if web.tiles[n].rank is not None]
 if priority:
  n=min(priority,key=lambda n:(web.tiles[n].rank,len(paths[n]) if nearest else sum(web.tiles[i].cost for i in paths[n]),n))
 else:
  available=web.available()
  if not available:return []
  n=min(available,key=lambda n:(web.tiles[n].cost,n))
 return paths[n]

def static_route(web):
 future=web.clone();future.level=1;future.active=False;future.budget=10**9
 order=[]
 while future.remaining():
  path=target_path(future,nearest=False)
  if not path:break
  for n in path:
   if future.buy(n):order.append(n)
 return order

def forecast_choice(web):
 """One-step robust heuristic; not optimal search or learned probabilities."""
 candidates=sorted(web.available());best=None
 for node in candidates:
  values=[]
  for model,offset in [('uniform',71),('rare_preference',113),('avoid_cascade',157)]:
   future=web.clone();future.model=model;future.seed+=offset;future.trace=[]
   if not future.buy(node):continue
   paths=future.paths('fewest')
   got=sum(future.priority_value(n) for n in future.owned if n in future.tiles)
   potential=sum(future.priority_value(n)*(.72**len(path)) for n,path in paths.items())
   lost=sum(future.priority_value(n) for n in future.absorbed)
   # Priority dominates filler cost; coefficients are experimental, explicit.
   values.append(8*got+4*potential-10*lost-future.spent/250000+.025*len(future.absorbed))
  if values:
   score=(min(values),statistics.mean(values),-web.tiles[node].cost,-node)
   if best is None or score>best[0]:best=(score,node)
 return best[1] if best else None

def center_complete(web):
 """Model of documented perks-first/cheapest-next, not the actual native code."""
 acquired=[]
 while web.remaining():
  paths=web.paths();perks=[n for n in paths if web.tiles[n].perk]
  if perks:
   target=min(perks,key=lambda n:(sum(web.tiles[i].cost for i in paths[n]),n));path=paths[target]
  else:
   available=web.available()
   if not available:break
   path=[min(available,key=lambda n:(web.tiles[n].cost,n))]
  step=web.chain(path);acquired.extend(step)
  if not step:break
 return acquired

def execute(web,strategy,path_policy='cost',ambiguity_rate=0.,micro_seconds=.25):
 queue=[];calls=0;plans=0;preview_calls=0;mapped_full=1;full_fast5=1;fast=True;compact_count=0
 while web.remaining() and calls<100:
  paths=web.paths(path_policy)
  if not paths:break
  center=False;forecast=False
  if strategy=='B':
   queue=[n for n in queue if n in web.remaining()]
   if not queue or queue[0] not in web.available():queue=static_route(web);plans+=1
   path=queue[:1];queue=queue[1:]
  elif strategy in ('A','C'):
   path=target_path(web);plans+=1
   if strategy=='A':path=path[:1]
   elif len(path)==1 and not any(web.tiles[n].rank is not None for n in web.remaining()):
    endpoint=min(paths,key=lambda n:(sum(web.tiles[i].cost for i in paths[n])/max(1,len(paths[n])),-len(paths[n]),n));path=paths[endpoint]
   else:path=paths[path[-1]] if path else []
  elif strategy=='D':
   node=forecast_choice(web);path=[node] if node is not None else [];plans+=1;forecast=True
  elif strategy=='E':
   if web.generic_perks or not any(web.tiles[n].rank is not None for n in web.remaining()):center=True;path=[];plans+=1
   elif not web.active:
    path=target_path(web,nearest=False);plans+=1
    safe=[]
    for n in path:
     if web.purchases+len(safe)+1>=5 or web.tiles[n].perk:break
     safe.append(n)
    if safe:path=safe
    else:
     node=forecast_choice(web);path=[node] if node is not None else [];forecast=True
   else:
    node=forecast_choice(web);path=[node] if node is not None else [];plans+=1;forecast=True
  else:raise ValueError(strategy)
  if not center and not path:break
  previous=len(web.absorbed);calls+=1
  if center:acquired=center_complete(web)
  else:acquired=web.chain(path)
  if len(path)>1:preview_calls+=1
  failure=random.Random(web.seed+calls*10007).random()<ambiguity_rate
  mapped_full+=int(failure) # one observation replaced by full, then validated map
  if fast and (calls>5 or failure or len(web.absorbed)>previous):fast=False
  if fast:compact_count+=3
  else:full_fast5+=3
  if not acquired:break
  if web.interruptions:queue=[]
 priorities={n for n,t in web.tiles.items() if t.rank is not None};got=priorities&web.owned;lost=priorities&web.absorbed
 reads=1+3*calls # ideal two post-state frames + independent pre-input state
 full=reads
 def duration(full_count):
  # Capture/compute anchors from v4.7/v4.9, extra animation assumptions separate.
  return full_count*.47+(reads-full_count)*.062+reads*(.211+.035)+calls*.25+web.purchases*micro_seconds+web.entity_turns*.10+preview_calls*.30+1.5
 return dict(strategy=strategy,priority_total=len(priorities),priority_got=len(got),priority_lost=len(lost),priority_unfinished=len(priorities-got-lost),
  weighted_got=sum(web.priority_value(n) for n in got),bp=web.spent,purchased_nodes=web.purchases,absorbed_nodes=len(web.absorbed),mouse_actions=calls,
  full_baseline=full,full_mapped=mapped_full,full_fast5_model=full_fast5,observations=reads,replans=max(0,plans-1),chain_interruptions=web.interruptions,
  estimated_seconds_full=duration(full),estimated_seconds_mapped=duration(mapped_full),estimated_seconds_fast5=duration(full_fast5),
  completed=not web.remaining(),unreachable=len(web.remaining()),trace=web.trace)

def from_case(case,profile='items',unknown_cost=3500,event_cost=2000):
 nodes=[n for n in case['nodes'] if n['state']!='center'];known=list(dict.fromkeys(n['icon_id'] for n in sorted(nodes,key=lambda n:(-n.get('ring',1),-COST.get(n.get('rarity'),3500),n['id'])) if n.get('icon_id')))
 selected=known[:3] # controlled priority profile, not invented ID ground truth
 ranks={icon:i for i,icon in enumerate(selected)}
 tiles={}
 for n in nodes:
  cost=COST.get(n.get('rarity'),unknown_cost) if n.get('rarity_confidence',0)>=.7 else unknown_cost
  if n.get('rarity')=='orange':cost=event_cost
  rank=(0 if n['type']=='perk' else None) if profile=='perks' else ranks.get(n.get('icon_id')) if profile=='items' else None
  tiles[n['id']]=Tile(n['id'],cost,n.get('ring',1),n['type']=='perk',rank,n.get('rarity_confidence',0)<.7)
 centers=[n['id'] for n in case['nodes'] if n['state']=='center'];assert centers==[0]
 return Web(tiles,[tuple(e) for e in case['edges']],case['level'],generic_perks=profile=='perks')

def summarize(rows):
 output=[]
 for strategy in 'ABCDE':
  subset=[r for r in rows if r['strategy']==strategy]
  output.append(dict(strategy=strategy,runs=len(subset),**{k:statistics.mean(r[k] for r in subset) for k in ('priority_got','priority_total','priority_lost','weighted_got','bp','mouse_actions','full_baseline','full_mapped','full_fast5_model','replans','estimated_seconds_full','estimated_seconds_mapped','chain_interruptions')},completion_rate=statistics.mean(r['completed'] for r in subset),any_priority_loss_rate=statistics.mean(r['priority_lost']>0 for r in subset)))
 return output

def main():
 folder=ROOT/'debug/v50';data=json.loads((folder/'observed_maps.json').read_text(encoding='utf-8'))
 # Two independently captured L49 detector variants are correlated views.
 cases=[c for c in data['cases'] if c['label']!='v49_opening49b_L49']
 rows=[];started=time.perf_counter()
 for case in cases:
  for profile in ('items','perks','none'):
   for model in ('uniform','rare_preference','avoid_cascade'):
    for seed in range(12):
     for strategy in 'ABCDE':
      world=from_case(case,profile);world.model=model;world.seed=seed
      result=execute(world,strategy);result.pop('trace');result.update(case=case['label'],profile=profile,entity_model=model,seed=seed);rows.append(result)
 summaries=summarize(rows)
 sensitivity=[]
 for path_policy,schedule,unknown,event,ambiguity in [('fewest','replacement',3500,2000,0),('cost','additional',3500,2000,0),('cost','replacement',2000,3000,.2),('cost','replacement',5000,3000,.5)]:
  samples=[]
  for case in cases:
   for profile in ('items','perks','none'):
    for seed in range(4):
     for strategy in 'ABCDE':
      world=from_case(case,profile,unknown,event);world.seed=seed;world.perk_schedule=schedule
      result=execute(world,strategy,path_policy,ambiguity);result.pop('trace');samples.append(result)
  sensitivity.append(dict(path_policy=path_policy,perk_schedule=schedule,unknown_cost=unknown,event_cost=event,ambiguity_rate=ambiguity,summary=summarize(samples)))
 out=dict(scope='Counterfactual Monte Carlo on seven detected real map snapshots, NOT live purchases or an identified game Entity policy.',
  scenes=len(cases),independent_game_levels=[c['level'] for c in cases],runs=len(rows),entity_models=['uniform','rare_preference','avoid_cascade'],seeds=12,
  profiles=['three recognized item IDs ordered by ring/cost','all observed perk shapes, no perk IDs','no priorities'],
  assumptions=dict(entity_after_nodes=5,chain_internal_cost_per_node_unchanged=True,preview_path='cheapest path; fewest-node sensitivity',perk_schedule='replacement; additional-turn sensitivity',
   observation='Ideal two final red frames per purchased node; level-final/fast-animation visibility not proven',full_mapped='one initial full plus model ambiguity refresh; Entity states/edges locally updated; actual v4.9 cannot yet sustain this',
   time='Serial scenario model, not calibrated chain/center time: full .47, compact .062, capture .211, diagnostics .035, hold .25, extra per-node animation .25, Entity .10, chain preview .30, transition1.5 seconds'),
  summary=summaries,by_profile={p:summarize([r for r in rows if r['profile']==p]) for p in ('items','perks','none')},sensitivity=sensitivity,seconds=time.perf_counter()-started)
 (folder/'strategy_comparison.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
 with (folder/'strategy_runs.csv').open('w',encoding='utf-8',newline='') as stream:
  writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
 print(json.dumps(dict(seconds=out['seconds'],summary=summaries),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
