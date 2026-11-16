"""Features that exist in the code but are not part of every build.

The Sable Detector (Memes tab) is experimental and does not ship in v1.1.0: release builds
(PyInstaller, sys.frozen) never show, start or bundle it. Running from source keeps it, so work
on it can continue; AUTOBLOODWEB_SABLE=0 hides it there too, =1 forces it on (source only).
"""
import os
import sys


def frozen():
    return bool(getattr(sys, 'frozen', False))


def sable_detector():
    if frozen():   # Its model and assets/sable are left out of the release build.
        return False
    return os.environ.get('AUTOBLOODWEB_SABLE', '1') != '0'
