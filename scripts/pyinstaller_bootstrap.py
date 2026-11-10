"""Load one consistent bundled Qt DLL set before numerical-library plugins."""
import os
import sys
from pathlib import Path

_autoblood_dll_handles=[]
try:
    root=Path(sys._MEIPASS)
    for directory in (root/'PySide6',root/'shiboken6'):
        _autoblood_dll_handles.append(os.add_dll_directory(str(directory)))
    from PySide6 import QtCore,QtGui,QtWidgets
except Exception:
    import traceback
    report=Path(sys.executable).resolve().parent/'debug/rc1/windows_build/qt_bootstrap_error.txt'
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(traceback.format_exc(),encoding='utf-8')
    raise
