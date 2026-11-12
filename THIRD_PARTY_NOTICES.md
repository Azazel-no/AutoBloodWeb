# Third-party notices

AutoBloodWeb's own source code is under the MIT License (`LICENSE`). This file lists everything else that ships in this repository or in the release builds.

Not affiliated with or endorsed by Behaviour Interactive. Dead by Daylight and its assets are trademarks of Behaviour Interactive. Use at your own risk: third-party tools may violate the game's rules.

## Game data and images

**Dead by Daylight Wiki** — <https://deadbydaylight.wiki.gg> — community content under [Creative Commons Attribution-ShareAlike 4.0 (CC BY-SA 4.0)](https://creativecommons.org/licenses/by-sa/4.0/). The game images themselves remain the property of Behaviour Interactive Inc.

Files derived from the wiki (text and data derived from it stay under CC BY-SA):

- `assets/killers/`, `assets/survivors/` — icons
- `assets/ui/killers/`, `assets/ui/killer_hud/`, `assets/ui/roles/` — portraits and HUD images where listed in `assets/wiki_ui_manifest.json`
- `assets/ui/survivors/`, `assets/randomizer_data.json` — survivor portraits and item add-on types for the Randomizer
- `assets/item_info.json`, `assets/perk_catalog.json`, `assets/event_catalog.json` — names and descriptions
- `assets/wiki_manifest.json`, `assets/wiki_ui_manifest.json`, `assets/wiki_active.json` — source records
- `src/core/locale_extra.py` — official Spanish, German and French killer names as listed on the wiki's language editions (`/es/wiki/Asesinos`, `/de/wiki/Killer`, `/fr/wiki/Tueurs`)

Reference screenshots in `references/` and `tests/fixtures/` are crops and screenshots of the game's interface, used only for image recognition and tests. The player name in them has been blurred.

## Otzdarva's add-on tier list

Credit: Otzdarva, <https://otz-addon-tierlist.pages.dev/>. `assets/otz_tierlist.json` holds only the extracted data the app shows (add-on order, tier groups and comments). The original web page is not included.

## Map overlay

**Map callouts by Hens333** (<https://discord.com/invite/hens333>). These images are **not** in this repository or in the release builds: no licence that allows redistributing them was found. On first use the app downloads each image from the public `generated-pictures` branch of DBD Map Overlay (`raw.githubusercontent.com/LucaFontanot/dbd-map-overlay`), checks its MD5 against the catalog, and caches it in `data/map_cache` on the user's computer. The images remain the property of their author.

**DBD Map Overlay** by LucaFontanot — <https://github.com/LucaFontanot/dbd-map-overlay> — Apache License 2.0 (`licenses/dbd-map-overlay-Apache-2.0.txt`). `assets/maps/map_names.json` is derived from its `src/i18n` files (map and realm names in English, Russian, Spanish, German and French, v1.7.2) and its image catalog (paths and MD5 sums). Changes: reorganised into realms and maps, other languages dropped. No code was copied; the matching approach (name folding, fuzzy matching, realm lines never select a map) follows its design.

## Software

Versions are those of the release build environment. Licence texts are in `licenses/`; the release builds include the same folder.

| Component | Version | Licence | Text |
|---|---|---|---|
| Python | 3.15.0 | PSF License Agreement | `licenses/Python-LICENSE.txt` |
| PySide6-Essentials, shiboken6 (Qt for Python, with Qt 6 libraries) | 6.12.0 | LGPL-3.0-only (chosen from LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only) | `licenses/LGPL-3.0.txt`, `licenses/GPL-3.0.txt` |
| opencv-python-headless (OpenCV) | 5.0.0.93 | Apache 2.0; bundled libraries listed separately | `licenses/OpenCV-LICENSE.txt`, `licenses/OpenCV-3RD-PARTY.txt` |
| NumPy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | `licenses/NumPy-LICENSE.txt` |
| MSS | 10.2.0 | MIT | `licenses/mss-LICENSE.txt` |
| windows-capture | 2.0.1 | MIT | `licenses/windows-capture-LICENSE.txt` |
| Pillow | 12.3.0 | MIT-CMU | `licenses/Pillow-LICENSE.txt` |
| PyInstaller bootloader (build tool; only its bootloader ships in the executable) | 6.22.3 | GPL-2.0-or-later with the Bootloader Exception, which allows distributing the bootloader with any program | `licenses/PyInstaller-COPYING.txt` |
| Inno Setup (installer builder; its setup runtime is in the installer) | 6.7.3 | Inno Setup License (free for commercial and non-commercial use) | <https://jrsoftware.org/files/is/license.txt> |

Development only, not shipped: pytest (MIT).

### Qt / PySide6 (LGPLv3)

AutoBloodWeb uses Qt through PySide6 and links it dynamically. In the release builds the Qt and PySide6 libraries are ordinary files in `_internal\PySide6\` and `_internal\shiboken6\` next to `AutoBloodWeb.exe`, so you can replace them with your own compatible build (for example a PySide6 6.12 you compiled yourself). Qt source code: <https://code.qt.io>; PySide6 source code: <https://code.qt.io/cgit/pyside/pyside-setup.git/>. If you need the exact sources used for a release, open an issue.

## Fonts

No fonts are bundled. The interface uses Windows system fonts (Segoe UI, Bahnschrift).

## Privacy

The app sends nothing over the network while running. It reads screen pixels locally. The only network use is the optional wiki data refresh run by hand from the command line (`--sync-wiki`, `--sync-perks`, `--sync-items`), which downloads public pages from the wiki and uploads nothing.
