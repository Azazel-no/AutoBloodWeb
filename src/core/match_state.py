"""Which perks belong to which HUD slot for the current match. Pure logic, no Qt.

Slots are HUD indexes 1-3 (slots 2-4 on screen); slot 0 is the player and is
never drawn. Perks are locked to a slot for the rest of the match. A later Tab
press fills unassigned slots and updates a slot only when its portrait matches.
"""
from dataclasses import dataclass, field
import time
import numpy as np
from src.core.match_reader import SLOTS, assign_scores, portrait_similarity, score_rows


@dataclass
class SlotPerks:
    slots: tuple  # SlotResult per Tab perk slot 1-4
    tiles: tuple  # Captured diamond tiles (BGR), drawn as the real in-game art
    hud_portrait: tuple = field(repr=False)  # The HUD portrait seen when assigned (may be None)
    assigned_at: float = 0.
    row: int = -1  # Tab row index the slot was paired with
    method: str = 'portrait'  # 'portrait', 'nickname', 'portrait+nickname' or 'by_elimination'
    looks: list = field(default_factory=list,repr=False)  # HUD portrait looks of this player this match

    def known(self,feature,threshold):
        return any(portrait_similarity(feature,look)>=threshold for look in self.looks)


def portrait_difference(a,b):
    """Mean L/a/b difference inside the circle: tiny for the same still frame."""
    return float(np.abs(a[0]-b[0])[a[1]].mean())


class HudCache:
    """Recent HUD portraits and nickname strips seen while Tab is closed, so one Tab press is enough.

    A crop is kept only when it looks the same in two consecutive frames, which
    rejects the HUD fading back in after Tab. Repeats of a still portrait refresh
    one entry, so `per_slot` different looks (clean, injured, hooked...) cover the
    last `keep` seconds. Not the whole match: portraits identify characters, so a
    clean portrait from the previous match could belong to someone else.
    """
    keep=120.    # Seconds a cached portrait stays usable; shorter than the gap between matches.
    per_slot=8
    stable=6.    # Max difference between consecutive frames of a usable crop.
    same=3.      # Below this a new crop replaces the last entry instead of adding one.

    def __init__(self,clock=time.monotonic):
        self.clock=clock;self.clear()

    def clear(self):
        self.last=[None]*4;self.entries=[[] for _ in range(4)];self.strips=[None]*4

    def add(self,portraits,strips=None):
        now=self.clock()
        for slot,strip in enumerate(strips or ()):
            if strip is not None:self.strips[slot]=(now,strip)
        for slot,feature in enumerate(portraits):
            previous=self.last[slot];self.last[slot]=feature
            if feature is None or previous is None or portrait_difference(feature,previous)>self.stable:continue
            entries=[e for e in self.entries[slot] if now-e[0]<=self.keep]
            if entries and portrait_difference(entries[-1][1],feature)<=self.same:entries[-1]=(now,feature)
            else:entries.append((now,feature))
            self.entries[slot]=entries[-self.per_slot:]

    def candidates(self):
        now=self.clock()
        return [[f for t,f in entries if now-t<=self.keep] for entries in self.entries]

    def name_strips(self,max_age=10.):
        """Latest HUD nickname strip per slot (names do not change; the background does)."""
        now=self.clock()
        return [s[1] if s is not None and now-s[0]<=max_age else None for s in self.strips]


class MatchState:
    same_match=.5    # A new Tab whose teammates match none of the stored portraits starts a new match.
    present=.35      # A stored HUD portrait still recognisable in its slot.
    absent_checks=10 # Consecutive checks (0.5 s apart) without any portrait before clearing.

    def __init__(self,clock=time.monotonic):
        self.clock=clock;self.slots={};self.absent=0;self.last_mapping={}

    def clear(self):
        self.slots={};self.absent=0;self.last_mapping={}

    def apply(self,rows,hud_portraits=None,scores=None,best=None,names=None,looks=None):
        """Merge one Tab read. Returns (changed slot numbers, scores) for the log.

        Either `hud_portraits` (one HUD feature per slot) or a precomputed
        `scores`/`best` table from `score_rows` over several frames; `names` are
        nickname scores; `looks[slot]` are the recent HUD looks of that slot.
        """
        if scores is None:scores,best=score_rows(rows,[[p] for p in hud_portraits])
        if looks is None:looks=[[p] if p is not None else [] for p in hud_portraits] if hud_portraits else [[] for _ in range(4)]
        teammates={r.index:r for r in rows if not r.own}
        pairs,methods=assign_scores(scores,list(teammates),names=names)
        self.last_mapping=dict(pairs)
        # The HUD portrait each slot is judged by: the paired one, else the best seen there.
        seen={}
        for s in SLOTS:
            options=[(scores[(r,s)],best[(r,s)]) for r in teammates if best.get((r,s)) is not None]
            if options:seen[s]=max(options,key=lambda o:o[0])[1]
        seen.update({s:best[(r,s)] for s,r in pairs.items() if best.get((r,s)) is not None})
        # A new match is recognised by the lobby (the controller clears the state there),
        # not guessed from portraits, which status icons cover.
        changed=[]
        for slot,index in pairs.items():
            row=teammates[index];current=self.slots.get(slot)
            known=[f for f in [seen.get(slot),*looks[slot]] if f is not None][:12]
            if current is None:
                self.slots[slot]=SlotPerks(row.slots,row.tiles,seen.get(slot),self.clock(),index,methods[slot],known);changed.append(slot)
                continue
            # Never downgrade: a known perk is kept; a re-read only fills unknown ones.
            results=list(current.slots);tiles=list(current.tiles);filled=False
            for i,(old,new) in enumerate(zip(current.slots,row.slots)):
                if old.icon_id is None and new.icon_id is not None:
                    results[i]=new;tiles[i]=row.tiles[i];filled=True
            if filled:current.slots=tuple(results);current.tiles=tuple(tiles);changed.append(slot)
            current.looks=(current.looks+[f for f in known if not current.known(f,.95)])[-12:]
        self.absent=0
        return changed,scores

    def complete(self,rows):
        """Every teammate row of this read has a slot."""
        return len(self.last_mapping)>=sum(not r.own for r in rows)

    def all_known(self,teammates=3):
        """All teammates assigned and every one of their perks known (an empty slot counts as known)."""
        return (len(self.slots)>=teammates and
                all(s.icon_id is not None for perks in self.slots.values() for s in perks.slots))

    def check_presence(self,hud_portraits):
        """False once the HUD has been gone for several checks (match over).

        A slot counts when its portrait matches any look seen this match. While at
        least one slot is recognised, new looks of the others (injured, hooked,
        dying, killer status) are learned, so covered portraits do not end the match.
        """
        if not self.slots:return False
        if not any(stored.looks for stored in self.slots.values()):
            # Assigned by nickname with no portrait seen yet: start from this frame.
            for slot,stored in self.slots.items():
                if hud_portraits[slot] is not None:stored.looks=[hud_portraits[slot]]
            self.absent=0;return True
        matched={slot for slot,stored in self.slots.items() if stored.known(hud_portraits[slot],self.present)}
        if matched:
            for slot,stored in self.slots.items():
                if slot not in matched and hud_portraits[slot] is not None:
                    stored.looks=(stored.looks+[hud_portraits[slot]])[-12:]
        self.absent=0 if matched else self.absent+1
        if self.absent>=self.absent_checks:
            self.clear();return False
        return True
