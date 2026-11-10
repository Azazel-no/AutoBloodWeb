"""Run with python -m src.app. All outputs remain in the existing project."""
import argparse
import json
import logging
from pathlib import Path
import sys

from src.core.runtime_paths import project_root
ROOT=project_root()


def main(argv=None):
    parser=argparse.ArgumentParser(description='AutoBloodWeb — static analysis and icon selection')
    parser.add_argument('--auto-elevate',action='store_true')
    parser.add_argument('--elevated',action='store_true')
    parser.add_argument('--start',action='store_true')
    parser.add_argument('--dev',action='store_true',help='Show developer controls for this session')
    parser.add_argument('--watcher',action='store_true',help='Logon watcher of the packaged app: start in the tray when DBD appears')
    parser.add_argument('--tray',action='store_true',help='Start hidden in the system tray (used by Start with DBD); never starts purchases')
    parser.add_argument('--live-session',type=Path,help='Explicit durable BP/node/time limits for ordinary GUI Start')
    parser.add_argument('--preferences',type=Path,help='Use an explicit profile without replacing the main profile')
    parser.add_argument('--smoke-gui',action='store_true',help='Open and close an isolated read-only GUI; requires --preferences and --output')
    parser.add_argument('--build-library',action='store_true')
    parser.add_argument('--force',action='store_true',help='Re-extract icons from original inventory files')
    parser.add_argument('--analyze',type=Path,help='Analyze one screenshot, without mouse control')
    parser.add_argument('--analyze-all',action='store_true',help='Analyze all project Bloodweb references')
    parser.add_argument('--scan-game',action='store_true',help='Capture and analyze the live game without input')
    parser.add_argument('--dry-run',action='store_true',help='Capture the live game and report the next proposed action')
    parser.add_argument('--diagnose-display',action='store_true',help='Report the current physical game window geometry')
    parser.add_argument('--audit-libraries',action='store_true',help='Inspect existing killer folders and recorded Wiki access status')
    parser.add_argument('--audit-templates',action='store_true',help='Validate source crops and version the existing 165 references')
    parser.add_argument('--sync-wiki',action='store_true',help='Download public Wiki originals; stops on access denial')
    parser.add_argument('--sync-perks',action='store_true',help='Download the public Wiki perk table into assets/perk_catalog.json; stops on access denial')
    parser.add_argument('--sync-items',action='store_true',help='Download public Wiki add-on, item and offering descriptions into assets/item_info.json; stops on access denial')
    parser.add_argument('--verify-wiki',action='store_true',help='Verify originals and publish complete character add-on sections')
    parser.add_argument('--repair-wiki-assets',action='store_true',help='Strict portraits and colour icons composed from Wiki HTML/CSS layers')
    parser.add_argument('--input-self-test',action='store_true',help='Native mouse and F8/F9 test, confined to own Qt stand')
    parser.add_argument('--hotkey-test',action='store_true',help='Manual hardware shortcut diagnosis; sends no keyboard/mouse input')
    parser.add_argument('--role',choices=('survivor','killer'),default='survivor')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    if args.smoke_gui and (not args.preferences or not args.output or args.start):
        parser.error('--smoke-gui requires --preferences and --output, and forbids --start')
    if args.auto_elevate and not args.elevated:
        from src.core.screen_manager import WindowsBackend
        from src.core.elevation import needs_elevation,request_restart
        if needs_elevation(WindowsBackend()):
            return 0 if request_restart(ROOT,autostart=args.start,live_session=args.live_session,preferences=args.preferences) else 2
    if args.watcher:
        from src.core.autostart import run_watcher
        return run_watcher(ROOT)
    if args.input_self_test:
        from src.gui.input_stand import main as input_main
        saved=sys.argv; sys.argv=[saved[0],'--auto']
        try: return input_main()
        finally: sys.argv=saved
    from logging.handlers import RotatingFileHandler
    (ROOT/'debug').mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s',
                        handlers=[RotatingFileHandler(ROOT/'debug/app.log',maxBytes=5_000_000,backupCount=4,encoding='utf-8')])
    from src.core.icon_library import IconLibrary
    library=IconLibrary(ROOT).build(force=args.force)
    if args.audit_templates:
        from src.core.template_audit import audit_templates
        report=audit_templates(library)
        print(json.dumps({k:report[k] for k in ('version','count','structurally_valid','identity_checked')},indent=2)); return 0
    if args.sync_wiki:
        from src.core.wiki_library import sync_wiki
        report=sync_wiki(ROOT)
        print(json.dumps({k:report[k] for k in ('attempts','errors','access_denied','added')},indent=2)); return 2 if report['access_denied'] or report['errors'] else 0
    if args.sync_perks:
        from src.core.perk_wiki import sync_perks
        report=sync_perks(ROOT)
        print(json.dumps({k:report[k] for k in ('parsed','stored','complete','access_denied')},indent=2)); return 0 if report['complete'] else 2
    if args.sync_items:
        from src.core.item_wiki import sync_items
        report=sync_items(ROOT)
        print(json.dumps(report,indent=2)); return 0 if report['complete'] else 2
    if args.verify_wiki:
        from src.core.wiki_activation import verify_wiki
        report=verify_wiki(library)
        print(json.dumps(dict(records_verified=report.get('records_verified',0),new_icons=len(report.get('entries',[])),
            characters=len(report.get('character_status',{})),portraits=len(report.get('portraits',{})),errors=report.get('errors',[])),indent=2))
        return 2 if report.get('errors') else 0
    if args.repair_wiki_assets:
        from src.core.wiki_assets import repair_wiki_assets
        report=repair_wiki_assets(library)
        print(json.dumps(dict(characters=len(report['characters']),portraits=len(report['portraits']),
            colored=len(report['icons']),errors=report['errors']),indent=2)); return 2 if report['errors'] else 0
    if args.audit_libraries:
        from src.core.library_audit import audit_libraries
        report=audit_libraries(library); output=ROOT/'debug/v4/library_audit.json'; output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(characters=report['characters'],images_added=report['images_added'],report=str(output)),indent=2)); return 0
    if args.scan_game or args.dry_run or args.diagnose_display:
        from dataclasses import asdict
        from src.core.screen_manager import ScreenManager,ScreenError
        from src.core.bloodweb_detector import BloodwebDetector
        from src.core.path_planner import plan_route
        from src.core.preferences import Preferences
        detector=BloodwebDetector(library,args.role); screen=ScreenManager(detector=detector)
        try:
            if args.diagnose_display:
                info=screen.backend.info()
                packet=dict(window=asdict(info),occlusions=[asdict(r) for r in screen.backend.occlusions(info)])
                if args.output:args.output.write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
                else:print(json.dumps(packet,ensure_ascii=False,indent=2))
                return 0
            frame=screen.capture(); result=detector.analyze(frame.image,'live_game')
            from src.core.diagnostics import DiagnosticStore
            preferences=Preferences(args.preferences or ROOT/'data/preferences.json',library.entries)
            plan=plan_route(result,preferences.priorities[args.role],preferences.settings['priority_mode'])
            DiagnosticStore(ROOT/'debug/analysis_runs').record(frame.image,result,detector,frame,plan,phase='cli_dry_run' if args.dry_run else 'cli_scan_game')
            packet=dict(frame=frame.metadata(),analysis=result.to_dict(),plan=asdict(plan))
            packet['plan']['screen_point']=frame.to_screen(*next((n.x,n.y) for n in result.nodes if n.id==plan.next_node)) if plan.next_node is not None else None
            (ROOT/'debug/live').mkdir(parents=True,exist_ok=True)
            (ROOT/'debug/live/latest.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(dict(frame=frame.metadata(),state=result.screen_state,nodes=len(result.nodes),
                recognized=sum(n.icon_id is not None for n in result.nodes),plan=packet['plan'],
                debug=result.debug_image),ensure_ascii=False,indent=2)); return 0
        except ScreenError as exc:
            print(json.dumps(dict(error=str(exc),detail=exc.detail),ensure_ascii=False)); return 2
    if args.build_library:
        print(json.dumps({'icons':len(library.entries),'rebuilt':library.rebuilt,'warnings':library.warnings,'manifest':str(library.manifest_path)},ensure_ascii=False,indent=2))
    if args.analyze or args.analyze_all:
        from src.core.bloodweb_detector import BloodwebDetector
        detector=BloodwebDetector(library,args.role)
        paths=[args.analyze.resolve()] if args.analyze else sorted((ROOT/'references/bloodweb').glob('*.png'))
        summary=[]
        for path in paths:
            output=args.output.resolve() if args.output and not args.analyze_all else ROOT/'debug/results'/path.name
            from src.core.diagnostics import DiagnosticStore
            from src.utils.images import read_image,write_image
            image=read_image(path);result=detector.analyze(image,str(path))
            DiagnosticStore(ROOT/'debug/analysis_runs').record(image,result,detector,phase='cli_file')
            if args.output and not args.analyze_all:
                if output.exists():output=output.with_name(output.stem+'_'+result.analysis_id+output.suffix)
                write_image(output,read_image(result.debug_image))
            summary.append({'source':path.name,'state':result.screen_state,'nodes':len(result.nodes),
                            'recognized':sum(n.icon_id is not None for n in result.nodes),'debug':result.debug_image})
        print(json.dumps(summary,ensure_ascii=False,indent=2))
        return 0
    if args.build_library: return 0
    from PySide6.QtWidgets import QApplication
    from src.gui.main_window import MainWindow
    instance=None
    if not args.smoke_gui:
        from src.core.autostart import APP_MUTEX,SingleInstance
        instance=SingleInstance(APP_MUTEX)
        if not instance.first:
            print('AutoBloodWeb is already running.')
            if not args.tray:
                import ctypes
                ctypes.windll.user32.MessageBoxW(None,'AutoBloodWeb уже запущен. Откройте его через значок в трее. / AutoBloodWeb is already running. Open it from the tray icon.','AutoBloodWeb',0x40)
            return 0
    if sys.platform=='win32':
        # Without its own ID the taskbar groups the window under python.exe and shows that icon.
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('AutoBloodWeb.App')
    app=QApplication(sys.argv[:1]); app.setApplicationName('AutoBloodWeb')
    window=MainWindow(library,preferences_path=args.preferences) if args.preferences else MainWindow(library)
    if args.smoke_gui:
        window.hotkeys.close();window.dry_run.setChecked(True);window.buttons['start'].setEnabled(False)
    if args.dev:window.set_developer_mode(True,save=False)
    if args.tray:window.enter_tray_mode()
    else:window.show()
    window.live_session_path=args.live_session
    if args.smoke_gui:
        from PySide6.QtCore import QTimer
        def smoke():
            packet=dict(visible=window.isVisible(),platform=app.platformName(),geometry=list(window.geometry().getRect()),
                icons=len(library.entries),preferences=str(window.preferences_path.resolve()),dry_run=window.dry_run.isChecked(),
                input_commands=0,root=str(ROOT),frozen=bool(getattr(sys,'frozen',False)))
            window.save_preferences();window.close()
            args.output.write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
            app.quit()
        QTimer.singleShot(500,smoke)
    elif args.start:
        from PySide6.QtCore import QTimer
        import time
        deadline=time.monotonic()+5
        def start_when_ready():
            # The F9 receiver registers on its own thread just after the window opens;
            # Start refuses without a live Stop, so wait for it instead of failing silently.
            health=window.hotkeys.native_health() if hasattr(window.hotkeys,'native_health') else dict(ready=True)
            if not health['ready'] and time.monotonic()<deadline:QTimer.singleShot(100,start_when_ready);return
            logging.getLogger(__name__).info('Autostart Start hotkeys=%s',health)
            window.start_automation()
        QTimer.singleShot(0,start_when_ready)
    elif args.hotkey_test:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0,window.open_hotkey_diagnostics)
    elif not args.tray and window.notice_due():
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0,window.show_notices)
    return app.exec()


if __name__=='__main__':
    raise SystemExit(main())
