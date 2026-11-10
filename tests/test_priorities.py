from src.core.preferences import Preferences
from src.core.path_planner import plan_route
from tests.test_planner import web


def test_priority_rank_precedes_distance():
    result=web()
    assert plan_route(result,['target','other']).next_node==1
    assert plan_route(result,['other','target']).next_node==3
    result.nodes[1].state='absorbed'
    assert plan_route(result,['target','other']).next_node==3


def test_preferences_migrate_preserve_order_and_roles(tmp_path,library):
    import json
    path=tmp_path/'preferences.json'
    old=dict(language='en',role='killer',selected=['survivor_item_003','killer_blight_addon_008','survivor_item_001'])
    path.write_text(json.dumps(old),encoding='utf-8')
    preferences=Preferences(path,library.entries)
    assert preferences.priorities['survivor']==['survivor_item_003','survivor_item_001']
    preferences.priorities['survivor'].reverse(); preferences.save('ru','survivor')
    loaded=Preferences(path,library.entries)
    assert loaded.priorities['survivor']==['survivor_item_001','survivor_item_003']
    assert loaded.priorities['killer']==['killer_blight_addon_008']
    assert json.loads(path.with_name('preferences.v1.backup.json').read_text())==old


def test_killer_context_order_survives_restart(tmp_path,library):
    from dataclasses import replace
    entries=list(library.entries)
    blight=next(e for e in entries if e.character=='blight')
    entries.append(replace(blight,id='nurse_test',character='nurse'))
    common=next(e.id for e in entries if e.role=='killer' and e.character is None)
    path=tmp_path/'preferences.json'
    p=Preferences(path,entries)
    p.priorities['killer'][:]=[blight.id,common]
    p.activate_character('nurse'); p.priorities['killer'][:]=[common,'nurse_test']
    p.save('ru','killer')
    loaded=Preferences(path,entries)
    assert loaded.character=='nurse'
    assert loaded.priorities['killer']==[common,'nurse_test']
    loaded.activate_character('blight')
    assert loaded.priorities['killer']==[blight.id,common]
    loaded.activate_character('nurse')
    assert loaded.priorities['killer']==[common,'nurse_test']
