"""Current native pixels; centre lookup optimization is not purchase proof."""
import hashlib,json
import pytest
from src.core.bloodweb_detector import find_center
from src.utils.images import read_image


@pytest.mark.parametrize('level',[1,21,49])
def test_confident_ordinary_header_avoids_wreath_and_preserves_fresh_glyph(root,monkeypatch,level):
    meta=json.loads((root/'tests/fixtures/rc1/skull_center.json').read_text())
    path=root/meta['source'];assert hashlib.sha256(path.read_bytes()).hexdigest()==meta['sha256']
    pixels=read_image(path);expected=find_center(pixels,hint=meta['center'],local_only=True)
    def forbidden(*args):pytest.fail('Prestige wreath cannot occur at an ordinary level')
    monkeypatch.setattr('src.core.prestige.native_ready_center',forbidden)
    assert find_center(pixels,hint=meta['center'],local_only=True,observed_level=(level,.99))==expected


@pytest.mark.parametrize('header',[None,(None,0.),(49,.94),(50,.99)])
def test_unknown_or_fifty_retains_original_native_prestige_search(root,monkeypatch,header):
    from src.core.prestige import native_ready_center
    meta=json.loads((root/'references/bloodweb/v56_dark_lord_prestige/manifest.json').read_text())
    path=root/meta['source'];assert hashlib.sha256(path.read_bytes()).hexdigest()==meta['source_sha256']
    pixels=read_image(path);expected=find_center(pixels);calls=[]
    def counted(*args):calls.append(True);return native_ready_center(*args)
    monkeypatch.setattr('src.core.prestige.native_ready_center',counted)
    assert find_center(pixels,observed_level=header)==expected
    assert calls
