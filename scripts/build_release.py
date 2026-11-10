"""Build the AutoBloodWeb release: PyInstaller one-folder app, portable zip, Inno Setup installer, SHA-256 sums.

Run with the release build environment (see README, "Building from source"):

    build\\venv\\Scripts\\python.exe scripts\\build_release.py

The build environment must contain opencv-python-headless (not opencv-python) and PySide6-Essentials only.
Output goes to build\\release\\.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import __version__ as VERSION  # noqa: E402

NAME = 'AutoBloodWeb'
BUILD = ROOT / 'build'
DIST = BUILD / 'dist'
APP = DIST / NAME
RELEASE = BUILD / 'release'

# Runtime modules from scripts/ that src imports.
RUNTIME_SCRIPTS = ('live_test_v47', 'live_test_v48', 'live_test_v53', 'live_test_v54', 'native_chain_v55', 'native_price_v55')
EXCLUDES = ('tkinter', 'pytest', '_pytest', 'IPython', 'matplotlib', 'scipy', 'pandas', 'PyInstaller',
            'PySide6.QtNetwork', 'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtPdf', 'PySide6.QtOpenGL',
            'PySide6.QtOpenGLWidgets', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtMultimedia',
            'PySide6.Qt3DCore', 'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtSql', 'PySide6.QtTest',
            'PySide6.QtDBus', 'PySide6.QtDesigner', 'PySide6.QtHelp', 'PySide6.QtPrintSupport', 'PySide6.QtXml',
            'PySide6.QtConcurrent', 'PySide6.QtStateMachine', 'PySide6.QtUiTools')
# Files shipped beside the executable (the app reads them relative to its folder).
DATA_FILES = ('data/native_price_locator.json', 'data/native_total_price_locator.json', 'data/wiki_sources.json',
              'src/core/ocr_helper.ps1', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md')
DATA_DIRS = ('assets', 'licenses')
# Reference screenshots the runtime opens (traced on startup, the GUI and analysis of varied frames, plus the
# sources read only while pricing nodes and on the automatic-unlock tooltip). Each is checked by SHA-256 at
# load time, so they ship unchanged. Other files under references/ are test and development material.
RUNTIME_REFERENCES = (
    'references/bloodweb/bloodweb_level43_finished_waiting_refresh.png',
    'references/bloodweb/bloodweb_level43_start.png',
    'references/bloodweb/bloodweb_prestige_node.png',
    'references/bloodweb/bloodweb_prestige_reward_ok.png',
    'references/bloodweb/prestige_node_example.png',
    'references/bloodweb/ui90/center_level11.png',
    'references/bloodweb/ui90/manifest.json',
    'references/bloodweb/v54_dark_lord/tutorial_unpredictability.png',
    'references/bloodweb/v54_onryo/automatic_1605.png',
    'references/bloodweb/v54_onryo/early_center_1815.png',
    'references/bloodweb/v54_onryo/prestige_1320.png',
    'references/bloodweb/v54_onryo/prestige_ready_1305.png',
    'references/bloodweb/v54_onryo/reward_1530.png',
    'references/bloodweb/v54_onryo/web_center_240.png',
    'references/bloodweb/v56_dark_lord_prestige/manifest.json',
    'references/bloodweb/v56_dark_lord_prestige/native_ready.png',
    'references/bloodweb/v56_early_center/glyph.png',
    'references/bloodweb/v56_early_center/manifest.json',
    'references/bloodweb/v56_early_center/native_source.png',
    'references/bloodweb/v57_knight/continue_perk_slot_iii.png',
    'references/bloodweb/v58_nicolas/automatic_native.png',
    'references/bloodweb/v58_nicolas/price_4000_native.png',
    'references/bloodweb/v_rc1_ui/native_price_locator_source.png',
    'references/bloodweb/v_rc1_ui/native_total_price_locator_source.png',
)
REFERENCE_DIRS = ('references/inventory', 'references/event')
# Bundled binaries the app never loads: OpenCV video I/O (no video capture), Qt's software OpenGL
# (widgets only), Pillow's AVIF codec, and Qt's own translations (the app has its own strings).
DROP = ('_internal/cv2/opencv_videoio_ffmpeg*.dll', '_internal/PySide6/opengl32sw.dll', '_internal/PIL/_avif*.pyd',
        '_internal/PySide6/translations')


def tracked(prefix):
    out = subprocess.run(['git', 'ls-files', '-z', prefix], cwd=ROOT, capture_output=True, check=True).stdout.decode('utf-8')
    return [p for p in out.split('\0') if p]


def runtime_references():
    files = set(tracked('references'))
    need = set(RUNTIME_REFERENCES)
    missing = need - files
    if missing:
        raise SystemExit(f'Runtime references missing from git: {sorted(missing)}')
    for folder in REFERENCE_DIRS:
        need |= {p for p in files if p.startswith(folder + '/')}
    return sorted(need)


def drop_unused():
    removed = 0
    for pattern in DROP:
        for path in APP.glob(pattern):
            if path.is_dir():
                removed += folder_size(path)
                shutil.rmtree(path)
            else:
                removed += path.stat().st_size
                path.unlink()
    return removed


def version_file():
    parts = (VERSION.split('.') + ['0', '0', '0'])[:4]
    tup = ', '.join(parts)
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({tup}), prodvers=({tup}), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'AutoBloodWeb contributors'),
      StringStruct('FileDescription', 'AutoBloodWeb'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('InternalName', '{NAME}'),
      StringStruct('LegalCopyright', 'MIT License'),
      StringStruct('OriginalFilename', '{NAME}.exe'),
      StringStruct('ProductName', 'AutoBloodWeb'),
      StringStruct('ProductVersion', '{VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    path = BUILD / 'version_info.txt'
    path.write_text(text, encoding='utf-8')
    return path


def pyinstaller():
    import cv2  # noqa: F401  # fail early when the build environment is incomplete
    from importlib.metadata import distributions
    names = {d.metadata['Name'].lower() for d in distributions()}
    if 'opencv-python' in names or 'pyside6-addons' in names:
        raise SystemExit('Use the release environment: opencv-python-headless and PySide6-Essentials only.')
    args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed', '--name', NAME,
            '--distpath', str(DIST), '--workpath', str(BUILD / 'work'), '--specpath', str(BUILD),
            '--paths', str(ROOT), '--uac-admin', '--icon', str(ROOT / 'assets/app_icon.ico'),
            '--version-file', str(version_file()), '--runtime-hook', str(ROOT / 'scripts/pyinstaller_bootstrap.py'),
            '--collect-binaries', 'windows_capture', '--hidden-import', 'PySide6.QtSvg', '--noupx']
    for module in RUNTIME_SCRIPTS:
        args += ['--hidden-import', f'scripts.{module}']
    for module in EXCLUDES:
        args += ['--exclude-module', module]
    args.append(str(ROOT / 'scripts/windows_entry.py'))
    subprocess.run(args, cwd=ROOT, check=True)


ICON_VARIANTS = ('/original_color/', '/wiki_originals/', '/wiki_normalized/')
DISPLAY_SIZE = 128  # display copies are drawn at 38-70 px; the matcher never reads them


def unreferenced_icon_files():
    """Wiki icon files that no manifest or catalogue names (leftovers of earlier syncs). Nothing globs these folders."""
    named = set()

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            named.add(o.replace('\\', '/'))

    for rel in tracked('assets') + tracked('data'):
        if rel.endswith('.json'):
            walk(json.loads((ROOT / rel).read_text(encoding='utf-8')))
    return {rel for rel in tracked('assets') if any(v in rel for v in ICON_VARIANTS) and rel not in named}


def shrink_display_icons():
    """Downscale the colour display icons (original_color, 256 px) in the build copy only.

    They are shown in the icon grid, priority list, info card and randomizer at 38-70 px and are never
    hash-checked or used for recognition, which reads wiki_originals and wiki_normalized.
    """
    from PIL import Image
    saved = 0
    for path in (APP / 'assets').rglob('original_color/*.png'):
        before = path.stat().st_size
        with Image.open(path) as image:
            image.load()
            if max(image.size) <= DISPLAY_SIZE:
                continue
            small = image.resize((DISPLAY_SIZE, DISPLAY_SIZE), Image.LANCZOS)
        small.save(path, optimize=True)
        saved += before - path.stat().st_size
    print(f'Display icons downscaled to {DISPLAY_SIZE} px: saved {saved / 1e6:.1f} MB')


def copy_data():
    for rel in DATA_FILES:
        target = APP / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target)
    skip = unreferenced_icon_files()
    for folder in DATA_DIRS:
        for rel in tracked(folder):
            if rel in skip:
                continue
            target = APP / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, target)
    shrink_display_icons()
    refs = runtime_references()
    for rel in refs:
        target = APP / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target)
    return refs


def folder_size(path):
    return sum(p.stat().st_size for p in path.rglob('*') if p.is_file())


def portable_zip():
    path = RELEASE / f'{NAME}-{VERSION}-portable.zip'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for file in sorted(APP.rglob('*')):
            if file.is_file():
                z.write(file, Path(NAME) / file.relative_to(APP))
    return path


def find_iscc():
    for candidate in (shutil.which('ISCC'), os.path.expandvars(r'%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe'),
                      r'C:\Program Files (x86)\Inno Setup 6\ISCC.exe', r'C:\Program Files\Inno Setup 6\ISCC.exe'):
        if candidate and Path(candidate).is_file():
            return candidate
    raise SystemExit('Inno Setup 6 (ISCC.exe) not found.')


def installer():
    subprocess.run([find_iscc(), f'/DAppVersion={VERSION}', f'/DSourceDir={APP}', f'/DOutputDir={RELEASE}',
                    str(ROOT / 'installer/AutoBloodWeb.iss')], cwd=ROOT, check=True)
    return RELEASE / f'{NAME}-{VERSION}-setup.exe'


def checksums(paths):
    lines = [f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}' for p in paths]
    (RELEASE / 'SHA256SUMS.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return lines


def main():
    for path in (DIST, RELEASE):
        shutil.rmtree(path, ignore_errors=True)
    RELEASE.mkdir(parents=True)
    pyinstaller()
    print(f'Dropped unused binaries: {drop_unused() / 1e6:.1f} MB')
    refs = copy_data()
    size = folder_size(APP)
    print(f'App folder: {size / 1e6:.1f} MB, {len(refs)} reference files')
    if size > 300e6:
        print(f'WARNING: app folder is {size / 1e6:.1f} MB, over the 300 MB target')
    outputs = [portable_zip(), installer()]
    for line in checksums(outputs):
        print(line)
    for p in outputs:
        print(f'{p.name}: {p.stat().st_size / 1e6:.1f} MB')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
