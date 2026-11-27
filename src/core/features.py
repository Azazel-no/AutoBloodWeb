"""Features that exist in the code but are not part of every build.

The Sable Detector (Memes tab) is experimental and does not ship in v1.1.0: release builds
(PyInstaller, sys.frozen) never show, start or bundle it. Running from source keeps it, so work
on it can continue; AUTOBLOODWEB_SABLE=0 hides it there too, =1 forces it on (source only).

The 'Match found' alert and the queue probe (Settings, TASKS 2026-11-25 C) are untested by players
and do not ship in v1.1.1 either: in release builds there is no card, no strings, no controller
thread and no Ctrl+Shift+M marker key, and settings saved by a development build stay inert.
AUTOBLOODWEB_MATCH_ALERT=0 hides them in source runs too.
"""
import os
import sys


def frozen():
    return bool(getattr(sys, 'frozen', False))


def sable_detector():
    if frozen():   # Its model and assets/sable are left out of the release build.
        return False
    return os.environ.get('AUTOBLOODWEB_SABLE', '1') != '0'


def match_alert():
    if frozen():   # src.core.match_alert, queue_probe, queue_signals, alert_strings and the page are left out.
        return False
    return os.environ.get('AUTOBLOODWEB_MATCH_ALERT', '1') != '0'
