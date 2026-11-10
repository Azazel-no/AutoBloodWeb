import base64,json,threading
from pathlib import Path
from PIL import Image
import io
from src.core.wiki_assets import LayeredWikiImages,css_layers,compose_icon,portrait_character
from src.core.process_access import compare_integrity
from src.core.hotkey_diagnostics import compare_game_focus


def image_bytes(color,size=(32,32)):
    output=io.BytesIO(); Image.new('RGBA',size,color).save(output,format='PNG'); return output.getvalue()


def test_strict_portrait_identity_rejects_power_and_other_character():
    parser=LayeredWikiImages('https://deadbydaylight.wiki.gg/wiki/Sally_Smithson','killer','addons','nurse')
    parser.feed('<img src="/images/IconHelpLoading_nurse.png"><img src="/images/K17_TheDemogorgon_Portrait.png"><img src="/images/K04_TheNurse_Portrait.png">')
    assert len(parser.images)==1 and parser.images[0]['filename']=='K04_TheNurse_Portrait.png'
    assert portrait_character('IconHelpLoading_nurse.png') is None


def test_table_background_wins_over_description_inline_and_tooltip():
    parser=LayeredWikiImages('https://deadbydaylight.wiki.gg/wiki/The_Trapper','killer','addons','trapper')
    parser.feed('<h2>Add-ons for Bear Trap</h2><span class="game-element-container"><span class="common-item-element"></span><img src="/images/IconAddon_example.png"></span>'
        '<div class="game-element-container"><div class="rare-item-element"></div><div class="addon-marker"></div><img src="/images/IconAddon_example.png"></div>')
    assert len(parser.images)==1
    assert parser.images[0]['background_class']=='rare-item-element' and parser.images[0]['addon_marker']


def test_css_comments_do_not_replace_current_textures_and_alpha_is_preserved():
    current=image_bytes((15,40,90,255)); old=image_bytes((80,60,15,255))
    css=':root {--rare-item-bg:url(data:image/png;base64,'+base64.b64encode(current).decode()+');/* --rare-item-bg:url(data:image/png;base64,'+base64.b64encode(old).decode()+'); */}'
    assert css_layers(css)['rare-item-bg']==current
    glyph=image_bytes((255,255,255,0))
    result=compose_icon(glyph,current,size=64)
    assert result.mode=='RGBA' and result.getpixel((32,32))==(15,40,90,255)


def test_windows_integrity_guard_is_independent_of_shortcut_registration():
    assert not compare_integrity(dict(rid=8192),dict(rid=12288))['allowed']
    assert compare_integrity(dict(rid=12288),dict(rid=12288))['allowed']
    assert compare_integrity({},dict(rid=8192))['reason']=='input_access_unverified'


def test_focus_credit_requires_exact_or_owned_game_window():
    expected=dict(hwnd=10,pid=50)
    assert compare_game_focus(dict(hwnd=10,pid=50),expected)[0]
    assert compare_game_focus(dict(hwnd=11,pid=50,root_owner=10),expected)[0]
    assert not compare_game_focus(dict(hwnd=11,pid=50,root_owner=11),expected)[0]
    assert not compare_game_focus(dict(hwnd=11,pid=60,root_owner=10),expected)[0]


def test_color_display_keeps_legacy_assets_and_ids(library,root):
    legacy=json.loads((root/'assets/manifest.json').read_text())['icons']
    assert len(legacy)==165
    for record in legacy:
        entry=next(e for e in library.entries if e.id==record['id'])
        assert entry.image==record['image'] and entry.source==record['source']
    if not getattr(library,'display_images',{}): return
    image=library.display_images['killer_blight_addon_001']
    assert 'original_color' in image and Image.open(root/image).mode=='RGBA'


def test_calibration_keeps_positive_evidence_through_missing_detection_but_never_counts_missing():
    import copy
    from tests.test_automation import Device,web,engine
    from src.core.models import NodeState
    missing=web(); missing.nodes=missing.nodes[:1]
    device=Device([web(),web(),web(NodeState.PURCHASED),missing,missing,web(NodeState.PURCHASED)])
    controller=engine(device,dry_run=False); controller.run(calibrate=True)
    assert controller.receipt['verified'] and len(device.presses)==1
    device=Device([web(),web(),web(NodeState.PURCHASED),missing])
    controller=engine(device,dry_run=False); controller.run(calibrate=True)
    assert controller.receipt is None and len(device.presses)==1
    device=Device([web(),web(),web(NodeState.PURCHASED),web(NodeState.ABSORBED)])
    controller=engine(device,dry_run=False); controller.run(calibrate=True)
    assert controller.receipt is None and len(device.presses)==1


def test_read_only_recheck_requires_fresh_same_item_and_scene_anchors():
    import copy
    from dataclasses import asdict
    from types import SimpleNamespace
    from tests.test_automation import Device,web
    from src.core.models import Node,NodeState,NodeType
    from src.core.calibration_recheck import verify_previous
    from src.core.action_report import ActionReport
    before=web()
    for i,(x,y) in enumerate(((80,80),(80,320),(320,80)),2):
        before.nodes.append(Node(i,x,y,20,NodeState.AVAILABLE,NodeType.ITEM,f'anchor_{i}',state_confidence=.95))
    after=copy.deepcopy(before); after.nodes[1].state=NodeState.PURCHASED
    device=Device([after,after]); device.backend=SimpleNamespace()
    frame=device.capture(); device.index=0
    context=dict(frame=frame.metadata(),target={'id':1,'icon':'target'},point=[-170,200],mode='click',duration=.05,
        nodes=[dict(id=n.id,x=n.x,y=n.y,icon=n.icon_id) for n in before.nodes],before='saved_png_hash',report='test_attempt.json')
    receipt,_,_=verify_previous(context,device,device,'click',.65,threading.Event(),ActionReport(),timeout=1)
    assert receipt['verified'] and receipt['mouse_actions']==0 and not device.presses
    device.window=__import__('dataclasses').replace(device.window,dpi=96)
    import pytest
    from src.core.screen_manager import ScreenError
    with pytest.raises(ScreenError,match='window_changed'):
        verify_previous(context,device,device,'click',.65,threading.Event(),ActionReport(),timeout=1)
    device.window=frame.window; device.results[0].nodes[1].icon_id='other'; device.results[1].nodes[1].icon_id='other'
    with pytest.raises(ScreenError,match='purchase_not_confirmed'):
        verify_previous(context,device,device,'click',.65,threading.Event(),ActionReport(),timeout=.4)
    assert not device.presses
