"""Checks the research world's mechanics and separation from production input."""
import ast,copy,json
from pathlib import Path
import pytest
from scripts.bloodweb_strategy_v50 import Tile,Web,execute,from_case

def line(level=1):
 return Web({i:Tile(i,2000,i) for i in (1,2,3)},[(0,1),(1,2),(2,3)],level)

def test_chain_matches_single_node_cost_without_entity():
 chain=line();singles=line()
 assert chain.chain([1,2,3])==[1,2,3]
 assert all(singles.buy(i) for i in (1,2,3))
 assert chain.spent==singles.spent==6000 and chain.owned==singles.owned
 assert chain.entity_turns==0

def test_entity_counts_internal_nodes_not_mouse_calls():
 web=Web({i:Tile(i,2000,1) for i in range(1,8)},[(0,i) for i in range(1,8)],10)
 web.chain([1,2,3,4]);assert not web.active and web.entity_turns==0
 assert web.buy(5) and web.active and web.entity_turns==1
 assert web.purchases==5 and len(web.absorbed)==1

def test_perk_starts_entity_before_five_nodes():
 web=Web({1:Tile(1,4000,1,True),2:Tile(2,3250,2,True),3:Tile(3,2000)},[(0,1),(0,2),(0,3)],40)
 assert web.buy(1) and web.active and web.purchases==1
 assert 2 in web.absorbed and 3 not in web.absorbed

def test_single_perk_does_not_create_early_consumption():
 web=Web({1:Tile(1,4000,1,True),2:Tile(2,2000)},[(0,1),(0,2)],11)
 assert web.buy(1) and not web.active and not web.absorbed

def test_cascade_requires_all_alternative_paths_blocked():
 web=Web({i:Tile(i,2000) for i in (1,2,3,4)},[(0,1),(1,2),(0,3),(3,2),(2,4)],40)
 assert web.orphaned({1})=={1}
 assert web.orphaned({1,3})=={1,2,3,4}

def test_chain_can_be_interrupted_without_crediting_its_endpoint():
 web=Web({i:Tile(i,2000,i) for i in (1,2,3,4)},[(0,1),(1,2),(2,3),(0,4)],40,
     absorbed={3},active=True,head=3,purchases=5)
 assert web.chain([1,2])==[1]
 assert web.interruptions==1 and 2 in web.absorbed and 2 not in web.owned
 assert web.spent==2000

def test_partial_bp_chain_only_charges_successful_nodes():
 web=line();web.budget=4500
 assert web.chain([1,2,3])==[1,2]
 assert web.spent==4000 and 3 not in web.owned and web.interruptions==1

def test_generic_perk_intent_is_explicit_before_center_delegation():
    nodes={1:Tile(1,4000,1,True,0),2:Tile(2,3250,2,True,0),3:Tile(3,2000)}
    generic=Web(nodes,[(0,1),(0,2),(0,3)],40,generic_perks=True)
    explicit=generic.clone();explicit.generic_perks=False
    result=execute(generic,'E');other=execute(explicit,'E')
    assert result['completed'] and result['mouse_actions']==1
    assert result['priority_got']==1 and result['priority_lost']==1
    assert other['mouse_actions']>1  # similar tiles do not imply broad consent

@pytest.mark.parametrize('strategy',list('ABCDE'))
def test_strategy_accounting_and_termination(strategy):
 web=line();web.tiles[3]=Tile(3,2000,3,False,0)
 result=execute(web,strategy)
 assert result['completed'] and result['priority_got']==1 and result['priority_lost']==0
 assert result['bp']==6000 and result['purchased_nodes']==3
 assert result['mouse_actions']>0 and result['full_mapped']==1
 assert result['full_baseline']==result['observations']
 assert result['estimated_seconds_mapped']<=result['estimated_seconds_full']

def test_research_module_cannot_import_native_or_production_controller(root):
 tree=ast.parse((root/'scripts/bloodweb_strategy_v50.py').read_text(encoding='utf-8'))
 imports=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
 imports.extend(alias.name for n in ast.walk(tree) if isinstance(n,ast.Import) for alias in n.names)
 assert all(m.split('.')[0] in {'copy','csv','heapq','json','math','random','statistics','time','dataclasses','pathlib'} for m in imports)

def test_five_strategies_share_the_same_initial_observed_map(root):
 cases=json.loads((root/'debug/v50/observed_maps.json').read_text(encoding='utf-8'))['cases']
 source=copy.deepcopy(cases[0]);worlds=[from_case(source) for _ in 'ABCDE']
 assert all(w.tiles==worlds[0].tiles and w.edges==worlds[0].edges for w in worlds)
 worlds[0].buy(min(worlds[0].available()))
 assert all(w.owned=={0} and w.spent==0 for w in worlds[1:])
 assert source==cases[0]
