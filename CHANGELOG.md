# Changelog

## 1.1.0 — unreleased

- **Settings icon** redrawn as a classic gear with eight teeth.
- **1v1 timer** (new tab, off by default): top-centre scoreboard with both nicknames, the score and each player's latest chase time (m:ss.cc). A round is armed when a match starts; the clock starts on your first Ctrl (survivor) or left click (killer) and stops when the survivor's HUD portrait shows dying or carried (hooked and dead as a fallback). Sides swap automatically; the longer chase of each pair wins the point. Hotkeys for show/hide, manual stop, restart round, undo, swap sides and reset match.
- **Map overlay** (new tab, off by default): the current map's callouts in the top-left corner, only in a match and only while DBD is in front. The map name is read from the loading screen (five languages, fuzzy matching; an uncertain read shows nothing) or picked by hand. Map callouts by Hens333, downloaded on first use.

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
