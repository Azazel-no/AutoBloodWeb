"""Map callout images by Hens333: downloaded on first use from the public source, cached locally.

The images are not in this repository: no licence allows redistributing them. Each one is fetched
once from the generated-pictures branch that dbd-map-overlay itself downloads from, checked
against the catalog's MD5 and kept in data/map_cache. Only HTTPS to raw.githubusercontent.com.
"""
import hashlib
import logging
import threading
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)
HOST = 'raw.githubusercontent.com'
LIMIT = 8 * 1024 * 1024
CREDIT = 'Map callouts by Hens333'


def cache_path(root, m):
    return Path(root) / 'data/map_cache' / Path(m.image).relative_to('maps')


def cached(root, m):
    """Path of a verified local copy, or None."""
    path = cache_path(root, m)
    try:
        return path if path.is_file() and hashlib.md5(path.read_bytes()).hexdigest() == m.md5 else None
    except OSError:
        return None


def url(base, m):
    return base + quote(m.image)


def download(root, base, m, opener=urlopen):
    """Blocking. Returns the cached path; raises on a foreign host, a size or an MD5 mismatch."""
    target = url(base, m)
    if urlsplit(target).scheme != 'https' or urlsplit(target).hostname != HOST: raise ValueError('foreign_source')
    with opener(Request(target, headers={'User-Agent': 'AutoBloodWeb (map overlay)'}), timeout=20) as response:
        if urlsplit(response.url).hostname != HOST: raise ValueError('foreign_redirect')
        body = response.read(LIMIT + 1)
    if len(body) > LIMIT: raise ValueError('map_image_too_large')
    if hashlib.md5(body).hexdigest() != m.md5: raise ValueError('map_image_md5_mismatch')
    path = cache_path(root, m); path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + '.part'); part.write_bytes(body); part.replace(path)
    return path


class Fetcher:
    """One background download at a time; `done(map, path or None)` is called from its thread."""
    def __init__(self, root, base, done, opener=urlopen):
        self.root, self.base, self.done, self.opener = root, base, done, opener
        self.busy = set(); self.failed = set(); self.lock = threading.Lock()

    def request(self, m):
        path = cached(self.root, m)
        if path: return path
        with self.lock:
            if m.key in self.busy or m.key in self.failed: return None
            self.busy.add(m.key)
        threading.Thread(target=self.run, args=(m,), name='MapImage', daemon=True).start()
        return None

    def run(self, m):
        path = None
        try:
            path = download(self.root, self.base, m, self.opener); log.info('Map image cached: %s', m.key)
        except Exception as exc:
            with self.lock: self.failed.add(m.key)   # No retry loop: the next app start tries again.
            log.warning('Map image download failed for %s: %s', m.key, exc)
        finally:
            with self.lock: self.busy.discard(m.key)
        self.done(m, path)
