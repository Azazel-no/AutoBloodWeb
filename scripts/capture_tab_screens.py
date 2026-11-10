"""Save lossless DBD frames for building overlay tests. Press F6 to save, F7 to quit.

Reads pixels of the game window only. Sends no input and touches no game memory.
Frames show other players' nicknames, so the folder is ignored by git; only
nickname-free crops get pinned as test fixtures.
"""
from datetime import datetime
import json
from pathlib import Path
import sys
import threading

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
FOLDER=ROOT/'references/tab_screens'


def save_frame(backend,folder=FOLDER,now=datetime.now):
    """Returns the saved path, or None when DBD is not the active window."""
    import cv2
    window=backend.info()
    if not window.foreground:return None
    image,provider=backend.grab(window.client)
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/f'{now():%Y%m%d_%H%M%S_%f}.png'
    ok,encoded=cv2.imencode('.png',image[:,:,:3])
    if not ok:raise ValueError('png_encode_failed')
    path.write_bytes(encoded.tobytes())
    path.with_suffix('.json').write_text(json.dumps(dict(width=window.client.width,height=window.client.height,dpi=window.dpi,
        monitor=[window.monitor.width,window.monitor.height],provider=provider),indent=2),encoding='utf-8')
    return path


def main():
    from src.core.display_mode import exclusive_fullscreen
    from src.core.overlay_hotkeys import OverlayHotkeys
    from src.core.screen_manager import ScreenError,WindowsBackend
    backend=WindowsBackend();done=threading.Event()
    def received(action):
        if action=='reset':
            done.set();return
        if action!='capture':return
        try:path=save_frame(backend)
        except ScreenError as exc:
            print('Not saved:',exc.code,flush=True);return
        print('Saved:' if path else 'Not saved: click into DBD first.',path or '',flush=True)
        if exclusive_fullscreen():print('Warning: DBD reports exclusive fullscreen; switch to borderless.',flush=True)
    hotkeys=OverlayHotkeys(received,dict(capture='F6',reset='F7'))
    if hotkeys.failed:print('Shortcut taken by another program:',hotkeys.failed,flush=True)
    print('F6 saves a frame to',FOLDER,'| F7 quits',flush=True)
    done.wait();hotkeys.close()


if __name__=='__main__':
    main()
