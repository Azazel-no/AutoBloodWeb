"""Real MSS and GDI capture of our own Qt test window, never mouse input."""
import ctypes
from ctypes import wintypes as W
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication,QLabel
from PySide6.QtCore import Qt,QEventLoop,QTimer
from PySide6.QtGui import QPixmap,QPainter,QColor
from src.core.screen_manager import WindowsBackend,WindowInfo,Rect,ScreenManager


def require_unobstructed_reference(backend,info,owned,reference,root):
    # A desktop test cannot manufacture a visible reference while an external
    # full-screen observer stays above it. Do not ignore that window in the
    # production capture code or count this configuration as a passed test.
    blockers=[]
    for hwnd in backend.windows():
        if hwnd==info.hwnd:break
        if hwnd in owned or backend.u.IsIconic(hwnd):continue
        rect=W.RECT()
        if backend.u.GetWindowRect(hwnd,ctypes.byref(rect)):
            box=Rect(rect.left,rect.top,rect.right-rect.left,rect.bottom-rect.top)
            if box.intersects(reference):
                from src.core.hotkey_diagnostics import window_metadata
                blockers.append(dict(window=window_metadata(hwnd),rect=vars(box)))
    if blockers:
        import json
        destination=root/'debug/tests'/f'native_capture_blocked_{info.hwnd}.json'
        destination.write_text(json.dumps(dict(reason='foreign_windows_above_native_reference',windows=blockers),indent=2),encoding='utf-8')
        pytest.skip(f'Native reference covered by foreign windows; details: {destination}')


def test_native_capture_backends_on_user_reference_window(root,monkeypatch):
    app=QApplication.instance() or QApplication([])
    widget=QLabel(); widget.setWindowTitle('AutoBloodWeb capture test')
    widget.setWindowFlags(Qt.FramelessWindowHint|Qt.WindowStaysOnTopHint)
    widget.setPixmap(QPixmap(str(root/'references/bloodweb/bloodweb_level43_start.png')))
    widget.setGeometry(10,30,916,840); widget.show()
    loop=QEventLoop(); QTimer.singleShot(200,loop.quit); loop.exec()
    backend=WindowsBackend()
    try:
        hwnd=int(widget.winId()); r=W.RECT(); p=W.POINT()
        with backend.pixels():
            assert backend.u.GetClientRect(hwnd,ctypes.byref(r))
            assert backend.u.ClientToScreen(hwnd,ctypes.byref(p))
        client=Rect(p.x,p.y,r.right,r.bottom)
        # Read a real native test window explicitly. Production discovery never
        # accepts this Python process as Dead by Daylight.
        assert not backend.game_process(hwnd)
        screen=app.primaryScreen().geometry()
        info=WindowInfo(hwnd,'AutoBloodWeb capture test',client,
            Rect(screen.x(),screen.y(),screen.width(),screen.height()),96,True)
        monkeypatch.setattr(backend,'info',lambda:info)
        require_unobstructed_reference(backend,info,set(),client,root)
        frame=ScreenManager(backend).capture()
        assert frame.provider=='mss' and frame.image.std()>10
        assert backend.owns_point(info,frame.to_screen(frame.image.shape[1]/2,frame.image.shape[0]/2))
        # Force MSS failure and exercise actual Windows BitBlt/GetDIBits fallback.
        def fail(): raise OSError('forced MSS failure')
        monkeypatch.setattr('src.core.screen_manager.mss.MSS',fail)
        fallback,provider=backend.grab(client)
        assert provider=='gdi' and fallback.shape==(client.height,client.width,3)
        assert np.mean(np.abs(fallback.astype(float)-backend_pixels(root,client)))<20
    finally: widget.close(); app.processEvents()


def backend_pixels(root,client):
    from src.utils.images import read_image
    import cv2
    image=read_image(root/'references/bloodweb/bloodweb_level43_start.png')
    return cv2.resize(image,(client.width,client.height)).astype(float)


def test_native_right_overlay_and_bloodweb_occlusion(root,monkeypatch):
    from src.core.screen_manager import ScreenError
    app=QApplication.instance() or QApplication([])
    if app.primaryScreen().geometry().width()<1600: pytest.skip('Native test requires room for two windows')
    canvas=QPixmap(1800,1000); canvas.fill(QColor('#303a42'))
    painter=QPainter(canvas); painter.drawPixmap(30,90,QPixmap(str(root/'references/bloodweb/bloodweb_level43_start.png'))); painter.end()
    game=QLabel(); game.setWindowFlags(Qt.FramelessWindowHint|Qt.WindowStaysOnTopHint)
    game.setPixmap(canvas); game.setGeometry(0,0,1800,1000); game.show()
    # Keep the real overlay owned by the reference window. Independent topmost
    # windows can swap Z order when a delayed native activation is delivered
    # during the slow scan; ownership makes this fixture's overlap stable.
    overlay=QLabel('AutoBloodWeb overlay test',game); overlay.setStyleSheet('background:#10151b;color:white')
    overlay.setWindowFlags(Qt.Window|Qt.FramelessWindowHint|Qt.WindowStaysOnTopHint)
    overlay.setGeometry(1200,0,600,1000); overlay.show(); overlay.raise_()
    loop=QEventLoop(); QTimer.singleShot(200,loop.quit); loop.exec()
    backend=WindowsBackend()
    info=WindowInfo(int(game.winId()),'Own Qt reference window',Rect(0,0,1800,1000),Rect(0,0,1920,1080),96,False)
    monkeypatch.setattr(backend,'info',lambda:info)
    try:
        require_unobstructed_reference(backend,info,{int(overlay.winId())},Rect(30,90,916,840),root)
        frame=ScreenManager(backend).capture()
        assert frame.roi.x+frame.roi.width<1200 and frame.image.std()>10
        assert any(r.x>=1200 for r in backend.occlusions(info))
        overlay.move(350,0); overlay.raise_(); app.processEvents()
        with pytest.raises(ScreenError,match='bloodweb_occluded'): ScreenManager(backend).capture()
    finally:
        overlay.close(); game.close(); app.processEvents()
