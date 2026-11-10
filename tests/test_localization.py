from src.core.localization import STRINGS, Localization


def test_all_ui_strings_available_in_both_languages():
    assert STRINGS['ru'].keys()==STRINGS['en'].keys()
    for language in ('ru','en'):
        assert Localization(language).diagnostic('uncertain_icons:5')!= 'uncertain_icons'
