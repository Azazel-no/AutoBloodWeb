"""How the match overlays read the screen, and the J8 capture test modes (developer settings).

J8 (15.11): NVIDIA recording (HDR on) still stops ~1 s after it starts, with the recording-friendly mode on too,
exactly when the overlays capture the screen (map OCR at match start, the perks read after Tab). Hypothesis: our
GDI/MSS BitBlt bursts on the HDR fullscreen game, not our windows, make NVIDIA log "HDR state changed [1]->[0]".

Test modes (overlays only; Bloodweb automation always captures as before):
- 'normal'       captures and windows as usual;
- 'no_capture'   overlay windows still appear (a test cluster on each Tab release), but no overlay captures pixels;
- 'no_windows'   overlays capture and read as usual, but none of their windows is ever shown.
Capture method for the overlays:
- 'gdi'  MSS (BitBlt of the desktop), GDI fallback: the method used so far;
- 'wgc'  Windows.Graphics.Capture bound to the DBD window (no desktop BitBlt). The frame is requested as 8-bit
         BGRA, so Windows itself converts an HDR (FP16 scRGB) game to SDR before we see it; detection gets the same
         kind of pixels as from MSS. One session is shared by all overlays and reused while DBD's window is the same.
"""
import logging
import threading
import time

from src.core.screen_manager import ScreenError, WindowsBackend

log = logging.getLogger(__name__)
MODES = ('normal', 'no_capture', 'no_windows')
METHODS = ('gdi', 'wgc')
REUSE_SECONDS = .05      # several overlays asking within one frame get the same WGC frame
REPORT_SECONDS = 10.


class Policy:
    mode = 'normal'
    method = 'gdi'


POLICY = Policy()
_STATS = dict(count=0, since=time.monotonic(), refused=0)
_WGC = dict(capture=None, frame=None, stamp=0., window=None)
_LOCK = threading.Lock()


def configure(mode=None, method=None):
    """Called from the settings; every change is logged so CaptureCore.log can be matched to app.log."""
    if mode is not None: POLICY.mode = mode if mode in MODES else 'normal'
    if method is not None:
        POLICY.method = method if method in METHODS else 'gdi'
        if POLICY.method != 'wgc': close_wgc()
    log.info('Overlay capture: test mode %s, method %s', POLICY.mode, POLICY.method)


def windows_allowed():
    return POLICY.mode != 'no_windows'


def note(refused=False):
    """Per 10 s, how many overlay captures there were: their timing can be compared with NVIDIA's log."""
    now = time.monotonic()
    if refused: _STATS['refused'] += 1
    else: _STATS['count'] += 1
    if now - _STATS['since'] >= REPORT_SECONDS:
        if POLICY.mode != 'normal' or POLICY.method != 'gdi':
            log.info('Overlay captures in the last %.0f s: %d (%s), refused by the test mode: %d',
                     now - _STATS['since'], _STATS['count'], POLICY.method, _STATS['refused'])
        _STATS.update(count=0, refused=0, since=now)


def close_wgc():
    with _LOCK:
        capture = _WGC['capture']; _WGC.update(capture=None, frame=None, stamp=0., window=None)
    if capture is not None:
        try: capture.close()
        except Exception: log.exception('WGC overlay capture did not close cleanly')


class OverlayBackend(WindowsBackend):
    """The backend of every match overlay: the test mode and the capture method apply here only."""

    def grab(self, rect):
        if POLICY.mode == 'no_capture':
            note(refused=True); raise ScreenError('capture_disabled_for_test')
        note()
        if POLICY.method == 'wgc': return self.grab_wgc(rect)
        return super().grab(rect)

    def grab_wgc(self, rect, factory=None):
        window = self.info()
        with _LOCK:
            now = time.monotonic()
            if _WGC['window'] is None or _WGC['window'].signature != window.signature or _WGC['capture'] is None:
                old = _WGC['capture']
                if old is not None:
                    try: old.close()
                    except Exception: pass
                from src.core.window_probe_capture import WindowProbeCapture
                try: _WGC['capture'] = WindowProbeCapture(window, factory=factory, bounds=self.probe_bounds)
                except (ImportError, RuntimeError, OSError) as exc: raise ScreenError('wgc_unavailable', str(exc)) from exc
                _WGC.update(window=window, frame=None, stamp=0.)
                log.info('WGC overlay capture started on the DBD window (%dx%d)', window.client.width, window.client.height)
            if _WGC['frame'] is None or now - _WGC['stamp'] > REUSE_SECONDS:
                frame, _ = _WGC['capture'].grab(window, timeout=.5)
                _WGC.update(frame=frame, stamp=time.monotonic())
            frame = _WGC['frame']
        c = window.client; x, y = rect.x - c.x, rect.y - c.y
        if x < 0 or y < 0 or x + rect.width > frame.shape[1] or y + rect.height > frame.shape[0]:
            raise ScreenError('capture_outside_game', f'{rect} not in the client {c}')
        return frame[y:y + rect.height, x:x + rect.width].copy(), 'wgc'
