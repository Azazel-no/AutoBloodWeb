"""Match watch for the meme overlays: where DBD is, its HUD scale, whether a match runs and the role.

The same checks the 1v1 timer and the DS timer use (nickname column visible, own perk diamonds
recognised, HUD scale fitted once), kept in one object so the chase meme and the Sable Detector
share them. Screen capture only.
"""
import logging
import time

from src.core.killer_status import HUD_GONE_SECONDS, column_rect, read_column
from src.core.match_reader import fit_hud, load_layout, with_hud_fit
from src.core.own_perks import load_matchers, own_rect, read_own
from src.core.screen_manager import Rect, WindowsBackend

log = logging.getLogger(__name__)
ROLE_EVERY = 10      # polls between the costly own-perk reads once the role is known


class MatchWatch:
    def __init__(self, root, backend=None, is_killer=lambda: False, clock=time.monotonic, role_reader=None, name='meme'):
        self.root = root; self.backend = backend or WindowsBackend(); self.is_killer = is_killer; self.clock = clock
        self.role_reader = role_reader; self.name = name; self.matchers = None
        self.layout = self.base_layout = load_layout(root); self.hud_scale = 1.; self.hud_fitted = False; self.fit_polls = 0
        self.game_hwnd = None; self.client = None; self.foreground = False
        self.in_match = False; self.hud_visible = False; self.hud_seen_at = None; self.role = None; self.polls = 0

    def reset(self):
        self.in_match = False; self.hud_visible = False; self.hud_seen_at = None; self.role = None
        self.layout = self.base_layout; self.hud_scale = 1.; self.hud_fitted = False

    def lost(self):
        """The game window is gone."""
        self.game_hwnd = None; self.client = None; self.foreground = False; self.reset()

    def current_role(self):
        return self.role or ('killer' if self.is_killer() else 'survivor')

    @property
    def survivor_match(self):
        return self.in_match and self.current_role() == 'survivor'

    def poll(self):
        """One look at the HUD. Raises ScreenError when DBD is not found."""
        window = self.backend.info(); self.game_hwnd = window.hwnd; self.client = window.client; self.foreground = bool(window.foreground)
        if not self.foreground: self.hud_visible = False; return
        self.polls += 1; client = window.client; now = self.clock()
        visible = self.column_visible(client)
        if not visible and not self.hud_fitted:
            self.fit_polls = (self.fit_polls + 1) % 3
            if self.fit_polls == 1 and self.try_fit(client): visible = self.column_visible(client)
        present = False
        if visible:
            present, role = self.read_role(client)
            if present and role: self.role = role
        self.hud_visible = bool(visible and present)
        if self.hud_visible:
            if not self.in_match: log.info('%s: match HUD found (%s, HUD scale %.2f)', self.name, self.current_role(), self.hud_scale)
            self.in_match = True; self.hud_seen_at = now
        elif self.hud_seen_at is not None and now - self.hud_seen_at > HUD_GONE_SECONDS:
            log.info('%s: match HUD gone', self.name); self.reset()

    def column_visible(self, client):
        x, y, w, h = column_rect(self.layout, client.width, client.height)
        image, _ = self.backend.grab(Rect(client.x + x, client.y + y, w, h))
        return read_column(image, self.layout, client.width, client.height, {})[1]

    def read_role(self, client):
        if self.in_match and self.role and self.polls % ROLE_EVERY: return True, self.role
        x, y, w, h = own_rect(client.width, client.height, self.hud_scale)
        own, _ = self.backend.grab(Rect(client.x + x, client.y + y, w, h))
        if self.role_reader: return self.role_reader(own, client.width, client.height, self.hud_scale)
        if self.matchers is None: self.matchers = load_matchers(self.root)
        return read_own(own, client.width, client.height, self.matchers, self.hud_scale)

    def try_fit(self, client):
        """DBD's HUD scale, found as the DS timer finds it and confirmed by the own perks."""
        band, _ = self.backend.grab(Rect(client.x, client.y, round(client.width * .3), client.height))
        scale, shift, confident = fit_hud(band, self.base_layout, band=True)
        if not confident or (scale == 1. and abs(shift) < 6): return False
        self.layout = with_hud_fit(self.base_layout, scale, shift); self.hud_scale = scale
        if not self.read_role(client)[0]:
            self.layout = self.base_layout; self.hud_scale = 1.; return False
        self.hud_fitted = True; log.info('%s: fitted to HUD scale %.2f shift %s', self.name, scale, shift); return True
