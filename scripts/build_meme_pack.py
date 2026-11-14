"""Builds the local meme pack from the user's own media (default: assets/mamba).

A developer tool. It is never bundled with the app and its output never goes into the
repository or a release: the media is third-party material that only the user holds.
The result is installed into the meme pack folder (%APPDATA%/AutoBloodWeb/meme_pack).

Run it with the tools environment, which holds Demucs, ffmpeg (imageio-ffmpeg), soundfile,
NumPy and OpenCV; none of these are dependencies of the app itself:

    uv venv build/meme_tools --python 3.12
    uv pip install --python build/meme_tools/Scripts/python.exe demucs imageio-ffmpeg soundfile numpy \
        opencv-python-headless torch torchaudio --extra-index-url https://download.pytorch.org/whl/cpu
    build/meme_tools/Scripts/python.exe scripts/build_meme_pack.py

Steps:
  1. Voice lines: every folder named <word><digit> with a video inside. The audio goes to
     voice_original/<folder>.mp3 untouched; a cleaned copy (Demucs vocals, silence trimmed,
     loudness normalised) goes to voice/<folder>.mp3. Mono, 128 kbps. Weight = the digit.
  2. Music: the full track of 'music+timer/*.mp4' to music/chase.mp3, with a loop check.
  3. Dance clips: green screen keyed out, cropped, saved as transparent WebP frames.
  4. Photos and the siren are copied.
  5. pack.json lists the files, weights and durations.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.core import meme_pack  # noqa: E402  (standard library only)

RATE = 44100
VOICE_LUFS = -16.        # target loudness of a cleaned voice line
PEAK_DB = -1.            # no sample above this after the gain
MUSIC_LUFS = -18.        # the music sits under the voice
DANCE_HEIGHT = 540       # frames are scaled down to this height at most
LOOP_CROSSFADE = .35     # seconds the player overlaps the end of the music with its start


def ffmpeg_exe():
    found = shutil.which('ffmpeg')
    if found: return found
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run(args, **kwargs):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True, encoding='utf-8', errors='replace', **kwargs)
    if result.returncode: raise RuntimeError(f'{Path(str(args[0])).name} failed:\n{result.stderr[-1500:]}')
    return result


def to_wav(ff, source, target, channels=2):
    run([ff, '-y', '-loglevel', 'error', '-i', source, '-vn', '-ac', channels, '-ar', RATE, '-c:a', 'pcm_s16le', target])


def to_mp3(ff, source, target, channels=1, filters=None):
    args = [ff, '-y', '-loglevel', 'error', '-i', source, '-vn', '-ac', channels, '-ar', RATE]
    if filters: args += ['-af', filters]
    run(args + ['-c:a', 'libmp3lame', '-b:a', '128k', target])


def read_wav(path):
    import soundfile
    data, rate = soundfile.read(str(path), dtype='float32', always_2d=True)
    assert rate == RATE, (path, rate)
    return data


def write_wav(path, data):
    import soundfile
    soundfile.write(str(path), np.clip(data, -1., 1.), RATE, subtype='PCM_16')


def db(value):
    return 20 * np.log10(max(float(value), 1e-9))


def loudness(ff, wav):
    """Integrated loudness (EBU R128, LUFS) measured by ffmpeg; None when the clip is too short or silent."""
    result = subprocess.run([ff, '-hide_banner', '-nostats', '-i', str(wav), '-af', 'loudnorm=print_format=json', '-f', 'null', '-'],
                            capture_output=True, text=True, encoding='utf-8', errors='replace')
    match = re.search(r'\{[^{}]*"input_i"[^{}]*\}', result.stderr)
    try: value = float(json.loads(match.group(0))['input_i']) if match else None
    except ValueError: value = None
    return value if value is not None and np.isfinite(value) and value > -60 else None


def envelope(mono, window=.02):
    """RMS in 20 ms steps."""
    size = int(RATE * window); count = len(mono) // size
    return np.sqrt((mono[:count * size].reshape(count, size) ** 2).mean(axis=1) + 1e-12), size


def trim_silence(data, threshold_db=-42., pad=.04):
    """Cuts the quiet head and tail; the threshold is relative to the loudest 20 ms."""
    env, size = envelope(data.mean(axis=1))
    if not len(env): return data
    loud = np.flatnonzero(env > env.max() * 10 ** (threshold_db / 20))
    if not len(loud): return data
    start = max(0, loud[0] * size - int(pad * RATE)); end = min(len(data), (loud[-1] + 1) * size + int(pad * RATE))
    return data[start:end]


def fade_edges(data, seconds=.008):
    n = min(int(seconds * RATE), len(data) // 2)
    if n:
        ramp = np.linspace(0., 1., n, dtype=np.float32)[:, None]
        data = data.copy(); data[:n] *= ramp; data[-n:] *= ramp[::-1]
    return data


def normalise(ff, data, target_lufs, work):
    """Linear gain to the target loudness, limited so the peak stays under PEAK_DB. Short clips fall back to RMS."""
    probe = work / 'loudness_probe.wav'; write_wav(probe, data)
    measured = loudness(ff, probe)
    if measured is None: measured = db(np.sqrt((data ** 2).mean())) + 3.   # RMS of a voice line reads about 3 dB under its LUFS.
    gain = target_lufs - measured
    gain = min(gain, PEAK_DB - db(np.abs(data).max()))
    return data * 10 ** (gain / 20), measured, gain


# --- 1. voice ---------------------------------------------------------------------------------
def voice_folders(source):
    for folder in sorted(source.iterdir()):
        if folder.is_dir() and re.fullmatch(r'[a-z]+\d', folder.name):
            videos = sorted(folder.glob('*.mp4'))
            if videos: yield folder.name, videos[0]


def separate(wavs, work, model='htdemucs'):
    """Demucs, two stems: vocals and everything else. Returns {stem name: vocals.wav}."""
    out = work / 'demucs'
    run([sys.executable, '-m', 'demucs', '--two-stems', 'vocals', '-n', model, '-o', out, *wavs])
    return {wav.stem: out / model / wav.stem / 'vocals.wav' for wav in wavs}


def music_share(original, vocals):
    """How much of the original's energy the separation removed (0 = nothing, 1 = everything)."""
    n = min(len(original), len(vocals)); a = original[:n].mean(axis=1); b = vocals[:n].mean(axis=1)
    return float(((a - b) ** 2).sum() / max((a ** 2).sum(), 1e-9))


