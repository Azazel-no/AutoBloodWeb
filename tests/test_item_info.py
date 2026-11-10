import json
import os
from urllib.error import HTTPError

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QPoint
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication

from src.core.item_info import CATALOG,load_item_info
from src.core.item_wiki import parse_items,sync_items
from src.gui.info_card import InfoCard
from src.gui.priority_list import PriorityList
from src.gui.widgets import IconTile

PAGE='https://deadbydaylight.wiki.gg/wiki/Add-ons'
URL='https://deadbydaylight.wiki.gg/images/IconAddon_a.png'
ROW=('<table><tr><th><div class="game-element-bg-settings very-rare-addon-element"></div><img src="/images/IconAddon_a.png?1"></th>'
     '<th><a>Lens</a></th><td><span class="tooltip"><span class="tooltiptext">hidden hover</span></span>'
     '<p>Boosts <b>+10&#160;%</b> range.</p><ul><li>Lasts 5 s.</li></ul><i><span class="luaClr clr">"A quote."</span></i></td></tr></table>')
RECORD=dict(name='Lens',rarity='Rare',description='Text',flavor='',page=PAGE)


def app():
    return QApplication.instance() or QApplication([])


def write(root,catalog=True):
    (root/'assets').mkdir()
    if catalog:(root/CATALOG).write_text(json.dumps(dict(records={URL:RECORD})),encoding='utf-8')
    (root/'assets/wiki_manifest.json').write_text(json.dumps(dict(records=[dict(wiki_id='wiki_a',url=URL)])),encoding='utf-8')
    (root/'assets/wiki_active.json').write_text(json.dumps(dict(
        additional_references=dict(old_a=[dict(url=URL)]),
        legacy_mapping_suggestions=[dict(old_id='old_b',url=URL,status='manual_review_required')])),encoding='utf-8')


def test_row_gives_rarity_text_and_flavor_without_hover_text():
    record=parse_items(ROW,PAGE)[URL]
    assert record['name']=='Lens' and record['rarity']=='Very Rare' and record['flavor']=='"A quote."'
    assert record['description']=='Boosts +10 % range.\n• Lasts 5 s.'


def test_only_confirmed_links_get_a_description(tmp_path):
    write(tmp_path)
    assert set(load_item_info(tmp_path))=={'wiki_a','old_a'}


def test_missing_catalog_is_empty(tmp_path):
    write(tmp_path,catalog=False)
    assert load_item_info(tmp_path)=={}


def test_denied_sync_keeps_the_old_catalog(tmp_path):
    write(tmp_path);before=(tmp_path/CATALOG).read_bytes()
    def denied(url,limit):raise HTTPError(url,403,'denied',{},None)
    report=sync_items(tmp_path,fetch=denied,pause=lambda s:None)
    assert report['access_denied'] and not report['complete'] and (tmp_path/CATALOG).read_bytes()==before


def test_sync_writes_attribution(tmp_path):
    write(tmp_path)
    report=sync_items(tmp_path,fetch=lambda url,limit:(ROW.encode(),'text/html',url),pause=lambda s:None)
    data=json.loads((tmp_path/CATALOG).read_text(encoding='utf-8'))
    assert report['complete'] and 'CC BY-SA' in data['license'] and URL in data['records']


def test_right_click_requests_details_without_toggling():
    app();tile=IconTile();tile.setCheckable(True);seen=[]
    tile.details_requested.connect(lambda:seen.append(1))
    tile.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Mouse,QPoint(5,5)))
    assert seen==[1] and not tile.isChecked()


def test_priority_right_click_outside_an_item_is_silent():
    app();box=PriorityList();seen=[];box.details_requested.connect(seen.append)
    box.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Mouse,QPoint(5,5)))
    assert seen==[]


def test_card_hides_empty_parts():
    app();card=InfoCard()
    card.show_item('missing.png','Lens','','All killers','Text','','')
    assert card.rarity.isHidden() and card.flavor.isHidden() and card.source.isHidden() and card.description.text()=='Text'
    card.show_item('missing.png','Lens','Rare','All killers','Text','"Q"','Source')
    assert not card.rarity.isHidden() and not card.source.isHidden()
    card.close()


def test_any_named_killer_can_have_a_live_session(tmp_path):
    from src.core.series_budget import SeriesBudget
    budget=SeriesBudget(tmp_path/'ledger.json',dict(role='killer',character='nightmare',max_bp=None,max_nodes=10,allow_prestige=False,allow_chain=False))
    assert budget.max_nodes==10
