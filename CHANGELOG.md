# Changelog

## 1.1.0 — unreleased

- **Settings icon** redrawn as a classic gear with eight teeth.
- **1v1 timer** (new tab, off by default): top-centre scoreboard with both nicknames, the score and each player's latest chase time (m:ss.cc). A round is armed when a match starts; the clock starts on your first Ctrl (survivor) or left click (killer) and stops when the survivor's HUD portrait shows dying or carried (hooked and dead as a fallback). Sides swap automatically; the longer chase of each pair wins the point. Hotkeys for show/hide, manual stop, restart round, undo, swap sides and reset match.
- **Map overlay** (new tab, off by default): the current map's callouts in the top-left corner, only in a match and only while DBD is in front. The map name is read from the loading screen (five languages, fuzzy matching; an uncertain read shows nothing) or picked by hand. Map callouts by Hens333, downloaded on first use.
- **Memes tab** (new, everything off by default). The Randomizer moved here as its first section; its own sidebar entry is gone. A meme volume slider (separate from the game, 60 % by default) and a global mute hotkey (Ctrl+M, changeable in Hotkeys). The media is yours: it lives in a meme pack folder in `%APPDATA%\AutoBloodWeb\meme_pack` (button "Open meme pack folder", README inside) and is never part of the app; with an empty pack the features ask you to add files. `scripts/build_meme_pack.py` prepares a pack from your own clips.
- **Chase meme** (survivor): while the killer chases you, a glitchy chase timer in the top-right corner, looping music, a weighted random voice line every 15–20 s (music ducked meanwhile) and, after a quarter of the lines, a keyed dance clip on the right. The chase starts on animated chase horns at your own portrait (the Obsession's static horns do not count) and ends when the red glow on your character's back has been gone for about 1.5 s. Timer size and opacity, dance position and size, a Preview button.
- **Sable Detector** (experimental): when Sable Ward is in the match and on screen, a hand-drawn red circle follows her and blinks, with a random photo beside her and a looping siren. People are found with YOLOX (Apache-2.0, downloaded on first use); recognition is not reliable yet and the detector stays off until Sable's HUD portrait sample is added.

- **Bloodweb: no more stuck runs after an unprovable click** (live The Mastermind run, 13.11). A click whose result the screen could not prove (a missed click on a locked node, or a centre auto-buy that bought nothing) paused the run, and every later Start refused until levels were bought by hand. Locked nodes are now read as "not bought" at their real confidence, and an earlier click that still cannot be proven after a short read-only look at a readable web is dropped without counting a purchase, so the run (or the next Start) continues.
- **Bloodweb: faster centre search.** A hovered or red centre made each read take 2.7–3.1 s, longer than the 2 s freshness rule, so every frame was discarded as stale (9 minutes lost in one evening). The other scales are now searched only around a centre already found at the game's own scale; results are identical on every saved frame. A centre press that buys nothing now falls back to node-by-node buying for that level only.
- **Prestige button with any prestige number.** The not-hovered prestige emblem with a two-digit number (P10–P99) or P100 was not recognised; it is now found by its wreath. Checked for prestige numbers 1–100 on both hovered and not-hovered emblems.
- **Map overlay: Yamaoka Estate and other long names.** A line such as "YAMAOKA ESTATE - FAMILY RE" (realm, then a cut-off map name) now picks the one map of that realm that starts that way, in all five languages; a realm alone or a too-short cut still shows nothing.
- **Chase meme:** the sound, the timer and the dancer fade out over 3–5 s (default 4 s) when the chase ends; a new chase during the fade starts fresh. The chase also ends when your own portrait shows you dying, carried or hooked.
- **Sable Detector:** Sable's HUD portraits (normal, Obsession, killer view) are included, so the detector switches on only in matches with Sable.
- **Teammate perks:** every Tab press that is ignored is written to the overlay log with its reason, to find why perks were not read in a match against The Nightmare.

- **Prestige rewards no longer hidden by the app window.** Right after a prestige the window grew back over the bottom-right OK of the "... UNLOCKED" reward screen, so the reward could not be seen and the run paused. The window now stays compact until an ordinary level 1–49 is read again.
- **Chase meme: no more false starts.** Animated horns start a chase only when the red heart on your back is also seen within 2 s; a bright, moving background behind the portrait no longer counts.
- **Sable Detector:** optional collection of training frames from real matches (off by default) and a labelling tool.
- **NVIDIA recording:** the teammate-perks and Decisive Strike overlays now cover only the left half of the game and no overlay window is the size of the whole screen, which NVIDIA could take for a fullscreen non-game application.

## 1.0.1 — 2026-11-11

Hotfix.

- **Administrator rights.** The app always asks for them through the normal Windows prompt (UAC). The installer's "Launch AutoBloodWeb" checkbox now starts it through that prompt instead of failing with a raw "requires elevation" error. If the app is somehow started without rights, it shows a short message in your language and restarts itself as administrator. Technical "integrity"/"elevation" messages are gone in all five languages.
- **Start with DBD.** Still a logon task with highest privileges. An old Run-key entry is moved to that task automatically; the uninstaller removes both.
- **Bloodweb on survivors other than Cheryl.** All survivors shared one saved purchase scene, so after switching survivor the previous survivor's clicks were restored onto the new web and blocked it ("automatic unlock unproven", "previous start unresolved"). A saved scene that provably belongs to another web is now dropped (two fresh reads, never credited as a purchase): a bought node shown as buyable again, a different level, or a far level for a pending click.
- **Missing survivor add-ons.** 21 survivor add-ons were missing from the library and priorities, among them Gel Dressings (+16 healing charges), Odd Bulb, Blood Amber, Crimson Stamp, Potent Extract, Anti-Exhaustion Syringe, Needle & Thread and Rubber Gloves. All 54 survivor add-ons and all 900 killer add-ons on the Wiki are now covered; five survivor items had wrong or missing names (Engineer's Toolbox, Skeleton Key, Cryptic Map, Toolbox, Worn-Out Tools).

## 1.0.0 — 2026-11-10

First public release.

- Bloodweb automation by per-character priorities, centre auto-buy on levels 1–9, Auto Prestige, Stop after N levels.
- Icon library with descriptions and Otzdarva's recommended killer add-ons.
- Build randomizer.
- Optional match overlays: teammate perks after Tab, Decisive Strike timer; optional crosshair.
- Start with DBD (tray).
- Interface in English, Russian, Spanish, German and French.
- UI/HUD scale 100% and 90%.
- Windows installer and portable build; no Python needed.
