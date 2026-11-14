"""F2: the pack builder's own logic (no ffmpeg, Demucs or real media needed) and the repository rules."""
import subprocess

import numpy as np

from scripts import build_meme_pack as build
from tests.conftest import ROOT

RATE = build.RATE


def tone(seconds, level=.5, frequency=220.):
    t = np.arange(int(seconds * RATE)) / RATE
    return (np.sin(2 * np.pi * frequency * t) * level).astype(np.float32)[:, None].repeat(2, axis=1)


def test_voice_folders_take_the_weight_from_the_trailing_digit(tmp_path):
    for name in ('chuckles3', 'mambaout2', 'dontpassit1', 'dance', 'music+timer', 'sable photos'):
        (tmp_path / name).mkdir(); (tmp_path / name / f'{name}-1.mp4').write_bytes(b'x')
    (tmp_path / 'empty2').mkdir()
    found = dict(build.voice_folders(tmp_path))
    assert set(found) == {'chuckles3', 'mambaout2', 'dontpassit1'}
    assert [build.meme_pack.weight_of(n) for n in ('chuckles3', 'mambaout2', 'dontpassit1')] == [3, 2, 1]


def test_silence_is_trimmed_and_edges_are_faded():
    silence = np.zeros((RATE // 2, 2), np.float32); data = np.concatenate([silence, tone(1.), silence])
    cut = build.trim_silence(data)
    assert .95 < len(cut) / RATE < 1.15
    faded = build.fade_edges(cut)
    assert faded[0, 0] == 0. and faded[-1, 0] == 0. and abs(faded[len(faded) // 2]).max() > 0


def test_loop_check_flags_a_click_only_when_the_ends_do_not_meet():
    quiet_ends = build.fade_edges(tone(3.), .2)
    assert build.loop_check(quiet_ends)['clicks_without_crossfade'] is False
    cut_mid_wave = tone(3.)[:int(2.501 * RATE) + 37]       # Ends at a high sample, starts at zero.
    cut_mid_wave[-1] = .5
    check = build.loop_check(cut_mid_wave)
    assert check['clicks_without_crossfade'] is True and check['jump'] > .02 and check['crossfade'] > 0


def test_separation_share():
    voice, music = tone(1., .5, 220.), tone(1., .25, 880.)
    assert build.music_share(voice + music, voice) > .15 and build.music_share(voice, voice) == 0.


def test_green_screen_is_keyed_and_despilled():
    frame = np.zeros((120, 160, 3), np.uint8); frame[:] = (0, 255, 40)            # BGR green screen
    frame[30:90, 50:110] = (200, 60, 150)                                        # the dancer: purple
    frame[100:104, 150:154] = (0, 0, 0)                                          # a stray speck
    background = 255. - 40.
    keyed = build.key_green(frame, background); keyed[..., 3] = build.keep_main_blobs(keyed[..., 3])
    assert keyed[5, 5, 3] == 0 and keyed[60, 80, 3] == 255                        # screen out, dancer in
    assert keyed[102, 152, 3] == 0                                                # the speck is dropped
    assert tuple(keyed[60, 80, :3]) == (200, 60, 150)                             # colours inside are untouched
    edge = keyed[30:90, 48:52]; assert (edge[..., 1].astype(int) <= np.maximum(edge[..., 0], edge[..., 2]).astype(int) + 1).all()   # no green fringe


def test_meme_media_is_ignored_and_the_builder_is_published():
    def ignored(path):
        return subprocess.run(['git', 'check-ignore', '-q', path], cwd=ROOT).returncode == 0
    assert ignored('assets/mamba/siren.mp3') and ignored('assets/mamba/dance/dance-1.mp4') and ignored('assets/anything/new.mp3')
    assert not ignored('scripts/build_meme_pack.py') and not ignored('src/core/meme_pack.py')
    tracked = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True).stdout.lower().splitlines()
    assert not [p for p in tracked if p.endswith(('.mp3', '.mp4', '.webm', '.wav')) or 'mamba' in p]
