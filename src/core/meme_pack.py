"""Meme pack: the user's own media for the Memes tab, kept outside the app and the repository.

The media (voice lines, music, dance clips, photos, siren) is third-party material that the
user supplies; AutoBloodWeb ships none of it. The pack lives in %APPDATA%/AutoBloodWeb/meme_pack
and is read from disk: pack.json when present (scripts/build_meme_pack.py writes it), otherwise
the folders are scanned, so files dropped in by hand work too.

Layout:
    voice/<name><weight>.mp3     voice lines; the trailing digit is the weight (3 common .. 1 rare)
    voice_original/              the same lines before clean-up; never played, for comparison
    music/chase.mp3              chase music, loops
    dance/<name>/0001.webp ...   dance clip: transparent frames in order, plus info.json with the fps
    dance/<name>.mp3             its sound
    sable/photos/*.png|jpg       Sable Detector photos
    sable/siren.mp3              Sable Detector siren, loops
    sable/library.npz            Sable outfit pieces for the detector (built from the user's footage)
"""
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)
ENV = 'AUTOBLOODWEB_MEME_PACK'
AUDIO = ('.mp3', '.wav', '.ogg')
IMAGES = ('.png', '.jpg', '.jpeg', '.webp')
FRAMES = ('.webp', '.png')
DEFAULT_FPS = 30.
FOLDERS = ('voice', 'voice_original', 'music', 'dance', 'sable/photos')
README = '''AutoBloodWeb meme pack
======================

Put your own media here. AutoBloodWeb ships no meme media and never uploads this folder.

voice/            Voice lines (mp3). A digit at the end of the name is the weight:
                  whatcanisay3.mp3 plays most often, mambaout2.mp3 less, dontpassit1.mp3 rarely.
voice_original/   The same lines before clean-up. Never played; only for comparing by ear.
music/            chase.mp3 - the chase music. It loops while the chase lasts.
dance/            Dance clips: a folder <name>/ of transparent frames in order (0001.webp,
                  0002.webp ... or .png) with info.json {"fps": 30}, and <name>.mp3 with its
                  sound. scripts/build_meme_pack.py makes them from green-screen clips.
sable/photos/     Photos for the Sable Detector (png, jpg).
sable/siren.mp3   The siren for the Sable Detector. It loops.
sable/library.npz Outfit pieces for the Sable Detector (built from your recordings).
pack.json         Written by scripts/build_meme_pack.py: files, weights, durations.
                  Without it the folders above are scanned.

Папка с вашими медиафайлами для вкладки «Мемы». В программу они не входят и никуда не
отправляются. Цифра в конце имени реплики - её вес (3 - часто, 1 - редко).
'''


def pack_dir(base=None):
    """The meme pack folder: an explicit base (tests), the override variable, else %APPDATA%."""
    if base is not None: return Path(base)
    if os.environ.get(ENV): return Path(os.environ[ENV])
    return Path(os.environ.get('APPDATA') or Path.home() / 'AppData/Roaming') / 'AutoBloodWeb' / 'meme_pack'


def ensure(folder):
    """Creates the folder tree and its README; never touches existing media."""
    folder = Path(folder)
    for name in FOLDERS: (folder / name).mkdir(parents=True, exist_ok=True)
    readme = folder / 'README.txt'
    if not readme.is_file() or readme.read_text(encoding='utf-8', errors='replace') != README:
        readme.write_text(README, encoding='utf-8')
    return folder


def weight_of(name):
    """Trailing digit of a file or folder name: 'whatcanisay3' -> 3; none -> 1; clamped to 1..3."""
    match = re.search(r'(\d)(?:-\d+)?$', Path(name).stem)
    return min(3, max(1, int(match.group(1)))) if match else 1


@dataclass(frozen=True)
class Voice:
    name: str
    path: Path
    weight: int = 1
    duration: float = 0.


@dataclass(frozen=True)
class Dance:
    name: str
    frames: tuple          # frame files in playing order
    fps: float = DEFAULT_FPS
    audio: Path | None = None

    @property
    def duration(self):
        return len(self.frames) / self.fps


@dataclass
class Pack:
    folder: Path
    voices: list = field(default_factory=list)
    music: Path | None = None
    music_duration: float = 0.
    dances: list = field(default_factory=list)
    photos: list = field(default_factory=list)
    siren: Path | None = None
    library: Path | None = None

    @property
    def chase_ready(self):
        """The chase meme needs at least the music or one voice line."""
        return bool(self.music or self.voices)

    @property
    def sable_ready(self):
        return bool(self.photos or self.siren)

    @property
    def empty(self):
        return not (self.chase_ready or self.sable_ready or self.dances)

    def counts(self):
        return dict(voices=len(self.voices), music=int(bool(self.music)), dances=len(self.dances), photos=len(self.photos), siren=int(bool(self.siren)))


def _files(folder, extensions):
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in extensions) if folder.is_dir() else []


def _inside(folder, relative):
    """A pack.json path, only if it stays inside the pack and exists."""
    if not isinstance(relative, str): return None
    path = (folder / relative).resolve()
    return path if path.is_file() and folder.resolve() in path.parents else None


def load(folder):
    """Reads the pack. A missing or broken pack is an empty Pack, never an error."""
    folder = Path(folder); pack = Pack(folder)
    if not folder.is_dir(): return pack
    data = {}
    try:
        if (folder / 'pack.json').is_file(): data = json.loads((folder / 'pack.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        log.warning('Meme pack: pack.json is unreadable, scanning the folders instead')
    if not isinstance(data, dict): data = {}
    listed = {}
    for entry in data.get('voices') or []:
        path = _inside(folder, entry.get('file')) if isinstance(entry, dict) else None
        if path: listed[path] = entry
    for path in _files(folder / 'voice', AUDIO):
        entry = listed.get(path.resolve(), {})
        weight = entry.get('weight'); duration = entry.get('duration')
        pack.voices.append(Voice(path.stem, path, weight if weight in (1, 2, 3) else weight_of(path.stem), float(duration) if isinstance(duration, (int, float)) else 0.))
    music = data.get('music') if isinstance(data.get('music'), dict) else {}
    pack.music = _inside(folder, music.get('file')) or next(iter(_files(folder / 'music', AUDIO)), None)
    pack.music_duration = float(music['duration']) if isinstance(music.get('duration'), (int, float)) else 0.
    for clip in sorted(p for p in (folder / 'dance').iterdir() if p.is_dir()) if (folder / 'dance').is_dir() else []:
        frames = tuple(_files(clip, FRAMES))
        if not frames: continue
        fps = DEFAULT_FPS
        try:
            value = json.loads((clip / 'info.json').read_text(encoding='utf-8')).get('fps')
            if isinstance(value, (int, float)) and 1 <= value <= 120: fps = float(value)
        except (OSError, ValueError, AttributeError): pass
        audio = next((clip.parent / (clip.name + ext) for ext in AUDIO if (clip.parent / (clip.name + ext)).is_file()), None)
        pack.dances.append(Dance(clip.name, frames, fps, audio))
    pack.photos = _files(folder / 'sable/photos', IMAGES)
    pack.siren = next((folder / 'sable' / ('siren' + ext) for ext in AUDIO if (folder / 'sable' / ('siren' + ext)).is_file()), None)
    pack.library = folder / 'sable/library.npz' if (folder / 'sable/library.npz').is_file() else None
    return pack
