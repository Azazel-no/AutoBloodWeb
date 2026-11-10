"""Recommended priorities and the popup placement."""
from PySide6.QtCore import QRect,QSize

from src.core.preferences import Preferences
from src.gui.info_card import place


class E:
    def __init__(self,i,c):self.id=i;self.role='killer';self.character=c


def prefs(tmp_path,data=None):
    import json
    p=tmp_path/'p.json'
    if data:p.write_text(json.dumps(data),encoding='utf-8')
    return Preferences(p,[E('a1','nurse'),E('a2','nurse'),E('b1','hag')])


def test_empty_lists_fill_once_and_user_lists_stay(tmp_path):
    x=prefs(tmp_path,dict(killer_priorities=dict(hag=['b1']),character='nurse'))
    x.fill_recommended(dict(nurse=['any_perk','a2','a1'],hag=['any_perk','b1']))
    assert x.killer_priorities['nurse']==['any_perk','a2','a1'] and x.priorities['killer'] is x.killer_priorities['nurse']
    assert x.killer_priorities['hag']==['b1']
    x.priorities['killer'].clear();x.fill_recommended(dict(nurse=['any_perk','a2']))
    assert x.priorities['killer']==[]  # A list the user cleared stays cleared.


def test_tier_groups_come_first_then_page_order():
    from scripts.build_otz_tierlist import TIERS,norm
    assert TIERS[0]=='strongest' and TIERS[-1]=='detrimental' and norm('Judgement')=='judgment'


def test_card_opens_under_the_icon_and_centred():
    icon=QRect(500,300,52,52);bounds=QRect(0,0,1920,1080);size=QSize(340,200);at=place(icon,size,bounds)
    assert at.y()==icon.bottom()+7 and at.x()+170==icon.center().x()


def test_card_flips_above_and_stays_inside():
    bounds=QRect(1280,130,640,900);icon=QRect(1860,950,52,52);size=QSize(340,200);at=place(icon,size,bounds)
    assert at.y()+200<=icon.top() and at.x()+340-1<=bounds.right() and at.x()>=bounds.left()
