"""Which HUD row is the player's own (TASKS 2026-11-25, B2).

The chase meme must react only to the player's own portrait. The row is established once per match
from identity, never from the vertical position alone:

* gold stripe: the perks overlay's Tab read marks the player's own Tab row by its gold stripe; that
  row's character portrait is compared with the four HUD portraits (the overlay's own portrait
  matcher and thresholds);
* nickname: the nickname typed in the chase meme settings is compared with the HUD nickname strips
  the overlay already reads with Windows OCR (the overlay's own name matcher and thresholds).

Either method alone establishes the row; when both are available and disagree, nothing is
established. Once established, the row is tracked by its portrait, not by its index, and is
re-checked cheaply whenever HUD portraits are at hand. Without an established row the chase meme
stays idle and says why (fail safe).
"""
import threading
import time

from src.core.match_reader import portrait_similarity
from src.core.nicknames import name_similarity

PORTRAIT_MIN = .65      # match_reader.assign: a direct portrait pair
PORTRAIT_MARGIN = .08   # ...and its lead over the runner-up
NAME_MIN = .75          # match_reader.assign_scores: a direct nickname pair
NAME_MARGIN = .2
TRACK_MIN = .55         # re-check: the locked portrait is still recognisable in some row
TRACK_MARGIN = .1       # ...clearly better there than in the locked row before the row is moved
ROWS = 4


def _best(scores, minimum, margin):
    """(row, score) of a clear winner among {row: score}, or (None, best score)."""
    if not scores: return None, 0.
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    row, top = ranked[0]; second = ranked[1][1] if len(ranked) > 1 else -1.
    return (row, top) if top >= minimum and top - second >= margin else (None, top)


class OwnRowLock:
    """Thread-safe: the perks overlay writes evidence from its worker results, the chase meme reads."""

    def __init__(self, clock=time.monotonic, nickname_source=None):
        self.clock = clock; self._lock = threading.Lock(); self.events = []
        self.nickname = ''; self.nickname_source = nickname_source   # callable: the nickname typed in the settings
        self.reset('created')

    # --- lifecycle --------------------------------------------------------------------------
    def reset(self, why='new match'):
        with self._lock:
            self.row = None; self.method = None; self.portrait = None; self.evidence = {}
            self.by_tab = None; self.by_name = None; self.since = None
            self.why = 'own row not established yet: press Tab once (teammate perks overlay on) or set your nickname'
            self._event('own_row_reset', why=why)

    def set_nickname(self, text):
        with self._lock:
            self.nickname = (text or '').strip()

    # --- evidence ---------------------------------------------------------------------------
    def observe_tab(self, own_tab_portrait, hud_looks):
        """own_tab_portrait: portrait feature of the gold-stripe Tab row (None when no gold row was seen).
        hud_looks: per HUD row, a list of recent portrait features."""
        if own_tab_portrait is None: return
        scores = {}
        for row, features in enumerate(list(hud_looks)[:ROWS]):
            values = [portrait_similarity(own_tab_portrait, f) for f in (features or []) if f is not None]
            if values: scores[row] = max(values)
        row, top = _best(scores, PORTRAIT_MIN, PORTRAIT_MARGIN)
        with self._lock:
            self.evidence['gold_stripe'] = {f'row{r + 1}': round(v, 3) for r, v in scores.items()}
            if row is None: return
            self.by_tab = row
            self._settle(own_portrait=self._first(hud_looks, row))

    def observe_names(self, hud_names, hud_looks=None):
        """hud_names: {row: [OCR texts]} of the HUD nickname strips (all four rows)."""
        with self._lock:
            nickname = self.nickname
        if self.nickname_source is not None:
            try: nickname = (self.nickname_source() or '').strip()
            except Exception: nickname = ''
        if not nickname or not hud_names: return
        scores = {row: max(name_similarity(nickname, t) for t in texts) for row, texts in hud_names.items() if texts}
        row, top = _best(scores, NAME_MIN, NAME_MARGIN)
        with self._lock:
            self.evidence['nickname'] = {f'row{r + 1}': round(v, 3) for r, v in scores.items()}
            if row is None: return
            self.by_name = row
            self._settle(own_portrait=self._first(hud_looks, row) if hud_looks else None)

    def track(self, hud_portraits):
        """Cheap re-check on fresh HUD portrait features (one per row): follow the own portrait if the
        rows changed. Never establishes a row on its own."""
        with self._lock:
            if self.row is None or self.portrait is None: return self.row
            scores = {r: portrait_similarity(self.portrait, f) for r, f in enumerate(list(hud_portraits)[:ROWS]) if f is not None}
            if not scores: return self.row
            best = max(scores, key=scores.get)
            if best != self.row and scores[best] >= TRACK_MIN and scores[best] - scores.get(self.row, -1.) >= TRACK_MARGIN:
                self._event('own_row_moved', before=self.row + 1, after=best + 1, scores={f'row{r + 1}': round(v, 3) for r, v in scores.items()})
                self.row = best
            return self.row

    # --- reading ----------------------------------------------------------------------------
    def current(self):
        """(row or None, method, reason text)."""
        with self._lock:
            return self.row, self.method, self.why

    def take_events(self):
        with self._lock:
            events, self.events = self.events, []
            return events

    # --- internals (lock held) --------------------------------------------------------------
    @staticmethod
    def _first(looks, row):
        try:
            features = list(looks)[row]
        except (IndexError, TypeError):
            return None
        features = [f for f in (features or []) if f is not None] if isinstance(features, list) else [features]
        return features[-1] if features else None

    def _settle(self, own_portrait=None):
        tab, name = self.by_tab, self.by_name
        if tab is not None and name is not None and tab != name:
            self.row = None; self.method = None
            self.why = f'own row contradictory: the gold stripe says row {tab + 1}, the nickname says row {name + 1}'
            self._event('own_row_contradiction', gold_stripe=tab + 1, nickname=name + 1, evidence=dict(self.evidence)); return
        row = tab if tab is not None else name
        method = 'gold_stripe+nickname' if tab is not None and name is not None else 'gold_stripe' if tab is not None else 'nickname'
        if row != self.row or method != self.method:
            self.row, self.method, self.since = row, method, self.clock()
            self.why = f'own row {row + 1} by {method}'
            self._event('own_row_established', row=row + 1, method=method, evidence=dict(self.evidence))
        if own_portrait is not None: self.portrait = own_portrait

    def _event(self, kind, **details):
        self.events = (self.events + [dict(kind=kind, **details)])[-50:]
