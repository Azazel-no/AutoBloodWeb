"""K1 (15.11): is the HUD still the match the teammate perks were locked for?

The perks overlay locks once every teammate's perks are known and, until now, unlocked only when the lobby's
READY/CANCEL button was seen. On 2026-11-15 a lobby went unseen (13:47-13:49: DBD not in front, or too short)
and the next match got the previous match's perks; its 9 Tab presses were ignored as "locked".

The identity of a locked match is, per HUD slot, the teammate's nickname (the Tab nickname of the row paired
with the slot plus the HUD nickname OCR) and the HUD portrait looks seen this match; per Tab row, its nickname
and character portrait. Nicknames decide wherever both sides have one; portraits only otherwise (a character
can be in two matches in a row, and two players can play the same character). Pure logic, no capture.
"""
from src.core.match_reader import SLOTS, portrait_similarity
from src.core.nicknames import name_similarity

NAME_SAME = .75          # the overlay's own nickname threshold (nicknames.NAME_MINIMUM)
NAME_DIFFERENT = .5      # below this two OCR readings are different players
LOOK_SAME = .65          # portrait thresholds, as for the Tab <-> HUD pairing
LOOK_DIFFERENT = .35     # MatchState.present: below it a stored portrait is not recognisable
SLOTS_TO_DIFFER = 2      # of the 3 teammates
CHECKS_TO_UNLOCK = 2     # consecutive HUD checks


def judge(names_before, looks_before, names_now, look_now):
    """('same' | 'different' | 'unknown', evidence) for one teammate."""
    if names_before and names_now:
        best = max(name_similarity(a, b) for a in names_before for b in names_now)
        verdict = 'same' if best >= NAME_SAME else 'different' if best < NAME_DIFFERENT else 'unknown'
        return verdict, dict(method='nickname', score=round(best, 3), now=list(names_now)[:2], before=list(names_before)[:2])
    if look_now is not None and looks_before:
        best = max(portrait_similarity(look_now, look) for look in looks_before)
        verdict = 'same' if best >= LOOK_SAME else 'different' if best < LOOK_DIFFERENT else 'unknown'
        return verdict, dict(method='portrait', score=round(best, 3))
    return 'unknown', dict(method='none')


def overall(verdicts):
    different = sum(v == 'different' for v in verdicts); same = sum(v == 'same' for v in verdicts)
    return 'different' if different >= SLOTS_TO_DIFFER else 'same' if same >= SLOTS_TO_DIFFER else 'unknown'


def compare_hud(identity, names_now, looks_now):
    """identity: dict(names={slot: [..]}, looks={slot: [features]}); names_now {slot: [..]}, looks_now [feature per slot 0-3].
    Returns (verdict, {slot+1: evidence}) for the log."""
    verdicts = {}; evidence = {}
    for slot in SLOTS:
        look = looks_now[slot] if looks_now is not None and slot < len(looks_now) else None
        verdicts[slot], evidence[slot + 1] = judge(identity['names'].get(slot, ()), identity['looks'].get(slot, ()), names_now.get(slot, ()), look)
        evidence[slot + 1]['verdict'] = verdicts[slot]
    return overall(verdicts.values()), evidence


def compare_tab(identity, names_now, portraits_now):
    """The Tab scoreboard of a press while locked: each teammate row against every locked row (row order is not
    trusted). names_now {row: [..]}, portraits_now {row: feature}. Returns (verdict, {row+1: evidence})."""
    before_names = [n for n in identity['tab_names'].values() if n]; before_looks = [p for p in identity['tab_portraits'].values() if p is not None]
    verdicts = {}; evidence = {}
    for row in sorted(set(names_now) | set(portraits_now)):
        names = names_now.get(row, ())
        if names and before_names:
            best = max(name_similarity(a, b) for group in before_names for a in group for b in names)
            verdict = 'same' if best >= NAME_SAME else 'different' if best < NAME_DIFFERENT else 'unknown'
            evidence[row + 1] = dict(method='nickname', score=round(best, 3), now=list(names)[:2], verdict=verdict)
        elif portraits_now.get(row) is not None and before_looks:
            best = max(portrait_similarity(portraits_now[row], look) for look in before_looks)
            verdict = 'same' if best >= LOOK_SAME else 'different' if best < LOOK_DIFFERENT else 'unknown'
            evidence[row + 1] = dict(method='portrait', score=round(best, 3), verdict=verdict)
        else:
            verdict = 'unknown'; evidence[row + 1] = dict(method='none', verdict=verdict)
        verdicts[row] = verdict
    return overall(verdicts.values()), evidence


def identity_from_session(state_slots, session):
    """The locked match's identity from the Tab session that completed the perks (MatchState.slots + session)."""
    texts = (session or {}).get('texts', {}); tab = texts.get('tab', {}); hud = texts.get('hud', {})
    rows = (session or {}).get('rows', ())
    names = {}; looks = {}
    for slot, perks in state_slots.items():
        found = list(hud.get(slot, ())) + list(tab.get(perks.row, ()))
        if found: names[slot] = found
        looks[slot] = [f for f in perks.looks if f is not None]
    return dict(names=names, looks=looks, tab_names={r.index: list(tab.get(r.index, ())) for r in rows if not r.own},
                tab_portraits={r.index: r.portrait for r in rows if not r.own}, own_rows=[r.index for r in rows if r.own])
