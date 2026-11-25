"""'Match found' notifier (TASKS 2026-11-25, C2/C3).

While the player waits in the queue with another game in front, AutoBloodWeb listens to passive
signals (src/core/queue_signals.py) and, when DBD leaves the queue for a lobby, plays a short tone
synthesised here (no audio files), optionally sends "Match found" to the player's own ntfy.sh topic
or webhook, and never touches focus or input.

Detection (C2) is a small fusion of independent signals. Which ones are strong is decided from the
queue probe's evidence (docs/QUEUE_PROBE_REPORT.md); until then every signal is weak and two weak
signals must coincide within FUSION_WINDOW seconds. One alert per match: after firing, the detector
waits REARM_SECONDS and for the signals to go quiet before it can fire again.
"""
import io
import json
import math
import threading
import time
import urllib.parse
import urllib.request
import wave

import numpy as np

SAMPLE_RATE = 22050
REPEATS = 3                 # the tone plays up to this many times unless acknowledged
REPEAT_GAP = 2.5            # seconds between repeats
FUSION_WINDOW = 3.          # two weak signals this close together fire
REARM_SECONDS = 120.        # after an alert: no new one for this long (a match lasts far longer)
QUIET_SECONDS = 20.         # ...and the signals must have been quiet this long
PUSH_TEXT = 'Match found'

# Signal strengths: 'strong' fires alone, 'weak' needs a second weak one, 'off' is ignored.
# All weak until the queue probe shows which signal is reliable (docs/QUEUE_PROBE_REPORT.md).
DEFAULT_STRENGTHS = dict(lobby_screen='weak', tcp_new_remote='weak', udp_new_local='weak', audio_burst='weak', log_burst='weak')
AUDIO_BURST = .25           # peak level of the game's audio session after a quiet second
AUDIO_QUIET = .05
LOG_BURST = 20000           # bytes appended to the game log in one second


# --- the tone ------------------------------------------------------------------------------------
def tone_wav(volume=.7):
    """A short three-note rising chime as WAV bytes, synthesised in code."""
    volume = max(0., min(1., float(volume))); notes = ((880., .14), (1175., .14), (1568., .28)); parts = []
    for frequency, seconds in notes:
        t = np.arange(int(SAMPLE_RATE * seconds)) / SAMPLE_RATE
        envelope = np.minimum(1., t / .01) * np.exp(-t * 6.)
        wave_ = np.sin(2 * math.pi * frequency * t) + .3 * np.sin(4 * math.pi * frequency * t)
        parts.append(wave_ * envelope / 1.3); parts.append(np.zeros(int(SAMPLE_RATE * .03)))
    samples = (np.concatenate(parts) * volume * 32767 * .9).astype('<i2')
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(SAMPLE_RATE); out.writeframes(samples.tobytes())
    return buffer.getvalue()


def play_wav(data):
    """Audio only (no window), audible while another game is fullscreen. Windows: winsound from memory."""
    try:
        import winsound
        winsound.PlaySound(data, winsound.SND_MEMORY)
        return True
    except (ImportError, RuntimeError, OSError):
        return False


class Chime:
    """Plays the tone up to REPEATS times on its own thread unless acknowledge() is called."""

    def __init__(self, player=play_wav, gap=REPEAT_GAP):
        self.player = player; self.gap = gap; self.stop_event = threading.Event(); self.thread = None

    def ring(self, volume, repeats=REPEATS):
        self.acknowledge(); stop = self.stop_event = threading.Event(); data = tone_wav(volume)

        def run():
            for i in range(repeats):
                if stop.is_set(): return
                self.player(data)
                if i + 1 < repeats and stop.wait(self.gap): return
        self.thread = threading.Thread(target=run, name='MatchChime', daemon=True); self.thread.start()
        return self.thread

    def acknowledge(self):
        self.stop_event.set()


# --- the optional phone push ----------------------------------------------------------------------
def push_request(target):
    """(url, body bytes, headers) for the player's own target, or None when it is not usable.
    ntfy:   a topic name ('my-dbd-topic') or a full https://ntfy.sh/<topic> URL -> plain text body;
    Discord: a https://discord.com/api/webhooks/... URL -> {"content": ...};
    Telegram: a https://api.telegram.org/bot<token>/sendMessage?chat_id=<id> URL -> text parameter."""
    target = (target or '').strip()
    if not target: return None
    if '://' not in target:
        if not all(c.isalnum() or c in '-_' for c in target): return None
        return f'https://ntfy.sh/{target}', PUSH_TEXT.encode('utf-8'), {'Content-Type': 'text/plain; charset=utf-8'}
    parsed = urllib.parse.urlparse(target)
    if parsed.scheme != 'https' or not parsed.netloc: return None
    host = parsed.netloc.lower()
    if host.endswith('discord.com') or host.endswith('discordapp.com'):
        return target, json.dumps(dict(content=PUSH_TEXT)).encode('utf-8'), {'Content-Type': 'application/json'}
    if host == 'api.telegram.org':
        query = dict(urllib.parse.parse_qsl(parsed.query)); query['text'] = PUSH_TEXT
        return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query))), b'', {}
    return target, PUSH_TEXT.encode('utf-8'), {'Content-Type': 'text/plain; charset=utf-8'}   # ntfy on its own server


