"""Audio for the meme overlays: thin wrappers over QtMultimedia with their own volume.

The volume is the meme volume, separate from the game's. QtMultimedia is imported on first use;
without it (a stripped build) the features keep working silently and say so once in the log.
"""
import logging

log = logging.getLogger(__name__)
_warned = False


def multimedia():
    """(QMediaPlayer, QAudioOutput, QUrl) or None when QtMultimedia is not available."""
    global _warned
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        return QMediaPlayer, QAudioOutput, QUrl
    except ImportError:
        if not _warned: _warned = True; log.warning('QtMultimedia is not available: the meme overlays stay silent')
        return None


class Sound:
    """One file on one player. position() and duration() are seconds."""
    def __init__(self, path, parent=None):
        self.path = path; self.volume = 1.; self.player = None; self.output = None
        api = multimedia()
        if api is None or path is None: return
        player_class, output_class, url = api
        self.player = player_class(parent); self.output = output_class(parent); self.player.setAudioOutput(self.output)
        self.player.setSource(url.fromLocalFile(str(path)))
        self.player.errorOccurred.connect(lambda error, text: log.warning('Meme audio %s: %s', getattr(path, 'name', path), text))

    def set_volume(self, volume):
        self.volume = max(0., min(1., float(volume)))
        if self.output: self.output.setVolume(self.volume)

    def play(self):
        """Always from the beginning."""
        if self.player: self.player.stop(); self.player.setPosition(0); self.output.setVolume(self.volume); self.player.play()

    def stop(self):
        if self.player: self.player.stop()

    def playing(self):
        return bool(self.player) and self.player.playbackState() == self.player.PlaybackState.PlayingState

    def position(self):
        return self.player.position() / 1000 if self.player else 0.

    def duration(self):
        return self.player.duration() / 1000 if self.player else 0.

    def close(self):
        if self.player: self.player.stop(); self.player.deleteLater(); self.output.deleteLater(); self.player = self.output = None


class LoopSound(Sound):
    """A short sound that repeats until stopped (the siren)."""
    def __init__(self, path, parent=None):
        super().__init__(path, parent)
        if self.player: self.player.setLoops(self.player.Loops.Infinite)
