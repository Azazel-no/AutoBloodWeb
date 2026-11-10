import json
from urllib.error import HTTPError
import cv2,numpy as np,pytest
from src.core.wiki_library import WikiImages,original_url,import_original,sync_wiki


def test_original_file_urls_and_addon_scope():
    page='https://deadbydaylight.wiki.gg/wiki/Sally_Smithson'
    url='/images/thumb/a/a1/IconAddon_test.png/96px-IconAddon_test.png'
    assert original_url(url,page)=='https://deadbydaylight.wiki.gg/images/a/a1/IconAddon_test.png'
    assert original_url('https://untrusted.example/image.png',page) is None
    parser=WikiImages(page,'killer','addons','nurse')
    parser.feed('<h2>Add-ons for Spencer\'s Last Breath</h2><img src="'+url+'"><img src="/images/FulliconAddon_test.png"><img src="/images/T_UI_iconAddon_new.png"><h2>Cosmetics</h2><img src="/images/b/b1/IconAddon_other.png">')
    assert len(parser.images)==2 and parser.images[0]['character']=='nurse'
    parser=WikiImages(page,'survivor','addons')
    parser.feed('<h2>Item Add-ons</h2><h3>Flashlights</h3><img src="'+url+'"><h2>Killer Add-ons</h2><img src="/images/b/b1/IconAddon_other.png">')
    assert len(parser.images)==1


def test_offer_role_is_never_inferred_from_an_unknown_heading():
    parser=WikiImages('https://deadbydaylight.wiki.gg/wiki/Offerings','survivor','offerings')
    parser.feed('<h2>List of Offerings</h2><h3>Killer Offerings</h3><img src="/images/a/a1/IconOffering_1.png"><h3>Survivor Offerings</h3><img src="/images/a/a1/IconOffering_2.png"><h3>Other</h3><img src="/images/a/a1/IconOffering_3.png">')
    assert len(parser.images)==1 and 'IconOffering_2' in parser.images[0]['url']


def test_original_alpha_aspect_and_no_duplicate_character_folder(tmp_path):
    folder=tmp_path/'assets/killers/characters/nurse'; folder.mkdir(parents=True)
    # Generic test pixels, not replacement game assets.
    image=np.full((64,32,4),200,np.uint8); image[:,:,3]=127
    body=cv2.imencode('.png',image)[1].tobytes()
    record=dict(url='https://deadbydaylight.wiki.gg/images/a/a1/test.png',page='test',filename='test.png',
                role='killer',category='addons',character='nurse')
    item=import_original(tmp_path,record,body)
    assert (tmp_path/item['original']).read_bytes()==body
    derived=cv2.imdecode(np.fromfile(tmp_path/item['normalized'],np.uint8),cv2.IMREAD_UNCHANGED)
    assert derived.shape==(96,96,4) and np.count_nonzero(derived[:,:,3])==96*48
    assert item['review_status']=='pending' and item['old_id'] is None
    assert import_original(tmp_path,record,body)['original']==item['original']
    assert len(list(folder.rglob('wiki_originals/*.png')))==1
    with pytest.raises(ValueError,match='unknown_character_folder'):
        import_original(tmp_path,dict(record,character='Sally_Smithson'),body)


def test_http_denial_stops_all_downloads_and_preserves_existing_images(tmp_path):
    (tmp_path/'assets/killers/characters/nurse/addons').mkdir(parents=True)
    (tmp_path/'data').mkdir(); existing=tmp_path/'assets/killers/characters/nurse/addons/user.png'
    existing.write_bytes(b'untouched user file'); calls=[]
    def fetch(url):
        calls.append(url); raise HTTPError(url,403,'Forbidden',{},None)
    report=sync_wiki(tmp_path,fetch=fetch,pause=lambda seconds:None)
    assert len(calls)==1 and report['access_denied'] and report['added']==0
    assert existing.read_bytes()==b'untouched user file'
    assert len(list((tmp_path/'assets/killers/characters').iterdir()))==1
    assert json.loads((tmp_path/'assets/wiki_manifest.json').read_text())['attempts'][0]['status']==403


def test_unreachable_host_does_not_attempt_each_character(tmp_path):
    (tmp_path/'assets/killers/characters/nurse').mkdir(parents=True); (tmp_path/'data').mkdir()
    calls=[]
    def fetch(url): calls.append(url); raise TimeoutError('network_timeout')
    report=sync_wiki(tmp_path,fetch=fetch,pause=lambda seconds:None)
    assert len(calls)==1 and report['errors'][0]['error']=='network_timeout'
    assert not report['complete'] and not report['records']


def test_only_complete_intact_character_section_is_published(tmp_path):
    from src.core.icon_library import IconLibrary
    from src.core.wiki_activation import verify_wiki
    (tmp_path/'assets/killers/characters/nurse').mkdir(parents=True)
    records=[]
    for index in range(2):
        raw=np.full((32,48,4),100+index,np.uint8)
        record=dict(url=f'https://deadbydaylight.wiki.gg/images/IconAddon_test{index}.png',
            filename=f'IconAddon_test{index}.png',page='https://deadbydaylight.wiki.gg/wiki/Sally_Smithson',
            role='killer',category='addons',character='nurse')
        records.append(import_original(tmp_path,record,cv2.imencode('.png',raw)[1].tobytes()))
    manifest=dict(records=records,page_reports=[dict(character='nurse',status='files_verified',
        addon_urls=[r['url'] for r in records],effective=records[0]['page'])])
    (tmp_path/'assets/wiki_manifest.json').write_text(json.dumps(manifest))
    library=IconLibrary(tmp_path)
    report=verify_wiki(library)
    assert report['character_status']['nurse']['addons']==2 and len(report['entries'])==2
    library.load_wiki()
    assert len(library.filter('killer','addons','nurse'))==2
    assert not library.filter('killer','addons','blight')
    # A broken original invalidates the entire page section, not just one card.
    (tmp_path/records[0]['original']).write_bytes(b'corrupt')
    report=verify_wiki(library)
    assert len(report['errors'])==1 and not report['entries'] and not report['character_status']