def build_voices(ff, source, out, work, separation=True):
    lines = list(voice_folders(source)); wavs = []
    for name, video in lines:
        wav = work / f'{name}.wav'; to_wav(ff, video, wav); wavs.append(wav)
        to_mp3(ff, wav, out / 'voice_original' / f'{name}.mp3')
    stems = separate(wavs, work) if separation and wavs else {}
    entries = []
    for (name, video), wav in zip(lines, wavs):
        original = read_wav(wav); vocals = read_wav(stems[name]) if name in stems else original
        removed = music_share(original, vocals) if name in stems else 0.
        clean = fade_edges(trim_silence(vocals)); clean, measured, gain = normalise(ff, clean, VOICE_LUFS, work)
        cleaned = work / f'{name}_clean.wav'; write_wav(cleaned, clean); to_mp3(ff, cleaned, out / 'voice' / f'{name}.mp3')
        entry = dict(name=name, file=f'voice/{name}.mp3', original=f'voice_original/{name}.mp3', weight=meme_pack.weight_of(name),
                     duration=round(len(clean) / RATE, 3), original_duration=round(len(original) / RATE, 3),
                     separated=name in stems, removed_share=round(removed, 3), measured_lufs=round(measured, 1), gain_db=round(gain, 1))
        entries.append(entry)
        print(f"  voice {name:<14} weight {entry['weight']}  {entry['original_duration']:.2f} s -> {entry['duration']:.2f} s, removed {removed:.0%} of the energy, gain {gain:+.1f} dB")
    return entries


# --- 2. music ---------------------------------------------------------------------------------
def loop_check(data, crossfade=LOOP_CROSSFADE):
    """Numbers that describe the loop point: a jump between the last and first sample is a click."""
    mono = data.mean(axis=1); n = int(.05 * RATE); body = np.sqrt((mono ** 2).mean())
    head, tail = np.sqrt((mono[:n] ** 2).mean()), np.sqrt((mono[-n:] ** 2).mean())
    jump = abs(float(mono[0]) - float(mono[-1]))
    return dict(jump=round(jump, 4), head_db=round(db(head), 1), tail_db=round(db(tail), 1), body_db=round(db(body), 1),
                clicks_without_crossfade=bool(jump > .02 or abs(db(head) - db(tail)) > 12 and max(db(head), db(tail)) > db(body) - 30),
                crossfade=crossfade)


