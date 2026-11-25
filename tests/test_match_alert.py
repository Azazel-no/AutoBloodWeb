"""C (TASKS 2026-11-25): queue probe pieces and the 'match found' notifier, without a game."""
import io
import os
import threading
import time
import wave

import pytest

from src.core import match_alert as ma
from src.core.queue_signals import LogGrowth


def test_tone_is_a_short_synthesised_wav_and_scales_with_volume():
    loud, quiet = ma.tone_wav(1.), ma.tone_wav(.1)
    with wave.open(io.BytesIO(loud)) as w:
        assert w.getnchannels() == 1 and w.getframerate() == ma.SAMPLE_RATE and .4 < w.getnframes() / w.getframerate() < 1.
    import numpy as np
    peak = lambda data: np.abs(np.frombuffer(data[44:], '<i2')).max()
    assert peak(quiet) < peak(loud) / 5


def test_chime_repeats_three_times_unless_acknowledged():
    played = []; chime = ma.Chime(player=played.append, gap=.01)
    chime.ring(.5).join(2); assert len(played) == ma.REPEATS
    played.clear(); chime = ma.Chime(player=lambda d: (played.append(d), chime.acknowledge()), gap=.2)
    chime.ring(.5).join(2); assert len(played) == 1


def test_push_sends_only_the_text_to_the_users_own_target():
    url, body, headers = ma.push_request('my-dbd-queue'); assert url == 'https://ntfy.sh/my-dbd-queue' and body == b'Match found'
    url, body, _ = ma.push_request('https://discord.com/api/webhooks/1/abc'); assert body == b'{"content": "Match found"}'
    url, body, _ = ma.push_request('https://api.telegram.org/bot1:x/sendMessage?chat_id=5'); assert url.endswith('chat_id=5&text=Match+found')
    assert ma.push_request('') is None and ma.push_request('http://insecure.example') is None and ma.push_request('bad topic!') is None
    sent = []

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
    ok, detail = ma.send_push('my-dbd-queue', opener=lambda req, timeout: (sent.append(req), Response())[1])
    assert ok and sent[0].data == b'Match found' and sent[0].get_method() == 'POST'
    assert ma.send_push('', opener=None) == (False, 'push target not usable')


def record(**kw):
    return dict(net=dict(tcp_new=kw.get('tcp', []), udp_new=kw.get('udp', [])), audio=dict(peak=kw.get('audio', 0.)),
                log=dict(grew=kw.get('log', 0)), capture=dict(lobby=kw.get('lobby', False)))


def test_two_weak_signals_fire_once_and_one_alone_never_fires():
    d = ma.MatchFoundDetector()
    assert d.feed(record(udp=['0.0.0.0:1']), 0)[0] is False
    assert d.feed(record(), 5)[0] is False                                    # outside the fusion window: forgotten
    fire, reason = d.feed(record(udp=['0.0.0.0:2'], audio=.6), 10)
    assert fire and 'audio_burst' in reason and 'udp_new_local' in reason
    assert d.feed(record(udp=['0.0.0.0:3'], audio=.7, log=50000), 11)[0] is False   # once per match
    assert d.feed(record(), 10 + ma.REARM_SECONDS + ma.QUIET_SECONDS)[0] is False
    d.previous_audio = 0.
    assert d.feed(record(udp=['0.0.0.0:4'], audio=.6), 10 + ma.REARM_SECONDS + ma.QUIET_SECONDS + 1)[0]   # the next match


def test_a_strong_signal_fires_alone_and_off_signals_are_ignored():
    d = ma.MatchFoundDetector(dict(lobby_screen='strong', udp_new_local='off'))
    d.feed(record(lobby=False), 0)
    assert d.feed(record(udp=['0.0.0.0:1']), 1)[0] is False
    assert d.feed(record(lobby=True), 2) == (True, 'strong signal lobby_screen')


def test_https_443_connections_alone_are_not_a_game_server():
    d = ma.MatchFoundDetector()
    assert d.signals(record(tcp=['3.218.34.13:443 ESTABLISHED'])) == []
    assert d.signals(record(tcp=['52.1.2.3:7777 ESTABLISHED'])) == ['tcp_new_remote']


def test_log_growth_survives_rotation_without_opening_the_file(tmp_path):
    log = tmp_path / 'DeadByDaylight.log'; log.write_bytes(b'x' * 100); watch = LogGrowth(tmp_path)
    assert watch.poll()['grew'] == 0
    with open(log, 'ab') as f: f.write(b'y' * 50)
    assert watch.poll()['grew'] == 50
    log.unlink(); time.sleep(.02); log.write_bytes(b'z' * 10)
    result = watch.poll(); assert result['rotated'] and result['grew'] == 10
    log.unlink(); assert watch.poll() == dict(found=False, grew=0, rotated=False)


def test_settings_are_off_by_default_and_cleaned():
    assert ma.default_settings()['enabled'] is False and ma.default_settings()['push'] is False and ma.default_settings()['background_only'] is True
    clean = ma.clean_settings(dict(enabled=True, volume=500, push_target=' t ', probe_minutes=0, background_only=False))
    assert clean['enabled'] and clean['volume'] == 70 and clean['push_target'] == 't' and clean['probe_minutes'] == 20 and clean['background_only'] is False


class FakeSampler:
    def __init__(self, records): self.records = list(records); self.notes = []; self.wait = None
    def open(self): pass
    def close(self): pass
    def sample(self):
        if not self.records: self.wait(10); return dict(pid=None)
        return self.records.pop(0)


@pytest.mark.parametrize('in_front,expected', [(False, 1), (True, 0)])
def test_controller_rings_only_when_dbd_is_not_in_front(qapp, in_front, expected):
    from src.gui.match_alert_page import MatchAlertController
    rings = []
    chime = type('C', (), dict(ring=lambda self, v: rings.append(v), acknowledge=lambda self: None))()
    records = [dict(pid=1, window=dict(foreground=in_front), **record(udp=['0.0.0.0:1'])),
               dict(pid=1, window=dict(foreground=in_front), **record(udp=['0.0.0.0:2'], audio=.8))]
    c = MatchAlertController('.', ma.default_settings(), sampler_factory=lambda: FakeSampler(records), chime=chime)
    stop = threading.Event(); thread = threading.Thread(target=c.watch, args=(stop,)); thread.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and len(rings) < expected:
        qapp.processEvents(); time.sleep(.02)
    stop.set(); thread.join(3); qapp.processEvents()
    assert len(rings) == expected


@pytest.fixture
def qapp():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])
