"""The person detector model for the Sable Detector: YOLOX-tiny, downloaded on first use.

YOLOX is Apache-2.0 (Megvii Inc., https://github.com/Megvii-BaseDetection/YOLOX); see
THIRD_PARTY_NOTICES.md. The 20 MB file is not in the repository: it is fetched once from the
project's own GitHub release over HTTPS, checked against a fixed SHA-256 and kept in data/models.
"""
import hashlib
import logging
import threading
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)
NAME = 'yolox_tiny.onnx'
URL = 'https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx'
SHA256 = '427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7'
SIZE = 20219662
HOSTS = ('github.com', 'githubusercontent.com')   # the release URL redirects to GitHub's asset storage


def model_path(root):
    return Path(root) / 'data/models' / NAME


def trusted(url):
    parts = urlsplit(url); host = parts.hostname or ''
    return parts.scheme == 'https' and any(host == h or host.endswith('.' + h) for h in HOSTS)


def cached(root):
    """Path of the verified local copy, or None."""
    path = model_path(root)
    try:
        return path if path.is_file() and path.stat().st_size == SIZE and hashlib.sha256(path.read_bytes()).hexdigest() == SHA256 else None
    except OSError:
        return None


def download(root, opener=urlopen):
    """Blocking. Returns the cached path; raises on a foreign host, a wrong size or a wrong hash."""
    if not trusted(URL): raise ValueError('foreign_source')
    with opener(Request(URL, headers={'User-Agent': 'AutoBloodWeb'}), timeout=60) as response:
        if not trusted(response.url): raise ValueError('foreign_redirect')
        body = response.read(SIZE + 1)
    if len(body) != SIZE: raise ValueError('model_size_mismatch')
    if hashlib.sha256(body).hexdigest() != SHA256: raise ValueError('model_sha256_mismatch')
    path = model_path(root); path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix('.part'); part.write_bytes(body); part.replace(path)
    return path


class ModelFetcher:
    """One background download; `done(path or None)` is called from its thread."""
    def __init__(self, root, done, opener=urlopen):
        self.root, self.done, self.opener = root, done, opener; self.busy = False; self.failed = False; self.lock = threading.Lock()

    def request(self):
        """The model path when it is ready; otherwise None, and a download is started once."""
        path = cached(self.root)
        if path: return path
        with self.lock:
            if self.busy or self.failed: return None
            self.busy = True
        threading.Thread(target=self.run, name='SableModel', daemon=True).start()
        return None

    def run(self):
        path = None
        try: path = download(self.root, self.opener); log.info('Sable Detector: person model downloaded (%s)', NAME)
        except Exception as exc:
            self.failed = True; log.warning('Sable Detector: person model download failed: %s', exc)
        finally:
            self.busy = False; self.done(path)