def build_music(ff, source, out, work):
    videos = sorted((source / 'music+timer').glob('*.mp4'))
    if not videos: return None
    wav = work / 'music.wav'; to_wav(ff, videos[0], wav); data = read_wav(wav); full = len(data) / RATE
    data = trim_silence(data, threshold_db=-50., pad=.0)
    data, measured, gain = normalise(ff, data, MUSIC_LUFS, work); check = loop_check(data)
    # The player overlaps the last `crossfade` seconds with the start, so the file itself only needs clean edges.
    data = fade_edges(data, .004); trimmed = work / 'music_trimmed.wav'; write_wav(trimmed, data)
    to_mp3(ff, trimmed, out / 'music' / 'chase.mp3', channels=2)
    entry = dict(file='music/chase.mp3', duration=round(len(data) / RATE, 3), source_duration=round(full, 3), loop_crossfade=check['crossfade'],
                 loop_check=check, measured_lufs=round(measured, 1), gain_db=round(gain, 1))
    print(f"  music {entry['duration']:.1f} s (source {full:.1f} s), loop point: jump {check['jump']}, head {check['head_db']} dB, tail {check['tail_db']} dB, "
          f"clicks without crossfade: {check['clicks_without_crossfade']}; the player crossfades {check['crossfade']} s")
    return entry


# --- 3. dance ---------------------------------------------------------------------------------
def key_green(frame, background):
    """BGR frame -> BGRA: alpha from how much greener than red and blue a pixel is, green spill removed."""
    import cv2
    f = frame.astype(np.float32); b, g, r = f[..., 0], f[..., 1], f[..., 2]
    dominance = g - np.maximum(r, b)
    alpha = 1. - np.clip((dominance - background * .18) / (background * .42), 0., 1.)
    alpha = cv2.erode(alpha, np.ones((3, 3), np.uint8)); alpha = cv2.GaussianBlur(alpha, (0, 0), .8)
    spill = np.minimum(g, np.maximum(r, b))   # A keyed edge keeps no green cast.
    out = np.dstack([b, spill, r, alpha * 255.])
    return np.clip(out, 0, 255).astype(np.uint8)


def keep_main_blobs(alpha, share=.02):
    """Drops specks that are not part of the dancer (logo corners, compression noise)."""
    import cv2
    count, labels, stats, _ = cv2.connectedComponentsWithStats((alpha > 96).astype(np.uint8), connectivity=8)
    if count <= 1: return alpha
    areas = stats[1:, cv2.CC_STAT_AREA]; keep = np.flatnonzero(areas >= areas.max() * share) + 1
    mask = cv2.dilate(np.isin(labels, keep).astype(np.uint8), np.ones((9, 9), np.uint8))
    return alpha * mask


def build_dance(ff, video, out, work):
    import cv2
    name = video.stem; capture = cv2.VideoCapture(str(video)); fps = capture.get(cv2.CAP_PROP_FPS) or 30.; frames = []
    background = None
    while True:
        ok, frame = capture.read()
        if not ok: break
        if background is None:   # The key colour's green dominance, from the frame border.
            f = frame.astype(np.float32); border = np.concatenate([f[:8].reshape(-1, 3), f[-8:].reshape(-1, 3), f[:, :8].reshape(-1, 3), f[:, -8:].reshape(-1, 3)])
            background = float(np.median(border[:, 1] - np.maximum(border[:, 0], border[:, 2])))
            if background < 40: raise RuntimeError(f'{video.name}: the border is not a green screen (dominance {background:.0f})')
        keyed = key_green(frame, background); keyed[..., 3] = keep_main_blobs(keyed[..., 3]); frames.append(keyed)
    capture.release()
    if not frames: raise RuntimeError(f'{video.name}: no frames')
    # Where the dancer is in at least 5 % of the frames: something that flies across for a moment must not widen the crop.
    cover = np.mean([f[..., 3] > 96 for f in frames], axis=0) >= .05; ys, xs = np.flatnonzero(cover.any(axis=1)), np.flatnonzero(cover.any(axis=0))
    if not len(ys): raise RuntimeError(f'{video.name}: nothing is left after keying')
    pad = 6; h, w = cover.shape
    y0, y1, x0, x1 = max(0, ys[0] - pad), min(h, ys[-1] + 1 + pad), max(0, xs[0] - pad), min(w, xs[-1] + 1 + pad)
    scale = min(1., DANCE_HEIGHT / (y1 - y0)); size = (max(2, round((x1 - x0) * scale)), max(2, round((y1 - y0) * scale)))
    folder = out / 'dance' / name
    if folder.is_dir(): shutil.rmtree(folder)
    folder.mkdir(parents=True)
    for index, frame in enumerate(frames, start=1):
        crop = cv2.resize(frame[y0:y1, x0:x1], size, interpolation=cv2.INTER_AREA)
        ok, data = cv2.imencode('.webp', crop, [cv2.IMWRITE_WEBP_QUALITY, 84])
        if not ok: raise RuntimeError(f'{name}: frame {index} could not be encoded')
        (folder / f'{index:04d}.webp').write_bytes(data.tobytes())
    (folder / 'info.json').write_text(json.dumps(dict(fps=round(fps, 3), frames=len(frames), width=size[0], height=size[1])), encoding='utf-8')
    wav = work / f'{name}.wav'; audio = None
    try:
        to_wav(ff, video, wav); level = db(np.abs(read_wav(wav)).max())
    except RuntimeError: level = -120.
    target = out / 'dance' / f'{name}.mp3'
    if level > -50:
        to_mp3(ff, wav, target, channels=2); audio = f'dance/{name}.mp3'
    elif target.exists(): target.unlink()
    megabytes = sum(p.stat().st_size for p in folder.iterdir()) / 1e6
    print(f"  dance {name}: {len(frames)} frames at {fps:.0f} fps, {size[0]}x{size[1]}, {megabytes:.1f} MB, " + ('with sound' if audio else f'no sound (the clip is silent, peak {level:.0f} dB)'))
    return dict(name=name, folder=f'dance/{name}', frames=len(frames), fps=round(fps, 3), duration=round(len(frames) / fps, 3), width=size[0], height=size[1], audio=audio, source_peak_db=round(level, 1))


