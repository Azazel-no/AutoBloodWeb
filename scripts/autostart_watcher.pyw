"""Logon watcher: starts AutoBloodWeb in the tray when DBD appears.

Checks the process list every 3 seconds; never touches the game process and
never starts Bloodweb purchases.
"""
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from src.core.autostart import run_watcher


if __name__=='__main__':
    sys.exit(run_watcher(ROOT))
