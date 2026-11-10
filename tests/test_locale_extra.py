"""Added languages: complete killer names, matching placeholders, English fallback."""
import re

from src.core.localization import STRINGS,Localization

ADDED=('es','de','fr')


def test_every_killer_has_a_name_in_every_added_language():
    names=[k for k in STRINGS['en'] if k.startswith('character_')]
    for code in ADDED:
        assert not [k for k in names if not STRINGS[code].get(k)],code


def test_translated_strings_keep_the_placeholders():
    fields=lambda text:set(re.findall(r'\{(\w+)',text))
    for code in ADDED:
        for key,value in STRINGS[code].items():
            assert fields(value)==fields(STRINGS['en'][key]),(code,key)


def test_missing_keys_fall_back_to_english():
    assert Localization('de').text('runtime_limit')==STRINGS['en']['runtime_limit']
    assert Localization('fr').text('character_nightmare')=='Le Cauchemar'