# --- 4. photos, siren -------------------------------------------------------------------------
def copy_sable(source, out):
    photos = []
    for index, photo in enumerate(sorted(p for p in (source / 'sable photos').iterdir() if p.suffix.lower() in meme_pack.IMAGES) if (source / 'sable photos').is_dir() else [], start=1):
        target = out / 'sable/photos' / f'sable-{index}{photo.suffix.lower()}'; shutil.copyfile(photo, target); photos.append(f'sable/photos/{target.name}')
    siren = None
    if (source / 'siren.mp3').is_file(): shutil.copyfile(source / 'siren.mp3', out / 'sable/siren.mp3'); siren = 'sable/siren.mp3'
    print(f'  sable: {len(photos)} photos, siren {"copied" if siren else "missing"}')
    return photos, siren


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--source', type=Path, default=ROOT / 'assets/mamba')
    parser.add_argument('--out', type=Path, default=None, help='pack folder; default: %%APPDATA%%/AutoBloodWeb/meme_pack')
    parser.add_argument('--no-separation', action='store_true', help='skip Demucs: the cleaned lines are only trimmed and normalised')
    parser.add_argument('--only', choices=('voice', 'music', 'dance', 'sable'), action='append', help='rebuild only these parts (the rest of pack.json is kept)')
    args = parser.parse_args()
    source = args.source.resolve(); out = meme_pack.ensure(meme_pack.pack_dir(args.out)); ff = ffmpeg_exe()
    if ROOT in out.resolve().parents: sys.exit('The meme pack must stay outside the repository.')
    parts = set(args.only or ('voice', 'music', 'dance', 'sable'))
    try: pack = json.loads((out / 'pack.json').read_text(encoding='utf-8'))
    except (OSError, ValueError): pack = {}
    print(f'Source: {source}\nPack:   {out}')
    with tempfile.TemporaryDirectory(prefix='meme_pack_') as temporary:
        work = Path(temporary)
        if 'voice' in parts: pack['voices'] = build_voices(ff, source, out, work, not args.no_separation)
        if 'music' in parts: pack['music'] = build_music(ff, source, out, work)
        if 'dance' in parts: pack['dances'] = [build_dance(ff, video, out, work) for video in sorted((source / 'dance').glob('*.mp4'))]
        if 'sable' in parts: pack['photos'], pack['siren'] = copy_sable(source, out)
    pack.update(version=1, built_at=time.strftime('%Y-%m-%d %H:%M:%S'), source=source.name)
    (out / 'pack.json').write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'pack.json written: {len(pack.get("voices") or [])} voice lines, {len(pack.get("dances") or [])} dances, {len(pack.get("photos") or [])} photos')


if __name__ == '__main__':
    main()