def send_push(target, opener=None, timeout=5.):
    """Sends only the text 'Match found'. Returns (ok, detail). Never raises."""
    request = push_request(target)
    if request is None: return False, 'push target not usable'
    url, body, headers = request
    try:
        req = urllib.request.Request(url, data=body or None, headers={'User-Agent': 'AutoBloodWeb', **headers}, method='POST')
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as response:
            return 200 <= response.status < 300, f'HTTP {response.status}'
    except Exception as exc:
        return False, type(exc).__name__


# --- detection ------------------------------------------------------------------------------------
class MatchFoundDetector:
    """Fusion of independent passive signals; fires once per match."""

    def __init__(self, strengths=None, clock=time.monotonic):
        self.strengths = dict(DEFAULT_STRENGTHS, **(strengths or {})); self.clock = clock
        self.seen = []; self.fired_at = None; self.last_signal = None; self.previous_audio = 0.; self.previous_lobby = None

    def signals(self, record):
        """Signal names present in one probe-style record (see QueueProbe for its shape)."""
        found = []
        net = record.get('net') or {}
        if any(not r.split(' ')[0].endswith(':443') for r in net.get('tcp_new', [])): found.append('tcp_new_remote')
        if net.get('udp_new'): found.append('udp_new_local')
        audio = (record.get('audio') or {}).get('peak')
        if audio is not None:
            if audio >= AUDIO_BURST and self.previous_audio <= AUDIO_QUIET: found.append('audio_burst')
            self.previous_audio = audio
        if (record.get('log') or {}).get('grew', 0) >= LOG_BURST: found.append('log_burst')
        lobby = (record.get('capture') or {}).get('lobby')
        if lobby is not None:
            if lobby and self.previous_lobby is False: found.append('lobby_screen')
            self.previous_lobby = lobby
        return [s for s in found if self.strengths.get(s, 'off') != 'off']

    def feed(self, record, now=None):
        """Returns (fire, reason) for one record."""
        now = self.clock() if now is None else now; signals = self.signals(record)
        if signals: self.last_signal = now
        if self.fired_at is not None:
            if now - self.fired_at < REARM_SECONDS or (self.last_signal is not None and now - self.last_signal < QUIET_SECONDS):
                return False, None
            self.fired_at = None; self.seen = []
        self.seen = [(t, s) for t, s in self.seen if now - t <= FUSION_WINDOW] + [(now, s) for s in signals]
        strong = [s for _, s in self.seen if self.strengths.get(s) == 'strong']
        weak = sorted({s for _, s in self.seen if self.strengths.get(s) == 'weak'})
        if strong or len(weak) >= 2:
            self.fired_at = now; self.seen = []
            return True, f"strong signal {strong[0]}" if strong else f"weak signals {', '.join(weak)} within {FUSION_WINDOW:.0f} s"
        return False, None


def load_strengths(root):
    """Strengths decided from the probe (assets/queue_signals.json), else all weak."""
    try:
        from pathlib import Path
        data = json.loads((Path(root) / 'assets/queue_signals.json').read_text(encoding='utf-8'))
        return {k: v for k, v in data.get('strengths', {}).items() if v in ('strong', 'weak', 'off')}
    except (OSError, ValueError, AttributeError):
        return {}


# --- settings ------------------------------------------------------------------------------------
def default_settings():
    return dict(enabled=False, background_only=True, volume=70, push=False, push_target='', probe_minutes=20)


def clean_settings(data):
    data = data if isinstance(data, dict) else {}; out = default_settings()
    for key in ('enabled', 'push'): out[key] = data.get(key) is True
    out['background_only'] = data.get('background_only', True) is not False
    if type(data.get('volume')) is int and 0 <= data['volume'] <= 100: out['volume'] = data['volume']
    if isinstance(data.get('push_target'), str): out['push_target'] = data['push_target'].strip()[:300]
    if type(data.get('probe_minutes')) is int and 1 <= data['probe_minutes'] <= 120: out['probe_minutes'] = data['probe_minutes']
    return out
