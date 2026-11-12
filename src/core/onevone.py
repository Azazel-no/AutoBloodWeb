"""1v1 chase timer: round state machine, score and the HUD 'survivor is down' detector.

Two players take turns as survivor and killer; the survivor's chase time is measured and the
longer chase of a pair wins a point. Pure logic with an explicit clock, so whole matches can be
tested without a game. Input is only observed (GetAsyncKeyState in the GUI layer), never sent.
"""
from dataclasses import dataclass, field

from src.core import killer_status as ks

IDLE = 'idle'        # nothing armed; the last result stays on screen
ARMED = 'armed'      # waiting for the first Ctrl (survivor) / M1 (killer)
RUNNING = 'running'  # the chase is being timed
DOWN_STATES = ('dying', 'carried', 'hooked', 'dead')  # any of these ends the chase (insta-downs included)

ACTIONS = ('show', 'stop', 'reset_round', 'undo', 'swap', 'reset_match')
# F7-F9 belong to the Bloodweb (F8 Start/Stop, F9 Stop), F10 to the crosshair, Tab to the perk overlay.
DEFAULT_KEYS = {'show': 'F6', 'stop': 'F5', 'reset_round': 'F2', 'undo': 'F3', 'swap': 'F4', 'reset_match': 'F11'}


def default_settings():
    return dict(enabled=False, names=['Player 1', 'Player 2'], keys=dict(DEFAULT_KEYS), opacity=90, size=100)


def clean_settings(data):
    data = data if isinstance(data, dict) else {}
    out = default_settings()
    out['enabled'] = data.get('enabled') is True
    names = data.get('names')
    if isinstance(names, list) and len(names) == 2 and all(isinstance(n, str) for n in names):
        out['names'] = [n[:24] for n in names]
    keys = data.get('keys') if isinstance(data.get('keys'), dict) else {}
    for action in ACTIONS:
        if isinstance(keys.get(action), str) and keys[action].strip(): out['keys'][action] = keys[action].strip().upper()
    for name, low, high in (('opacity', 30, 100), ('size', 50, 200)):
        value = data.get(name)
        if type(value) is int and low <= value <= high: out[name] = value
    return out


def load_down_templates(root):
    return ks.load_templates(root, DOWN_STATES)


def downed(statuses):
    """True when any HUD slot shows a downed survivor. In a 1v1 only one survivor is in the match,
    so the slot does not matter; this also makes the killer and the survivor point of view alike."""
    return any(s in DOWN_STATES for s in statuses)


@dataclass
class Round:
    survivor: int   # 0 or 1: who was timed
    seconds: float


@dataclass
class OneVOne:
    names: list = field(default_factory=lambda: ['Player 1', 'Player 2'])
    survivor: int = 0           # who plays survivor in the current round
    state: str = IDLE
    started_at: float | None = None
    rounds: list = field(default_factory=list)   # finished rounds, oldest first
    score: list = field(default_factory=lambda: [0, 0])
    stop_reason: str | None = None

    # --- flow -------------------------------------------------------------------------------
    def arm(self):
        """New match detected or reset-round hotkey: the next Ctrl / M1 starts the clock."""
        self.state, self.started_at, self.stop_reason = ARMED, None, None

    def start(self, now):
        if self.state != ARMED: return False
        self.state, self.started_at = RUNNING, now
        return True

    def stop(self, now, reason='down'):
        """Stores the time for this round's survivor, scores a finished pair and swaps sides."""
        if self.state != RUNNING: return None
        result = Round(self.survivor, max(0., now - self.started_at))
        self.rounds.append(result); self.state, self.started_at, self.stop_reason = IDLE, None, reason
        self.rescore(); self.survivor = 1 - self.survivor
        return result

    def reset_round(self):
        """Discards a running chase and arms again for the same survivor."""
        self.arm()

    def undo(self):
        """Removes the last finished round; the sides go back to who was timed in it."""
        if self.state == RUNNING:
            self.state, self.started_at = IDLE, None; return True
        if not self.rounds: return False
        last = self.rounds.pop(); self.survivor = last.survivor; self.state = IDLE
        self.rescore(); return True

    def swap(self):
        if self.state != RUNNING: self.survivor = 1 - self.survivor

    def reset_match(self):
        self.rounds.clear(); self.score = [0, 0]; self.survivor = 0
        self.state, self.started_at, self.stop_reason = IDLE, None, None

    def rescore(self):
        """A point per complete pair (each player timed once): the longer chase wins; a tie scores nothing."""
        self.score = [0, 0]
        for a, b in self.pairs():
            if a.seconds != b.seconds: self.score[a.survivor if a.seconds > b.seconds else b.survivor] += 1

    def pairs(self):
        out, pending = [], None
        for r in self.rounds:
            if pending is None or pending.survivor == r.survivor: pending = r
            else: out.append((pending, r)); pending = None
        return out

    # --- display ----------------------------------------------------------------------------
    def elapsed(self, now):
        return max(0., now - self.started_at) if self.state == RUNNING else 0.

    def shown_times(self, now):
        """Per player: (seconds or None, timed now, losing). Running time live; otherwise the latest."""
        times = [None, None]
        for r in self.rounds: times[r.survivor] = r.seconds
        if self.state == RUNNING: times[self.survivor] = self.elapsed(now)
        complete = len(self.rounds) % 2 == 0 and self.rounds and self.state != RUNNING
        losing = [False, False]
        if complete and None not in times and times[0] != times[1]:
            losing[0 if times[0] < times[1] else 1] = True
        return [(times[i], self.state in (RUNNING, ARMED) and self.survivor == i, losing[i]) for i in (0, 1)]


def format_time(seconds):
    """m:ss.cc"""
    if seconds is None: return '-:--.--'
    total = int(round(seconds * 100)); minutes, rest = divmod(total, 6000)
    return f'{minutes}:{rest // 100:02d}.{rest % 100:02d}'
