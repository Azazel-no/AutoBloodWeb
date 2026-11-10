import json
import logging
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QSize, QTimer
from PySide6.QtGui import QFont, QIcon, QShortcut, QKeySequence, QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QButtonGroup, QStackedWidget, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QComboBox, QFrame, QScrollArea, QTabWidget, QTableWidget,
    QTableWidgetItem, QHeaderView, QToolButton, QFileDialog, QMessageBox, QDoubleSpinBox,
    QDialog,QLineEdit,QApplication,QSpinBox,QSizePolicy,QSlider)

from src.core.bloodweb_detector import BloodwebDetector
from src.core.capture import capture_game, GameNotFoundError
from src.core.localization import Localization
from src.core.path_planner import plan_route
from src.gui.widgets import Background, BloodButton, IconGrid, NavButton, Preview, ResetButton, ToggleSwitch, TrashButton, app_icon
from src.utils.images import read_image
from src.core.preferences import Preferences
from src.core.screen_manager import ScreenManager, ScreenError,WindowsBackend
from src.core.automation import Settings, State
from src.core.input_control import GlobalHotkeys
from src.gui.automation_worker import AutomationWorker, make_engine
from html import escape
from PySide6.QtCore import QUrl
from src.core.item_info import load_item_info
from src.core import tierlist
from src.gui.info_card import InfoCard
from src.gui.priority_list import PriorityList
from src.gui.killer_selector import KillerSelector
from src.gui.selector import ElidedCombo
from src.core.priority_targets import PERK_TARGET,perk_target_entry,perk_eligible

log=logging.getLogger(__name__)
PTB_CHARACTERS=('terrifier',)  # Terrifier chapter: PTB data; re-check after the live release (assets/ptb_data.json).
SUPPORT_ADDRESS='TBgvqCy45eEcuazM32YY2vTDLvMBpPpK4W'  # USDT, TRC20 (Tron) only.
ISSUES_URL='https://github.com/Azazel-no/AutoBloodWeb/issues/new/choose'
LANGUAGES=(('ru','Русский'),('en','English'),('es','Español'),('de','Deutsch'),('fr','Français'))

STYLE='''
QWidget { color: #ece5e5; font-family: "Segoe UI"; font-size: 13px; }
QMainWindow, QScrollArea, QScrollArea > QWidget > QWidget, QStackedWidget { background: transparent; }
QFrame#card { background: rgba(24,16,17,190); border: none; border-radius: 10px; }
QFrame#hero { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 rgba(70,14,22,235),stop:1 rgba(26,16,17,235)); border: 1px solid rgba(190,40,52,90); border-radius: 12px; }
QFrame#separator { background: rgba(170,40,52,110); border: none; }
QFrame#statusPill { background: rgba(14,9,10,190); border-radius: 9px; }
QFrame#sidebar { background: rgba(16,11,12,245); border: none; border-right: 1px solid rgba(170,40,52,40); }
QLabel#section { color: #ead6d7; font-family: "Bahnschrift SemiCondensed","Bahnschrift","Segoe UI"; font-size: 15px; font-weight: 700; letter-spacing: 1px; }
QLabel#pageTitle { color: #f6efef; font-family: "Bahnschrift SemiCondensed","Bahnschrift","Segoe UI"; font-size: 22px; font-weight: 700; letter-spacing: 1px; }
QLabel#subtitle { color: #a58f90; font-size: 9px; }
QLabel#notice { color: #e8bcbf; background: rgba(120,22,36,150); padding: 8px; border-radius: 10px; }
QLabel#muted { color: #a8979a; font-size: 12px; }
QLabel#pill { color: #e8d6d7; background: rgba(120,22,36,110); border-radius: 11px; padding: 3px 12px; font-size: 12px; }
QLabel#value { font-size: 23px; color: #f0e2e2; }
* { outline: none; }
QListWidget { background: rgba(14,9,10,150); border: none; border-radius: 8px; color: #eee; outline: 0; }
QListWidget::item { padding: 3px; border-radius: 6px; }
QListWidget::item:hover { background: rgba(170,24,36,70); }
QListWidget::item:selected { background: #6e1a28; }
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox { background: #1b1314; color: #eee; border: 1px solid #58282e; border-radius: 10px; padding: 6px 9px; }
QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover,
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid #c22b3c; }
QToolButton#iconTile:focus { border: 1px solid #d83247; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView { background: #1b1314; color: #eee; selection-background-color: #7a1c2b; }
QToolButton#iconTile { background: rgba(44,31,33,190); border: 1px solid #47292d; border-radius: 10px; }
QToolButton#iconTile:checked { border: 2px solid #d83247; background: #5c1824; }
QToolTip { background: #1b1314; color: #eee; border: 1px solid #7a1c2b; }
QScrollBar:vertical { width: 8px; background: transparent; }
QScrollBar::handle:vertical { background: #5a2a30; min-height: 20px; border-radius: 4px; }
QScrollBar:horizontal { height: 8px; background: transparent; }
QScrollBar::handle:horizontal { background: #5a2a30; min-width: 20px; border-radius: 4px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0px; width: 0px; }
QSlider::groove:horizontal { height: 4px; background: #3b3234; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #c22b3c; border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; margin: -5px 0; background: #e6dcdd; border-radius: 7px; }
QTabWidget::pane { border: 1px solid #47292d; border-radius: 10px; background: rgba(14,9,10,170); }
QTabBar::tab { background: #1b1314; padding: 7px 12px; color: #b5a5a7; border-radius: 8px; }
QTabBar::tab:hover { background: #4a1822; }
QTabBar::tab:selected { background: #6e1a28; color: #fff; }
QTableWidget { background: #120c0d; gridline-color: #392325; border: none; }
QHeaderView::section { background: #241719; padding: 5px; border: none; }
QTableWidget::item:selected { background: #6e1a28; }
QToolButton { color: #d5c8c9; border: none; }
'''


class AnalysisWorker(QThread):
    completed=Signal(object)
    capture_checked=Signal(object)
    failed=Signal(str)
    failed_detail=Signal(str,str)

    def __init__(self,library,role,path,parent=None,character='blight',detailed=True):
        super().__init__(parent)
        self.library,self.role,self.path=library,role,path
        self.character=character
        self.detailed=detailed

    def run(self):
        try:
            detector=BloodwebDetector(self.library,self.role,character=self.character)
            detector.prepare()
            frame=None
            if self.path:
                image=read_image(self.path)
            else:
                self.capture_manager=ScreenManager(detector=detector)
                frame=self.capture_manager.capture(); image=frame.image
            result=detector.analyze_live(frame) if frame else detector.analyze(image,str(self.path))
            result.live_frame=frame
            result.capture_manager=self.capture_manager if frame else None
            result.raw_image=image; result.renderer=detector
            from src.core.diagnostics import DiagnosticStore
            store=DiagnosticStore(self.library.root/'debug/analysis_runs')
            store.record(image,result,detector,frame,plan_route(result,[]),phase='file' if self.path else 'scan_game',full=self.detailed)
            if not self.isInterruptionRequested():
                self.completed.emit(result)
        except (GameNotFoundError,ScreenError) as exc:
            manager=getattr(self,'capture_manager',None)
            if manager and manager.last_raw is not None:
                from src.core.models import Analysis,ScreenState
                from src.core.diagnostics import DiagnosticStore
                raw=manager.last_raw
                failed=Analysis(raw.shape[1],raw.shape[0],ScreenState.UNKNOWN,diagnostics=[exc.code])
                DiagnosticStore(self.library.root/'debug/analysis_runs').record(raw,failed,detector,phase='capture_failed',reason=exc.code)
            log.warning('Capture failed code=%s detail=%s',exc.code,exc.detail)
            self.failed.emit(str(exc))
            self.failed_detail.emit(exc.code,exc.detail)
        except Exception as exc:
            log.exception('Image analysis failed')
            self.failed.emit(str(exc))
        finally:
            manager=getattr(self,'capture_manager',None)
            if manager: self.capture_checked.emit(getattr(manager,'capture_status',{}))


class MainWindow(QMainWindow):
    global_action=Signal(str)
    physical_stop_received=Signal(object)
    native_hotkey_event=Signal(object)
    def __init__(self,library,preferences_path=None):
        super().__init__()
        self.library=library; self.root=library.root
        self.preferences_path=preferences_path or self.root/'data/preferences.json'
        self.prefs=Preferences(self.preferences_path,library.entries)
        preferences=self.prefs.data
        self.locale=Localization(preferences.get('language','en'))
        valid={e.id for e in library.entries}
        self.selected=set(sum(self.prefs.priorities.values(),[]))
        self.auto_worker=None; self.calibration=self.prefs.saved_calibration(); self.last_live=None; self.last_file=None
        self.session_check=None;self.session_proof_at=None;self.resume_engine=None
        self.method_recovery_pending=False
        self.preparation_hotkey_check_consumed=False;self._start_prepared=False
        self.global_action.connect(self.handle_global_action)
        self.physical_stop_received.connect(self.record_emergency_test)
        self.get_foreground_hwnd=WindowsBackend().foreground_handle
        self.calibration_wizard=None; self.calibration_selection=None
        self.emergency_tested_signature=None; self.last_action_report=None
        from src.core.action_report import ActionReport
        trace_folder=self.root/'debug/hotkeys' if preferences_path is None else self.preferences_path.parent/'hotkeys'
        self.hotkey_trace=ActionReport(trace_folder/f'session_{uuid.uuid4().hex[:8]}.json')
        self.hotkey_diagnostic_only=False
        self.hotkey_test_game={}
        self.native_hotkey_event.connect(self.record_native_hotkey_event)
        self.hotkeys=GlobalHotkeys(self.hotkey_received,self.prefs.settings['hotkeys'])
        self.attach_hotkey_diagnostics()
        self.entries_by_id={e.id:e for e in library.entries}
        self.item_info=load_item_info(self.root);self.info_card=None
        self.entries_by_id[PERK_TARGET]=perk_target_entry(self.root,'survivor')
        self.last_analysis=None; self.worker=None; self.status_key='ready'; self.error_message=''
        self.cancelled=False; self.last_action_key=None
        self.setWindowTitle('AutoBloodWeb');self.setWindowIcon(app_icon(self.root))
        self.setStyleSheet(STYLE)
        self.background=Background(self.root)
        self.setCentralWidget(self.background)
        root_row=QHBoxLayout(self.background); root_row.setContentsMargins(0,0,0,0); root_row.setSpacing(0)
        self.sidebar=QFrame(); self.sidebar.setObjectName('sidebar'); self.sidebar.setFixedWidth(68); root_row.addWidget(self.sidebar)
        outer=QVBoxLayout(); outer.setContentsMargins(16,14,16,14); outer.setSpacing(10); root_row.addLayout(outer,1); self.content_layout=outer
        self.language=QComboBox()
        for code,name in LANGUAGES:self.language.addItem(name,code)
        self.language.setCurrentIndex(max(0,self.language.findData(self.locale.language)))
        self.language.currentIndexChanged.connect(self.change_language)
        self.notice=QLabel(); self.notice.setObjectName('notice'); self.notice.setWordWrap(True); outer.addWidget(self.notice)
        self.notice.hide()
        selectors=QHBoxLayout()
        self.role=ElidedCombo(); self.role.addItem('','survivor'); self.role.addItem('','killer')
        self.role.setCurrentIndex(1 if preferences.get('role')=='killer' else 0)
        self.role.currentIndexChanged.connect(self.role_changed)
        self.character=KillerSelector(self.root)
        self.character.setIconSize(QSize(32,32)); self.character.setMinimumHeight(44)
        self.role.setIconSize(QSize(32,32)); self.role.setMinimumHeight(44)
        available=sorted({e.character for e in library.entries if e.character},key=lambda c:(c!='blight',c))
        for character in available:
            portrait=getattr(library,'portraits',{}).get(character)
            self.character.addItem(QIcon(str(self.root/portrait)) if portrait else QIcon(),character,character)
        self.character.setCurrentIndex(max(0,self.character.findData(self.prefs.character)))
        self.prefs.fill_recommended(tierlist.recommended(self.root,available))
        self.prefs.activate_character(self.character.currentData() or 'blight')
        self.character.currentIndexChanged.connect(self.character_changed)
        portrait=getattr(library,'portraits',{}).get('survivor')
        if portrait: self.role.setItemIcon(0,QIcon(str(self.root/portrait)))
        portrait=getattr(library,'portraits',{}).get('killer')
        if portrait:self.role.setItemIcon(1,QIcon(str(self.root/portrait)))
        self.character.setVisible(self.current_role=='killer')
        self.topmost=ToggleSwitch(); self.topmost.toggled.connect(self.set_topmost)
        selectors.addWidget(self.role,1); selectors.addWidget(self.character,1)
        outer.addLayout(selectors)
        body_scroll=QScrollArea(); body_scroll.setWidgetResizable(True); body_scroll.setFrameShape(QFrame.NoFrame)
        body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body_scroll=body_scroll
        body=QWidget(); body_layout=QVBoxLayout(body); body_layout.setContentsMargins(0,0,3,0); body_layout.setSpacing(9)
        body_scroll.setWidget(body)
        self.pages=QStackedWidget(); self.pages.addWidget(body_scroll); outer.addWidget(self.pages,1)

        card,layout=self.card()
        line=QHBoxLayout(); self.library_title=self.section_label(); line.addWidget(self.library_title)
        self.library_hint_icon=QLabel(); self.library_hint_icon.setPixmap(QIcon(str(self.root/'assets/ui/mouse_right_click.svg')).pixmap(20,20)); line.addWidget(self.library_hint_icon)
        self.library_hint=QLabel(); self.library_hint.setObjectName('muted'); self.library_hint.setMinimumWidth(0); self.library_hint.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred); line.addWidget(self.library_hint,1)
        self.category=QComboBox(); self.category.currentIndexChanged.connect(self.refresh_library); line.addWidget(self.category)
        layout.addLayout(line); layout.addWidget(self.separator())
        self.library_count=QLabel(); self.library_count.setObjectName('muted'); layout.addWidget(self.library_count)
        self.grid_scroll=QScrollArea(); self.grid_scroll.setWidgetResizable(True); self.grid_scroll.setFrameShape(QFrame.NoFrame)
        self.grid_scroll.setMinimumHeight(176)
        self.grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.icon_grid=IconGrid(self.root,self.toggle_icon,on_details=lambda ident:self.show_item_details(ident,self.icon_grid.anchor)); self.grid_scroll.setWidget(self.icon_grid)
        layout.addWidget(self.grid_scroll); body_layout.addWidget(card,1)

        card,layout=self.card()
        line=QHBoxLayout(); self.selected_title=self.section_label(); line.addWidget(self.selected_title); line.addStretch()
        self.reset_button=ResetButton();self.reset_button.clicked.connect(self.reset_recommended);line.addWidget(self.reset_button)
        self.clear_button=TrashButton()
        self.clear_button.clicked.connect(self.confirm_clear); line.addWidget(self.clear_button); layout.addLayout(line); layout.addWidget(self.separator())
        self.selected_scroll=QScrollArea(); self.selected_scroll.setWidgetResizable(True); self.selected_scroll.setFixedHeight(61)
        self.selected_scroll.setFrameShape(QFrame.NoFrame)
        self.selected_widget=QWidget(); self.selected_layout=QHBoxLayout(self.selected_widget)
        self.selected_layout.setContentsMargins(0,0,0,2); self.selected_layout.setSpacing(5)
        self.selected_scroll.setWidget(self.selected_widget); layout.addWidget(self.selected_scroll); body_layout.addWidget(card)
        self.selected_scroll.hide()
        self.priority_list=PriorityList(); self.priority_list.reordered.connect(self.reorder_priorities)
        self.priority_list.details_requested.connect(lambda ident:self.show_item_details(ident,self.priority_list.anchor))
        self.priority_list.remove_requested.connect(self.toggle_icon)
        layout.addWidget(self.priority_list)
        body_layout.insertWidget(0,card)
        row=QHBoxLayout(); self.priority_mode_label=QLabel(); row.addWidget(self.priority_mode_label)
        self.priority_mode=QComboBox()
        for mode in ('top_first','smart','strict'):self.priority_mode.addItem('',mode)
        self.priority_mode.setCurrentIndex(self.priority_mode.findData(self.prefs.settings['priority_mode']))
        self.priority_mode.currentIndexChanged.connect(self.automation_settings_changed)
        row.addWidget(self.priority_mode); priority_mode_row=row

        card,layout=self.card(); card.setObjectName('hero')
        self.buttons={}
        row=QHBoxLayout()
        for key in ('start','pause','stop'):
            button=BloodButton(); button.setEnabled(key=='start'); self.buttons[key]=button; row.addWidget(button)
        self.buttons['start'].clicked.connect(self.start_automation)
        self.buttons['pause'].clicked.connect(self.pause_automation)
        self.buttons['stop'].clicked.connect(self.stop_analysis); layout.addLayout(row)
        self.toggle_box=QHBoxLayout(); self.toggle_box.setSpacing(8); layout.addLayout(self.toggle_box)
        self.level_limit_label=QLabel()
        self.level_limit=QSpinBox();self.level_limit.setRange(0,999);self.level_limit.setValue(self.prefs.settings['max_levels'])
        self.level_limit.valueChanged.connect(self.level_limit_changed);self.level_limit.setMaximumWidth(92)
        self.toggle_box.addStretch();self.toggle_box.addWidget(self.level_limit_label);self.toggle_box.addWidget(self.level_limit)
        row=QHBoxLayout(); self.test_button=BloodButton(); self.scan_button=BloodButton(primary=True)
        self.test_button.clicked.connect(self.choose_screenshot); self.scan_button.clicked.connect(lambda:self.start_analysis(None))
        row.addWidget(self.scan_button,1); row.addWidget(self.test_button,1); layout.addLayout(row)
        self.session_button=BloodButton();self.session_button.clicked.connect(self.open_session_check);layout.addWidget(self.session_button)
        self.status_pill=QFrame();self.status_pill.setObjectName('statusPill')
        pill=QHBoxLayout(self.status_pill);pill.setContentsMargins(10,3,12,3);pill.setSpacing(7)
        self.status_dot=QLabel();self.status_dot.setFixedSize(9,9);pill.addWidget(self.status_dot)
        self.action_status=QLabel();self.action_status.setStyleSheet('font-size: 12px; font-weight: 600;');pill.addWidget(self.action_status)
        self.action_reason=QLabel();self.action_reason.setWordWrap(True);self.action_reason.setObjectName('muted');pill.addWidget(self.action_reason,1)
        self.status_pill.hide();layout.addWidget(self.status_pill)
        self.runtime_summary=QLabel();self.runtime_summary.setWordWrap(True)
        self.runtime_summary.setStyleSheet('font-size: 12px; color: #c9c6be;')
        self.runtime_summary.hide();layout.addWidget(self.runtime_summary)
        self.countdown=QLabel();self.countdown.setAlignment(Qt.AlignCenter);self.countdown.setStyleSheet('font-size: 34px; font-weight: 700;')
        self.countdown.hide();layout.addWidget(self.countdown)
        self.hotkey_hint=QLabel(); self.hotkey_hint.setObjectName('muted')
        layout.addWidget(self.hotkey_hint)
        self.action_panel=card
        for button in (*self.buttons.values(),self.scan_button,self.test_button,self.session_button):
            button.setMinimumHeight(46);button.setStyleSheet('font-size: 16px; font-weight: 600;')
        row=QHBoxLayout(); self.samples=QComboBox()
        samples=sorted((self.root/'references/bloodweb').glob('*.png'))
        for p in samples: self.samples.addItem(p.stem.removeprefix('bloodweb_'),p)
        preferred=next((i for i,p in enumerate(samples) if p.name=='bloodweb_level43_start.png'),-1)
        if preferred>=0: self.samples.setCurrentIndex(preferred)
        self.sample_button=BloodButton(); self.sample_button.clicked.connect(self.analyze_sample)
        sample_row=row
        row.addWidget(self.samples,1); row.addWidget(self.sample_button)
        body_layout.addWidget(card)

        card,layout=self.card(); self.display_card=card
        self.display_title=QToolButton(); self.display_title.setCheckable(True)
        self.display_title.setToolButtonStyle(Qt.ToolButtonTextBesideIcon); self.display_title.setArrowType(Qt.RightArrow)
        layout.addWidget(self.display_title)
        self.display_info=QLabel(); self.display_info.setWordWrap(True); layout.addWidget(self.display_info)
        self.dry_run=ToggleSwitch(); self.dry_run.setChecked(self.prefs.settings['dry_run'])
        layout.addWidget(self.dry_run)
        self.display_panel=QWidget(); self.display_panel.hide(); layout.addWidget(self.display_panel)
        layout=QVBoxLayout(self.display_panel); layout.setContentsMargins(0,0,0,0)
        self.display_title.toggled.connect(lambda checked:(self.display_panel.setVisible(checked),
            self.display_title.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)))
        self.auto_prestige=ToggleSwitch(); self.auto_prestige.setChecked(self.prefs.settings.get('auto_prestige') is True)
        self.toggle_box.insertWidget(0,self.auto_prestige)
        row=QHBoxLayout(); self.purchase_label=QLabel(); row.addWidget(self.purchase_label)
        self.purchase_mode=QComboBox(); self.purchase_mode.addItem('','click'); self.purchase_mode.addItem('','hold');self.purchase_mode.addItem('','repeat')
        self.purchase_mode.setCurrentIndex(self.purchase_mode.findData(self.prefs.settings.get('purchase_mode','hold')))
        row.addWidget(self.purchase_mode)
        self.hold_duration=QDoubleSpinBox(); self.hold_duration.setRange(.05,5); self.hold_duration.setSingleStep(.05)
        self.hold_duration.setValue(float(self.prefs.settings.get('hold_seconds',.65))); row.addWidget(self.hold_duration)
        layout.addLayout(row)
        self.calibrate_button=BloodButton(); self.calibrate_button.clicked.connect(self.open_calibration); layout.addWidget(self.calibrate_button)
        self.confirm_button=BloodButton(); self.confirm_button.setEnabled(False); self.confirm_button.clicked.connect(self.confirm_calibration); layout.addWidget(self.confirm_button)
        self.focus_notice=QLabel(); self.focus_notice.setWordWrap(True); layout.addWidget(self.focus_notice)
        row=QHBoxLayout();self.delay_label=QLabel();row.addWidget(self.delay_label)
        self.startup_delay=QSpinBox();self.startup_delay.setRange(3,3);self.startup_delay.setValue(3)
        self.startup_delay.valueChanged.connect(self.automation_settings_changed);row.addWidget(self.startup_delay);layout.addLayout(row)
        row=QHBoxLayout();self.transition_timeout_label=QLabel();row.addWidget(self.transition_timeout_label)
        self.transition_timeout=QSpinBox();self.transition_timeout.setRange(2,15);self.transition_timeout.setValue(self.prefs.settings['refresh_timeout'])
        self.transition_timeout.valueChanged.connect(self.automation_settings_changed);row.addWidget(self.transition_timeout);layout.addLayout(row)
        self.detailed_diagnostics=ToggleSwitch();self.detailed_diagnostics.setChecked(self.prefs.settings['detailed_diagnostics'])
        self.detailed_diagnostics.toggled.connect(self.automation_settings_changed);layout.addWidget(self.detailed_diagnostics)
        self.fast_early_levels=ToggleSwitch();self.fast_early_levels.setChecked(self.prefs.settings['fast_early_levels'])
        self.fast_early_levels.toggled.connect(self.automation_settings_changed);layout.addWidget(self.fast_early_levels)
        self.fast_opening=ToggleSwitch();self.fast_opening.setChecked(self.prefs.settings['fast_opening'])
        self.fast_opening.toggled.connect(self.automation_settings_changed);layout.addWidget(self.fast_opening)
        self.turbo_burst=ToggleSwitch();self.turbo_burst.setChecked(self.prefs.settings['turbo_burst'])
        self.turbo_burst.toggled.connect(self.automation_settings_changed);layout.addWidget(self.turbo_burst)
        self.native_chain=ToggleSwitch();self.native_chain.setChecked(self.prefs.settings['native_chain'])
        self.native_chain.toggled.connect(self.automation_settings_changed);layout.addWidget(self.native_chain)
        self.match_overlay=ToggleSwitch();self.match_overlay.setChecked(self.prefs.settings['match_overlay'])
        self.match_overlay.toggled.connect(self.match_overlay_toggled)
        self.overlay_status=QLabel();self.overlay_status.setObjectName('muted');self.overlay_status.setWordWrap(True)
        self.overlay_status.setContentsMargins(42,0,0,0)
        self.start_with_dbd=ToggleSwitch();self.start_with_dbd.clicked.connect(self.autostart_toggled)
        self.autostart_note=QLabel();self.autostart_note.setObjectName('muted');self.autostart_note.setWordWrap(True);self.autostart_note.hide()
        self.ds_timer=ToggleSwitch();self.ds_timer.setChecked(self.prefs.settings['ds_timer'])
        self.ds_timer.toggled.connect(self.ds_timer_toggled)
        row=QHBoxLayout();self.overlay_size_label=QLabel();row.addWidget(self.overlay_size_label)
        self.overlay_size=QComboBox();self.overlay_size.addItem('','large');self.overlay_size.addItem('','normal')
        self.overlay_size.setCurrentIndex(self.overlay_size.findData(self.prefs.settings['overlay_size']))
        self.overlay_size.currentIndexChanged.connect(self.overlay_size_changed);row.addWidget(self.overlay_size);layout.addLayout(row)
        self.experimental_persistent=ToggleSwitch();self.experimental_persistent.setChecked(self.prefs.settings['persistent_route'])
        self.experimental_persistent.toggled.connect(self.automation_settings_changed);layout.addWidget(self.experimental_persistent)
        self.shadow_planner=ToggleSwitch();self.shadow_planner.setChecked(self.prefs.settings['shadow_planner'])
        self.shadow_planner.toggled.connect(self.automation_settings_changed);layout.addWidget(self.shadow_planner)
        self.physical_f9_check=ToggleSwitch();self.physical_f9_check.setChecked(False)
        self.physical_f9_check.hide()  # Start verifies registration; physical Stop test remains available manually.
        row=QHBoxLayout();self.run_mode_label=QLabel();row.addWidget(self.run_mode_label)
        self.run_mode=QComboBox();self.run_mode.addItem('','test');self.run_mode.addItem('','continuous')
        self.run_mode.setCurrentIndex(1 if self.prefs.settings['run_mode']=='continuous' else 0)
        self.run_mode.currentIndexChanged.connect(self.automation_settings_changed);row.addWidget(self.run_mode);self.action_panel.layout().insertLayout(1,row)
        self.hotkeys_button=BloodButton(); self.hotkeys_button.clicked.connect(self.configure_hotkeys); layout.addWidget(self.hotkeys_button)
        self.warnings_button=BloodButton(); self.warnings_button.clicked.connect(self.show_notices); layout.addWidget(self.warnings_button)
        layout.addLayout(priority_mode_row)  # Developer panel only: normal users get one behaviour.
        self.dry_run.toggled.connect(self.automation_settings_changed)
        self.auto_prestige.toggled.connect(self.automation_settings_changed)
        self.purchase_mode.currentIndexChanged.connect(self.automation_settings_changed)
        self.hold_duration.valueChanged.connect(self.automation_settings_changed)
        body_layout.addWidget(card)

        card,layout=self.card(); self.counters_card=card; counters=QHBoxLayout(); self.counter_labels={}; self.counter_values={}
        for key in ('nodes','matched','targets'):
            column=QVBoxLayout(); value=QLabel('0'); value.setObjectName('value')
            label=QLabel(); label.setObjectName('muted'); column.addWidget(value); column.addWidget(label)
            counters.addLayout(column,1); self.counter_labels[key]=label; self.counter_values[key]=value
        layout.addLayout(counters)
        self.status=QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.state_label=QLabel(); self.state_label.setObjectName('muted'); self.state_label.setWordWrap(True); layout.addWidget(self.state_label)
        self.plan_label=QLabel(); self.plan_label.setObjectName('muted'); self.plan_label.setWordWrap(True); layout.addWidget(self.plan_label)
        body_layout.addWidget(card)

        card,layout=self.card(); self.preview_card=card
        self.main_preview=Preview(); self.main_preview.setMinimumHeight(180); self.main_preview.setMaximumHeight(260)
        layout.addWidget(self.main_preview); body_layout.addWidget(card)
        card,layout=self.card(); self.debug_card=card; self.debug_title=QToolButton(); self.debug_title.setCheckable(True)
        self.debug_title.setToolButtonStyle(Qt.ToolButtonTextBesideIcon); self.debug_title.setArrowType(Qt.RightArrow); layout.addWidget(self.debug_title)
        self.debug_panel=QWidget(); debug_layout=QVBoxLayout(self.debug_panel); debug_layout.setContentsMargins(0,0,0,0)
        layout.addWidget(self.debug_panel); self.debug_panel.hide()
        self.debug_title.toggled.connect(self.toggle_diagnostics)
        layout=debug_layout; layout.addLayout(sample_row)
        self.tabs=QTabWidget(); self.tabs.setMinimumHeight(230)
        self.preview=Preview(); self.tabs.addTab(self.preview,'')
        self.table=QTableWidget(0,6); self.table.verticalHeader().hide(); self.table.setIconSize(QSize(24,24))
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3,QHeaderView.ResizeToContents)
        self.table.cellDoubleClicked.connect(self.review_node)
        self.tabs.addTab(self.table,''); layout.addWidget(self.tabs)
        self.legend=QLabel(); self.legend.setObjectName('muted'); self.legend.setWordWrap(True); layout.addWidget(self.legend)
        self.legend.hide()
        self.diagnostics=QLabel(); self.diagnostics.setObjectName('muted'); self.diagnostics.setWordWrap(True); layout.addWidget(self.diagnostics)
        body_layout.addWidget(card); body_layout.addStretch()
        footer=QHBoxLayout(); self.mode_label=QLabel(); self.mode_label.setObjectName('muted'); footer.addWidget(self.mode_label); footer.addStretch()
        self.logs_button=BloodButton(); self.logs_button.setMinimumHeight(24); self.logs_button.setFixedHeight(24)
        self.logs_button.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.root/'debug'))))
        footer.addWidget(self.logs_button); outer.addLayout(footer)
        # One action panel stays visible above the scrolling library.
        outer.insertWidget(2,self.action_panel)
        self.stop_shortcut=QShortcut(QKeySequence('Esc'),self); self.stop_shortcut.activated.connect(self.stop_analysis)
        screen=self.screen().availableGeometry()
        width=min(screen.width(),max(420,round(screen.width()/3)))
        height=min(screen.height()-130,max(600,screen.height()-142))
        self.resize(width,height); self.move(screen.right()-width+1,screen.top()+130)
        self.setMinimumSize(min(420,screen.width()),min(600,screen.height()))
        self.settings_menu=self.menuBar().addMenu('')
        self.recalibrate_action=self.settings_menu.addAction('');self.recalibrate_action.triggered.connect(self.open_calibration)
        self.session_action=self.settings_menu.addAction('');self.session_action.triggered.connect(self.open_session_check)
        self.overlay_key_action=self.settings_menu.addAction('');self.overlay_key_action.triggered.connect(self.choose_overlay_key)
        self.logs_action=self.settings_menu.addAction('')
        self.logs_action.triggered.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.root/'debug'))))
        self.developer_action=self.settings_menu.addAction('');self.developer_action.setCheckable(True)
        self.developer_action.triggered.connect(self.set_developer_mode)
        self.logs_button.hide()
        self.buttons['start'].primary=True
        self.menuBar().hide()
        self.build_navigation()
        self.session_button.hide()
        for button in (self.scan_button,self.test_button):button.setFixedHeight(34);button.setMinimumHeight(34);button.setStyleSheet('font-size: 14px;')
        self.calibrate_button.hide();self.confirm_button.hide()
        self.apply_language(); self.refresh_library(); self.refresh_selected(); self.refresh_status()
        self.display_info.setText(self.locale.text('not_scanned'))
        self.set_developer_mode(self.prefs.settings['developer_mode'],save=False)
        from PySide6.QtCore import QTimer as _Timer
        self.overlay_status_timer=_Timer(self);self.overlay_status_timer.setInterval(1000)
        self.overlay_status_timer.timeout.connect(self.refresh_overlay_status)
        # A saved 'on' starts the overlay with the app: the checkbox was ticked before its signal was connected.
        self.apply_overlay(self.match_overlay.isChecked())
        self.apply_ds_timer(self.ds_timer.isChecked())
        self.apply_crosshair(self.prefs.settings['crosshair']['enabled'])
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0,self.refresh_autostart_action)

    def build_navigation(self):
        """Sidebar: the top action panel stays; pages replace only the area below it."""
        self.nav_group=QButtonGroup(self);self.nav_group.setExclusive(True);self.nav_buttons={}
        column=QVBoxLayout(self.sidebar);column.setContentsMargins(10,16,10,16);column.setSpacing(10)
        for index,kind in ((0,'bloodweb'),(2,'randomizer'),(3,'crosshair'),(1,'settings')):  # Page index in the stack.
            button=NavButton(kind);self.nav_group.addButton(button,index);column.addWidget(button);self.nav_buttons[kind]=button
        column.addStretch()
        self.nav_buttons['log']=NavButton('log');self.nav_buttons['log'].setCheckable(False)
        self.nav_buttons['log'].clicked.connect(self.logs_action.trigger);column.addWidget(self.nav_buttons['log'])
        self.nav_group.idClicked.connect(self.open_page);self.nav_buttons['bloodweb'].setChecked(True)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.NoFrame);self.pages.addWidget(scroll)
        page=QWidget();scroll.setWidget(page);layout=QVBoxLayout(page);layout.setContentsMargins(0,0,3,0);layout.setSpacing(10)
        self.page_settings_title=QLabel();self.page_settings_title.setObjectName('pageTitle');layout.addWidget(self.page_settings_title)
        card,inner=self.card()
        row=QHBoxLayout();self.language_label=QLabel();row.addWidget(self.language_label);row.addStretch();row.addWidget(self.language);inner.addLayout(row)
        inner.addWidget(self.separator())
        for widget in (self.start_with_dbd,self.autostart_note,self.match_overlay,self.overlay_status,self.ds_timer):inner.addWidget(widget)
        row=QHBoxLayout();self.overlay_position_label=QLabel();row.addWidget(self.overlay_position_label);row.addStretch()
        self.overlay_position=QComboBox();self.overlay_position.addItem('','left');self.overlay_position.addItem('','right')
        self.overlay_position.setCurrentIndex(self.overlay_position.findData(self.prefs.settings['overlay_position']))
        self.overlay_position.currentIndexChanged.connect(self.overlay_position_changed);row.addWidget(self.overlay_position);inner.addLayout(row)
        row=QHBoxLayout();self.overlay_scale_label=QLabel();row.addWidget(self.overlay_scale_label)
        self.overlay_scale=QSlider(Qt.Horizontal);self.overlay_scale.setRange(50,200);self.overlay_scale.setSingleStep(5);self.overlay_scale.setValue(self.prefs.settings['overlay_scale'])
        self.overlay_scale_value=QLabel();self.overlay_scale_value.setFixedWidth(40);self.overlay_scale.valueChanged.connect(self.overlay_scale_changed)
        row.addWidget(self.overlay_scale,1);row.addWidget(self.overlay_scale_value);inner.addLayout(row)
        layout.addWidget(card)
        card,inner=self.card();self.page_buttons={}
        for key,action in (('page_hotkeys',self.configure_hotkeys),('page_notice',self.show_notices),('page_report_bug',self.report_bug)):
            button=BloodButton();button.setMinimumHeight(42);button.clicked.connect(action);inner.addWidget(button);self.page_buttons[key]=button
        self.page_developer=ToggleSwitch();self.page_developer.setChecked(self.prefs.settings['developer_mode'])
        self.page_developer.clicked.connect(lambda:self.developer_action.trigger());inner.addWidget(self.page_developer)
        inner.addWidget(self.topmost)
        layout.addWidget(card)
        self.ui_scale_note=QLabel();self.ui_scale_note.setObjectName('notice');self.ui_scale_note.setWordWrap(True);layout.addWidget(self.ui_scale_note)
        card,inner=self.card();self.about_title=self.section_label();inner.addWidget(self.about_title);inner.addWidget(self.separator())
        self.about_text=QLabel();self.about_text.setObjectName('muted');self.about_text.setWordWrap(True);self.about_text.setOpenExternalLinks(True);inner.addWidget(self.about_text)
        # Optional donation details: shown inline only after the user clicks the link; no popups or reminders.
        self.support_link=QLabel();self.support_link.setObjectName('muted');self.support_link.linkActivated.connect(self.toggle_support);inner.addWidget(self.support_link)
        self.support_box=QWidget();box=QVBoxLayout(self.support_box);box.setContentsMargins(0,0,0,0);box.setSpacing(6)
        self.support_text=QLabel();self.support_text.setObjectName('muted');self.support_text.setWordWrap(True);box.addWidget(self.support_text)
        row=QHBoxLayout();self.support_address=QLineEdit(SUPPORT_ADDRESS);self.support_address.setReadOnly(True);row.addWidget(self.support_address,1)
        self.support_copy=BloodButton();self.support_copy.clicked.connect(self.copy_support_address);row.addWidget(self.support_copy);box.addLayout(row)
        self.support_warning=QLabel();self.support_warning.setObjectName('notice');self.support_warning.setWordWrap(True);box.addWidget(self.support_warning)
        self.support_box.hide();inner.addWidget(self.support_box)
        layout.addWidget(card);layout.addStretch()

        self.randomizer_host=QWidget();QVBoxLayout(self.randomizer_host).setContentsMargins(0,0,0,0);self.pages.addWidget(self.randomizer_host);self.randomizer_page=None
        self.crosshair_host=QWidget();QVBoxLayout(self.crosshair_host).setContentsMargins(0,0,0,0);self.pages.addWidget(self.crosshair_host);self.crosshair_page=None

    def toggle_support(self,_link=None):
        self.support_box.setVisible(self.support_box.isHidden())

    def copy_support_address(self):
        QApplication.clipboard().setText(SUPPORT_ADDRESS);self.support_copy.setText(self.locale.text('support_copied'))

    def report_bug(self):
        """Opens the GitHub issue form and the folder that holds app.log for the user to attach."""
        folder=self.root/'debug';folder.mkdir(exist_ok=True)
        QDesktopServices.openUrl(QUrl(ISSUES_URL));QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def open_page(self,index):
        """Randomizer and Crosshair are built on first use and get the whole height of the window."""
        if index==2 and self.randomizer_page is None:
            from src.core.randomizer import Pool
            from src.gui.randomizer_page import RandomizerPage
            pool=Pool(self.root,self.library,lambda c:self.locale.text('character_'+c))
            self.randomizer_page=RandomizerPage(self.root,pool,self.prefs.settings['randomizer'],self.save_preferences,self.locale.text)
            self.randomizer_host.layout().addWidget(self.randomizer_page)
        if index==3 and self.crosshair_page is None:
            from src.gui.crosshair_overlay import CrosshairPage
            self.crosshair_page=CrosshairPage(self.prefs.settings['crosshair'],self.save_preferences,self.locale.text)
            self.crosshair_page.changed.connect(self.crosshair_config_changed);self.crosshair_page.enabled_changed.connect(self.apply_crosshair)
            self.crosshair_host.layout().addWidget(self.crosshair_page)
        self.pages.setCurrentIndex(index);full=index in (2,3)
        self.action_panel.setVisible(not full);self.role.setVisible(not full);self.character.setVisible(not full and self.current_role=='killer')

    def crosshair_settings(self):
        from src.core.crosshair import preset_config
        data=self.prefs.settings['crosshair'];return preset_config(data['preset'],data['overrides'].get(data['preset']))

    def crosshair_config_changed(self,config):
        if getattr(self,'crosshair_controller',None):self.crosshair_controller.window.set_config(config)

    def apply_crosshair(self,enabled):
        controller=getattr(self,'crosshair_controller',None)
        if enabled and controller is None:
            from src.gui.crosshair_overlay import CrosshairController
            controller=self.crosshair_controller=CrosshairController(self.root,lambda:bool(self.auto_worker),self.prefs.settings['crosshair']['key'])
            controller.window.set_config(self.crosshair_settings())
        if controller:(controller.enable if enabled else controller.disable)()

    @property
    def current_role(self): return self.role.currentData()

    @property
    def active_selected(self):
        return self.prefs.priorities[self.current_role]

    @staticmethod
    def card():
        frame=QFrame(); frame.setObjectName('card')
        layout=QVBoxLayout(frame); layout.setContentsMargins(10,9,10,9); layout.setSpacing(7)
        return frame,layout

    @staticmethod
    def separator():
        line=QFrame();line.setObjectName('separator');line.setFixedHeight(1);return line

    @staticmethod
    def section_label():
        label=QLabel(); label.setObjectName('section'); return label

    def apply_language(self):
        t=self.locale.text
        for kind,button in self.nav_buttons.items():button.setToolTip(t('nav_'+kind))
        self.ds_timer.setText(t('ds_timer'));self.ds_timer.setToolTip(t('ds_timer_hint'))
        self.page_settings_title.setText(t('page_settings_title').upper());self.page_developer.setText(t('page_developer'))
        for key,button in self.page_buttons.items():button.setText(t(key))
        self.page_buttons['page_report_bug'].setToolTip(t('report_bug_tip'))
        self.support_link.setText(f'<a href="#support" style="color:#ff7a86">{t("support_link")}</a>');self.support_text.setText(t('support_text'))
        self.support_copy.setText(t('support_copy'));self.support_warning.setText(t('support_warning'))
        self.settings_menu.setTitle(t('settings_menu'));self.recalibrate_action.setText(t('recalibrate'))
        self.session_action.setText(t('session_check'))
        self.overlay_position_label.setText(t('overlay_position'));self.overlay_scale_label.setText(t('overlay_scale'));self.overlay_scale.setToolTip(t('overlay_scale_hint'));self.overlay_scale_value.setText(f'{self.overlay_scale.value()} %')
        for index,key in enumerate(('left','right')):self.overlay_position.setItemText(index,t('overlay_position_'+key))
        self.ui_scale_note.setText(t('ui_scale_note'));self.about_title.setText(t('about_title'));self.about_text.setText((t('about_text')+'<br>'+t('about_otz')).replace('<a ','<a style="color:#ff7a86" '));self.reset_button.setToolTip(t('reset_recommended'));self.reset_button.setAccessibleName(t('reset_recommended'));self.language_label.setText(t('language'));self.clear_button.setToolTip(t('clear'));self.clear_button.setAccessibleName(t('clear'))
        mapping={self.notice:'safe_notice',self.library_title:'library',self.library_hint:'info_right_click',
            self.selected_title:'selected',
            self.test_button:'test',self.scan_button:'scan',self.sample_button:'analyze',
            self.debug_title:'debug',self.legend:'legend',self.topmost:'topmost',self.mode_label:'mode',self.logs_button:'logs'}
        for widget,key in mapping.items(): widget.setText(t(key))
        for key,button in self.buttons.items(): button.setText(t(key)); button.setToolTip('')
        for widget,key in ((self.display_title,'display'),(self.dry_run,'dry_run'),(self.auto_prestige,'auto_prestige'),
            (self.purchase_label,'purchase_mode'),(self.calibrate_button,'calibrate'),(self.confirm_button,'confirm_calibration'),(self.focus_notice,'focus_delay')):
            widget.setText(t(key))
        self.purchase_mode.setItemText(0,t('click')); self.purchase_mode.setItemText(1,t('hold'))
        self.purchase_mode.setItemText(2,t('repeat_clicks'))
        self.hold_duration.setToolTip(t('hold_seconds'))
        self.mode_label.setText(t('hotkeys_v3',**self.prefs.settings['hotkeys'])); self.mode_label.setWordWrap(True)
        self.hotkeys_button.setText(t('hotkey_settings')); self.warnings_button.setText(t('warnings_button'))
        self.session_button.setText(t('restore_input_method' if not self.calibration and self.prefs.recovery_calibration() else 'session_check'));self.delay_label.setText(t('startup_delay'))
        self.transition_timeout_label.setText(t('transition_timeout'))
        self.detailed_diagnostics.setText(t('detailed_diagnostics'))
        self.fast_early_levels.setText(t('fast_early_levels'));self.fast_early_levels.setToolTip(t('fast_early_hint'))
        self.fast_opening.setText(t('fast_opening'));self.fast_opening.setToolTip(t('fast_opening_hint'))
        self.turbo_burst.setText(t('turbo_burst'));self.turbo_burst.setToolTip(t('turbo_burst_hint'))
        self.native_chain.setText(t('native_chain'));self.native_chain.setToolTip(t('native_chain_hint'))
        self.match_overlay.setText(t('match_overlay'));self.match_overlay.setToolTip(t('match_overlay_hint'))
        self.overlay_key_action.setText(t('overlay_key'))
        self.overlay_size_label.setText(t('overlay_size'));self.overlay_size.setItemText(0,t('overlay_size_large'));self.overlay_size.setItemText(1,t('overlay_size_normal'))
        self.level_limit_label.setText(t('level_limit_label'));self.level_limit.setSpecialValueText(t('level_limit_none'))
        self.level_limit.setToolTip(t('level_limit_hint'))
        self.start_with_dbd.setText(t('start_with_dbd'));self.start_with_dbd.setToolTip(t('start_with_dbd_hint'))
        self.logs_action.setText(t('logs'));self.developer_action.setText(t('developer_mode'))
        self.hotkey_hint.setText(t('hotkey_hint',**self.prefs.settings['hotkeys']))
        self.experimental_persistent.setText(t('experimental_persistent'));self.experimental_persistent.setToolTip(t('experimental_persistent_hint'))
        self.shadow_planner.setText(t('shadow_planner'));self.shadow_planner.setToolTip(t('shadow_planner_hint'))
        self.physical_f9_check.setText(t('physical_f9_check'));self.physical_f9_check.setToolTip(t('physical_f9_hint'))
        self.run_mode_label.setText(t('run_mode'));self.run_mode.setItemText(0,t('test_mode'));self.run_mode.setItemText(1,t('continuous_mode'))
        self.calibrate_button.setText(t('recalibrate' if self.calibration else 'calibrate'))
        self.priority_mode_label.setText(t('priority_mode'))
        for index,mode in enumerate(('top_first','smart','strict')):self.priority_mode.setItemText(index,t(mode))
        self.focus_notice.setText(t('wizard_conditions'))
        self.auto_prestige.setToolTip(t('prestige_verified_hint'))
        for key,label in self.counter_labels.items(): label.setText(t(key))
        self.role.blockSignals(True); self.role.setItemText(0,t('survivor')); self.role.setItemText(1,t('killer')); self.role.blockSignals(False)
        for index in range(self.character.count()):
            character=self.character.itemData(index)
            self.character.setItemText(index,t('character_'+character))
            from src.core.localization import STRINGS
            from src.core.killer_aliases import aliases_for
            self.character.set_search_tokens(index,[character,character.replace('_',' '),
                *(names.get('character_'+character,character) for names in STRINGS.values()),
                *aliases_for(self.root,character),*tierlist.aliases(self.root,character)])
            if not getattr(self.library,'portraits',{}).get(character):self.character.setItemData(index,t('portrait_review'),Qt.ToolTipRole)
        self.character.search_hint=t('killer_search');self.character.setToolTip(self.character.currentText()+'\n'+t('killer_search'))
        self.role.setToolTip(self.role.currentText())
        category=self.category.currentData()
        self.category.blockSignals(True); self.category.clear()
        for key in ('all','items','addons','offerings','perks','event') if self.current_role=='survivor' else ('all','addons','offerings','perks','event'):
            self.category.addItem(t(key),None if key=='all' else key)
        self.category.setCurrentIndex(max(0,self.category.findData(category))); self.category.blockSignals(False)
        self.tabs.setTabText(0,t('preview')); self.tabs.setTabText(1,t('details'))
        self.table.setHorizontalHeaderLabels([t('number'),t('content'),t('state'),t('geometry_score'),t('state_score'),t('icon_candidate_score')])
        self.samples.setToolTip(t('sample'))
        self.main_preview.setToolTip(t('zoom')+' · '+t('preview'))
        if self.preview.original.isNull(): self.preview.setText(t('empty_preview'))
        self.refresh_selected(); self.refresh_status(); self.refresh_table()
        self.library_count.setText(t('library_count',count=len(self.icon_grid.entries)))

    def change_language(self):
        self.locale.language=self.language.currentData(); self.apply_language(); self.save_preferences()

    def role_changed(self):
        self.stop_analysis(); self.confirm_button.setEnabled(False)
        self.emergency_tested_signature=None;self.calibration_signature=None;self.resume_engine=None
        self.last_live=None;self.last_file=None;self.preview_signature=None
        self.character.setVisible(self.current_role=='killer')
        self.last_analysis=None; self.preview.original=self.preview.original.__class__(); self.preview.clear()
        self.main_preview.original=self.main_preview.original.__class__(); self.main_preview.clear()
        self.status_key='ready'; self.error_message=''; self.apply_language(); self.refresh_library(); self.save_preferences()

    def character_changed(self):
        self.prefs.activate_character(self.character.currentData()); self.role_changed(); self.refresh_selected()

    def refresh_library(self):
        entries=self.library.filter(self.current_role,self.category.currentData(),self.character.currentData() or 'blight')
        target=perk_target_entry(self.root,self.current_role);self.entries_by_id[PERK_TARGET]=target
        if self.category.currentData() in (None,'perks'):entries=entries+[target]
        if self.current_role=='killer': entries=sorted(entries,key=lambda e:e.character is None)
        self.icon_grid.set_entries(entries,self.active_selected,getattr(self.library,'display_images',{}))
        for entry in entries:self.icon_grid.buttons[entry.id].setToolTip(self.item_tooltip(entry.id))
        if PERK_TARGET in self.icon_grid.buttons:self.icon_grid.buttons[PERK_TARGET].setAccessibleName('PERKS')
        self.library_count.setText(self.locale.text('library_count',count=len(entries)))
        if self.current_role=='killer' and self.character.currentData() in getattr(self.library,'wiki_status',{}):
            status=self.library.wiki_status[self.character.currentData()]
            self.library_count.setText(self.library_count.text()+'\n'+self.locale.text('wiki_library_status',count=status['addons']))

    def item_tooltip(self,ident):
        t=self.locale.text;info=self.item_info.get(ident);metadata=getattr(self.library,'event_metadata',{}).get(ident)
        if ident==PERK_TARGET:lines=[t('any_perk_hint')]
        elif metadata:lines=[metadata['name'],t(metadata['availability']),metadata['page']]
        elif info:lines=[info.name]+([info.rarity] if info.rarity else [])
        else:lines=[]
        icon=QUrl.fromLocalFile(str(self.root/'assets/ui/mouse_right_click.svg')).toString()
        return ''.join(f'<div>{escape(line)}</div>' for line in lines)+(
            f'<table cellspacing="0"><tr><td><img src="{icon}" width="24" height="24"></td>'
            f'<td valign="middle">&nbsp;{escape(t("info_tooltip_hint"))}</td></tr></table>')

    def show_item_details(self,ident,anchor=None):
        t=self.locale.text;entry=self.entries_by_id.get(ident)
        if entry is None:return
        if self.info_card is None:self.info_card=InfoCard(self)
        image=self.root/getattr(self.library,'display_images',{}).get(ident,entry.image)
        if ident==PERK_TARGET:
            self.info_card.show_item(image,'PERKS','','',t('any_perk_hint'),'','',anchor);return
        info=self.item_info.get(ident);index=self.character.findData(entry.character) if entry.character else -1
        owner=self.character.itemText(index) if index>=0 else t('info_all_killers' if entry.role=='killer' else 'info_all_survivors')
        if entry.character in PTB_CHARACTERS:owner+=' · '+t('info_ptb')
        note=tierlist.comment(self.root,ident);note=t('info_otz_opinion',text=note) if note else ''
        if info:self.info_card.show_item(image,info.name,info.rarity,owner,info.description,info.flavor,t('info_source'),anchor,note)
        else:self.info_card.show_item(image,getattr(self.library,'event_metadata',{}).get(ident,{}).get('name',t('info_unnamed')),'',owner,t('info_missing'),'','',anchor)

    def toggle_icon(self,ident):
        role=self.current_role if ident==PERK_TARGET else self.entries_by_id[ident].role; priorities=self.prefs.priorities[role]
        if ident in priorities: priorities.remove(ident); self.selected.discard(ident)
        else: priorities.append(ident); self.selected.add(ident)
        if ident in self.icon_grid.buttons: self.icon_grid.buttons[ident].setChecked(ident in priorities)
        self.refresh_selected(); self.refresh_status(); self.save_preferences()

    def recommended_list(self):
        return tierlist.recommended(self.root,[self.character.currentData()]).get(self.character.currentData()) if self.current_role=='killer' else None

    def reset_recommended(self):
        t=self.locale.text;ids=self.recommended_list()
        if not ids:return
        box=QMessageBox(self);box.setWindowTitle('AutoBloodWeb');box.setText(t('reset_recommended_confirm'))
        yes=box.addButton(t('yes').capitalize(),QMessageBox.YesRole);box.addButton(t('no').capitalize(),QMessageBox.NoRole);box.exec()
        if box.clickedButton() is yes:self.apply_recommended(ids)

    def apply_recommended(self,ids):
        valid=[i for i in ids if i==PERK_TARGET or i in self.entries_by_id];self.active_selected[:]=valid
        for ident,button in self.icon_grid.buttons.items():button.setChecked(ident in valid)
        self.refresh_selected();self.refresh_status();self.save_preferences()

    def confirm_clear(self):
        t=self.locale.text;box=QMessageBox(self);box.setWindowTitle('AutoBloodWeb');box.setText(t('clear_confirm'))
        yes=box.addButton(t('yes').capitalize(),QMessageBox.YesRole);box.addButton(t('no').capitalize(),QMessageBox.NoRole);box.exec()
        if box.clickedButton() is yes:self.clear_selection()

    def clear_selection(self):
        self.selected-=set(self.active_selected)
        self.prefs.priorities[self.current_role]=[]
        for button in self.icon_grid.buttons.values(): button.setChecked(False)
        self.refresh_selected(); self.refresh_status(); self.save_preferences()

    def refresh_selected(self):
        self.priority_list.populate(self.active_selected,self.entries_by_id,self.root,getattr(self.library,'display_images',{}),
                                    context=(self.current_role,self.character.currentData() if self.current_role=='killer' else None))
        for i in range(self.priority_list.count()):
            self.priority_list.item(i).setToolTip(self.item_tooltip(self.priority_list.item(i).data(Qt.UserRole)))
        self.clear_button.setEnabled(bool(self.active_selected));self.reset_button.setVisible(bool(self.recommended_list()))

    def refresh_status(self):
        t=self.locale.text; result=self.last_analysis
        shown=(self.status_key,getattr(self,'error_message',''))
        if shown!=getattr(self,'_logged_status',None) and shown[1] and not self.auto_worker:
            # A Start refused before the worker exists leaves no action report; keep the reason.
            log.warning('Status %s: %s',*shown)
        self._logged_status=shown
        stages={'purchasing':'top_buying','wait_purchase':'top_checking','recovering':'top_rechecking',
                'paused':'top_paused','stopped':'top_stopped','error':'top_paused',
                'wait_refresh':'top_level','finished':'top_level','scanning':'top_wait_dbd','wait_stable':'top_rechecking'}
        stages.update(wait_next_bloodweb='loading_next_bloodweb',manual_action='manual_action')
        key='top_retry' if getattr(self,'action_stage_message','')=='purchase_retry_one' else stages.get(self.status_key,'top_ready')
        if getattr(self,'action_stage_message','') in ('auto_start_switch','auto_start_scanning','continue_upgrading'):key=self.action_stage_message
        self.action_status.setText(t(key));self.action_reason.setText(getattr(self,'covered_by','') or self.error_message.split('\n',1)[0])
        running=bool(self.auto_worker or self.worker);failed=self.status_key=='error'
        problem=failed or self.status_key=='paused' or (bool(self.error_message) and not running) or getattr(self,'covered_by','')!=''
        self.status_dot.setStyleSheet('border-radius:4px;background:%s;'%('#e0283c' if failed else '#e6b422' if problem else '#3ecf74'))
        self.action_reason.setVisible(problem and bool(self.error_message));self.status_pill.setVisible(running or problem)
        action=f" · {t('last_action')}: {t(self.last_action_key)}" if self.last_action_key else ''
        self.status.setText(t(self.status_key) + (f' · {self.error_message}' if self.error_message else action))
        self.counter_values['nodes'].setText(str(len(result.nodes) if result else 0))
        self.counter_values['matched'].setText(str(sum(n.icon_id is not None for n in result.nodes) if result else 0))
        self.counter_values['targets'].setText(str(sum(n.icon_id in self.active_selected or (PERK_TARGET in self.active_selected and perk_eligible(n)) for n in result.nodes) if result else 0))
        if result:
            self.state_label.setText(f"{t('state')}: {t(result.screen_state.value)} · {t('level')}: {result.level or '—'}")
            plan=getattr(result,'automation_plan',None) if getattr(result,'automation_priorities',None)==tuple(self.active_selected) and getattr(result,'automation_priority_mode',None)==self.priority_mode.currentData() else None
            if plan is None:plan=plan_route(result,self.active_selected,self.priority_mode.currentData())
            if hasattr(result,'raw_image'):
                signature=(id(result),tuple(plan.route),plan.next_node)
                if getattr(self,'preview_signature',None)!=signature:
                    pixels=BloodwebDetector.render_preview(result.raw_image,result,plan)
                    self.preview.set_pixels(pixels);self.main_preview.set_pixels(pixels)
                    self.preview_signature=signature
            reason=t('persistent_route_reason',reason=t(plan.reason.split(':',1)[1],node=plan.next_node)) if plan.reason.startswith('PERSISTENT:') else t(plan.reason,node=plan.next_node)
            self.plan_label.setText(f"{t('plan')}: {reason}")
            frame=getattr(result,'live_frame',None)
            if frame and plan.next_node is not None:
                node=next(n for n in result.nodes if n.id==plan.next_node)
                self.plan_label.setText(self.plan_label.text()+f' · {frame.to_screen(node.x,node.y)}')
            self.diagnostics.setText('\n'.join(self.locale.diagnostic(m) for m in result.diagnostics)+'\n'+t('debug_saved'))
            notice=getattr(self,'direct_burst_notice',None)
            if notice and notice.get('level')==result.level:
                self.diagnostics.setText(self.diagnostics.text()+'\n'+self.direct_burst_notice_text(notice))
            shadow=result.details.get('shadow_planner')
            if shadow:
                self.diagnostics.setText(self.diagnostics.text()+'\n'+t('shadow_summary',
                    old=shadow['old_choice'],persistent=shadow['persistent_choice'],chain=shadow['chain_choice'],
                    nodes=' → '.join(str(i['node']) for i in shadow['chain_items']) or '—',
                    saved=shadow['hypothetical_saved_commands'],extra=', '.join(map(str,shadow['extra_items'])) or '—',
                    risk=t('yes') if shadow['higher_priority_risk'] else t('no')))
        else:
            self.state_label.setText(t('static_read_only')); self.plan_label.clear(); self.diagnostics.clear()

    def refresh_table(self):
        result=self.last_analysis
        self.table.setRowCount(len(result.nodes) if result else 0)
        if not result: return
        for row,node in enumerate(result.nodes):
            self.table.setRowHeight(row,36)
            content=QTableWidgetItem(self.locale.text(node.type.value))
            if node.type.value=='unknown': content.setText(self.locale.text('unknown_content'))
            if node.recognition_status=='ambiguous':content.setText(self.locale.text('ambiguous'))
            if node.icon_id:
                content.setIcon(QIcon(str(self.root/self.entries_by_id[node.icon_id].image)))
                content.setToolTip(node.icon_id)
            elif node.candidate_id:
                content.setToolTip(f'{node.candidate_id} (?)')
            if node.candidates:
                content.setToolTip('\n'.join(f'{ident}: {score:.3f}' for ident,score in node.candidates)+
                    f'\nΔ={node.icon_margin:.4f} · {self.locale.text(node.match_reason)}\nContours={node.contour_score:.3f}'+
                    '\n'+self.locale.text('category_evidence',category=node.category_hint,score=node.category_confidence)+
                    '\n'+self.locale.text('rarity_evidence',rarity=node.rarity,score=node.rarity_confidence)+
                    ('\n'+self.locale.text('color_tie_break') if node.rarity_tie_break else '')+
                    ('\n'+self.locale.text('glyph_detail_evidence',score=node.glyph_detail_score) if node.glyph_detail_tie_break else '')+
                    ('\n'+self.locale.text('gradient_evidence') if node.recognition_method=='gradient' else ''))
            score=f'{node.confidence:.3f}' if node.icon_id else self.locale.text(node.recognition_status)+' · '+f'{node.confidence:.3f}'
            if not node.candidates:
                content.setToolTip(content.toolTip()+'\n'+self.locale.text('category_evidence',category=node.category_hint,score=node.category_confidence)+'\n'+self.locale.text('rarity_evidence',rarity=node.rarity,score=node.rarity_confidence))
            values=[QTableWidgetItem(f'{node.id} · R{node.ring}'),content,QTableWidgetItem(self.locale.text(node.state.value)),
                QTableWidgetItem(f'{node.geometry_confidence:.3f}'),QTableWidgetItem(f'{node.state_confidence:.3f}'),QTableWidgetItem(score)]
            for col,item in enumerate(values): self.table.setItem(row,col,item)

    def analyze_sample(self):
        if self.samples.currentData(): self.start_analysis(self.samples.currentData())

    def choose_screenshot(self):
        path,_=QFileDialog.getOpenFileName(self,self.locale.text('select_file'),str(self.root/'references/bloodweb'),'Images (*.png *.webp *.jpg *.jpeg *.bmp)')
        if path: self.start_analysis(Path(path))

    def start_analysis(self,path):
        if self.worker is not None or self.auto_worker is not None: return
        self.cancelled=False; self.last_action_key='file_source' if path else 'live_source'
        self.last_analysis=None; self.preview.original=self.preview.original.__class__(); self.preview.clear()
        self.main_preview.original=self.main_preview.original.__class__(); self.main_preview.clear()
        self.preview.setText(self.locale.text('busy')); self.refresh_table()
        self.status.setToolTip(str(path) if path else self.locale.text('scan'))
        self.error_message=''; self.status_key='busy'; self.refresh_status(); self.set_busy(True)
        log.info('Analyze role=%s source=%s',self.current_role,path or 'game_window')
        self.worker=AnalysisWorker(self.library,self.current_role,path,self,character=self.character.currentData() or 'blight',
            detailed=self.detailed_diagnostics.isChecked())
        self.worker.completed.connect(self.analysis_complete); self.worker.failed.connect(self.analysis_failed)
        self.worker.failed_detail.connect(self.analysis_failed_detail)
        self.worker.finished.connect(self.worker_finished); self.worker.start()

    def set_busy(self,busy):
        if busy and self.info_card is not None:self.info_card.hide()  # Must not cover the Bloodweb.
        self.recalibrate_action.setEnabled(not busy)
        self.session_action.setEnabled(not busy)
        for widget in (self.test_button,self.scan_button,self.sample_button,self.samples,self.role,self.transition_timeout,self.fast_early_levels,self.fast_opening,self.experimental_persistent,self.shadow_planner,self.physical_f9_check,self.auto_prestige): widget.setEnabled(not busy)
        self.buttons['stop'].setEnabled(busy)
        self.buttons['start'].setEnabled(not busy)
        self.calibrate_button.setEnabled(not busy)
        self.session_button.setEnabled(not busy)
        self.character.setEnabled(not busy)

    def analysis_complete(self,result):
        if self.cancelled: return
        self.last_analysis=result; self.status_key='analysis_done'; self.preview.set_image(result.debug_image)
        self.main_preview.set_image(result.debug_image)
        frame=getattr(result,'live_frame',None)
        if frame:
            self.last_live=result
            c=frame.window.client
            self.display_info.setText(f'{frame.window.title}\n{c.width}×{c.height} · ({c.x}, {c.y}) · DPI {frame.window.dpi} · {frame.provider}')
        else: self.last_file=result
        self.status.setToolTip(self.locale.text('live_source' if frame else 'file_source'))
        log.info('Result state=%s nodes=%s recognized=%s',result.screen_state,len(result.nodes),sum(n.icon_id is not None for n in result.nodes))
        self.refresh_status(); self.refresh_table()
        QTimer.singleShot(0,lambda:self.body_scroll.ensureWidgetVisible(self.main_preview,0,20))

    def analysis_failed(self,message):
        if self.cancelled: return
        self.status_key='error'
        from src.core.localization import STRINGS
        expected=message in STRINGS['en']
        self.error_message=self.locale.text(message) if expected else self.locale.text('error_details')
        self.preview.setText(self.locale.text('unknown'))
        self.refresh_status()
        if not expected:
            QMessageBox.warning(self,self.locale.text('error'),self.locale.text('analysis_failed',message=message))

    def worker_finished(self):
        self.set_busy(False)
        if self.worker: self.worker.deleteLater()
        self.worker=None

    def analysis_failed_detail(self,code,detail):
        if detail:
            self.error_message=self.locale.text(code)+'\n'+detail; self.refresh_status()

    def stop_analysis(self):
        if getattr(self,'priority_list',None):self.priority_list.cancel_drag()
        if self.session_check and getattr(self.session_check,'auto_start',False):
            self.session_check.close();self.countdown.hide();self.set_busy(False);self._start_prepared=False
        if self.auto_worker: self.auto_worker.engine.halt()
        self.cancelled=True
        if self.worker: self.worker.requestInterruption()
        if self.worker: self.preview.setText(self.locale.text('stopped'))
        if not self.auto_worker:self.error_message=''  # A refused Start leaves nothing to report after Stop.
        self.status_key='stopped'; self.refresh_status(); log.info('Stop requested (Esc or Stop)')

    def set_topmost(self,checked):
        self.setWindowFlag(Qt.WindowStaysOnTopHint,checked); self.show()

    def reorder_priorities(self,ids):
        if set(ids)!=set(self.active_selected) or len(ids)!=len(self.active_selected): return
        self.prefs.priorities[self.current_role]=ids
        if self.auto_worker:self.auto_worker.engine.priorities=list(ids)
        self.refresh_status(); self.save_preferences()

    def toggle_diagnostics(self,checked):
        self.debug_panel.setVisible(checked)
        self.debug_title.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)

    def automation_settings_changed(self,*args):
        self.prefs.settings['turbo_burst']=self.turbo_burst.isChecked()
        self.prefs.settings['native_chain']=self.native_chain.isChecked()
        self.prefs.settings['match_overlay']=self.match_overlay.isChecked()
        self.prefs.settings.update(dry_run=self.dry_run.isChecked(),auto_prestige=self.auto_prestige.isChecked(),
            purchase_mode=self.purchase_mode.currentData(),hold_seconds=self.hold_duration.value(),priority_mode=self.priority_mode.currentData(),
            startup_delay=self.startup_delay.value(),detailed_diagnostics=self.detailed_diagnostics.isChecked(),run_mode=self.run_mode.currentData())
        self.prefs.settings['refresh_timeout']=self.transition_timeout.value()
        self.prefs.settings['fast_early_levels']=self.fast_early_levels.isChecked()
        self.prefs.settings['fast_opening']=self.fast_opening.isChecked()
        self.prefs.settings['persistent_route']=self.experimental_persistent.isChecked()
        self.prefs.settings['shadow_planner']=self.shadow_planner.isChecked()
        self.prefs.settings['physical_f9_check']=self.physical_f9_check.isChecked()
        if self.calibration and (self.calibration.get('mode',self.purchase_mode.currentData())!=self.purchase_mode.currentData()
                or self.calibration.get('hold_seconds',self.hold_duration.value())!=self.hold_duration.value()):
            self.invalidate_calibration('input_method_changed')
            self.calibration=None; self.confirm_button.setEnabled(False)
        self.save_preferences()

    def hotkey_received(self,action):
        # Immediate release in the shortcut thread, even if Qt is busy painting.
        if action in ('stop','unavailable') and self.auto_worker: self.auto_worker.engine.halt()
        if action=='unavailable':
            self.native_hotkey_event.emit(dict(stage='hotkey_safety_lost',error=getattr(self.hotkeys,'error_detail','')))
            self.global_action.emit('stop'); return
        if action=='stop':
            wizard=self.calibration_wizard
            result=wizard.result if wizard else None
            if self.session_check:result=self.session_check.result
            frame=result.live_frame if result else None
            native=getattr(self.hotkeys,'current_event',None) or {}
            foreground=native.get('foreground_info',{}).get('hwnd') or self.get_foreground_hwnd()
            log.info('Global Stop received foreground=%s expected_game=%s',foreground,frame.window.hwnd if frame else None)
            if frame:
                from src.core.hotkey_diagnostics import window_metadata,compare_game_focus
                focus=native.get('foreground_info') or window_metadata(foreground)
                expected=window_metadata(frame.window.hwnd)
                matches,reason=compare_game_focus(focus,expected)
                self.physical_stop_received.emit(dict(signature=frame.window.signature,
                    foreground=foreground,expected_game=frame.window.hwnd,foreground_info=focus,expected_game_info=expected,
                    focus_matches=matches,focus_reason=reason,native_event=native,
                    shortcut=native.get('shortcut',self.prefs.settings['hotkeys']['stop'])))
            elif self.hotkey_diagnostic_only:
                from src.core.hotkey_diagnostics import window_metadata,compare_game_focus
                focus=native.get('foreground_info') or window_metadata(foreground)
                expected=self.hotkey_test_game or dict(hwnd=0,pid=0)
                matches,reason=compare_game_focus(focus,expected)
                self.native_hotkey_event.emit(dict(stage='B_diagnostic_game_focus',foreground_info=focus,
                    expected_game_info=expected,focus_matches=matches,focus_reason=reason,
                    calibration_state='diagnostic_only_no_purchase',calibration_accepted=False))
        if self.hotkey_diagnostic_only: return
        if action in ('pause','toggle') and self.auto_worker:
            self.auto_worker.user_paused=True
            self.auto_worker.engine.pause.set();self.auto_worker.engine.halt()
            self.global_action.emit('pause_label'); return
        self.global_action.emit(action)

    def record_emergency_test(self,packet):
        self.hotkey_trace.add('B_game_focus',**packet)
        if self.calibration_wizard: self.calibration_wizard.receive_emergency_test(packet)
        if self.session_check:self.session_check.receive_emergency_test(packet)

    def attach_hotkey_diagnostics(self):
        self.hotkeys.diagnostic=self.native_hotkey_event.emit
        for packet in getattr(self.hotkeys,'events',[]): self.record_native_hotkey_event(packet)

    def record_native_hotkey_event(self,packet):
        details={key:value for key,value in packet.items() if key not in ('stage','time')}
        details['native_time']=packet.get('time')
        self.hotkey_trace.add(packet['stage'],**details)
        if packet['stage'] in ('hotkey_thread_error','hotkey_safety_lost'):
            self.emergency_tested_signature=None
            if self.calibration_wizard: self.calibration_wizard.refresh_gate()

    def open_hotkey_diagnostics(self):
        if self.auto_worker: self.stop_analysis()
        from src.gui.hotkey_diagnostics_dialog import HotkeyDiagnosticsDialog
        HotkeyDiagnosticsDialog(self).exec()

    def handle_global_action(self,action):
        if action=='stop':
            if self.preparation_hotkey_check_consumed:
                self.preparation_hotkey_check_consumed=False;return
            self.stop_analysis()
        elif action=='start': self.start_automation()
        elif action=='pause': self.pause_automation()
        elif action=='pause_label': self.update_pause_label()
        elif action=='toggle':
            if self.auto_worker: self.pause_automation()
            elif self.calibration_wizard:
                self.calibration_wizard.start_purchase()
            else: self.start_automation()

    def update_pause_label(self):
        paused=bool(self.auto_worker and self.auto_worker.engine.pause.is_set())
        self.buttons['pause'].setText(self.locale.text('resume' if paused else 'pause'))

    def pause_automation(self):
        if self.auto_worker:
            self.auto_worker.user_paused=True
            self.auto_worker.engine.pause.set();self.auto_worker.engine.halt();self.update_pause_label()
        elif self.status_key=='paused': self.start_automation()

    def invalidate_calibration(self,reason):
        if self.calibration:
            self.calibration['blocked_reason']=reason
            self.prefs.data['calibration']=dict(self.calibration)
        self.calibration=None;self.emergency_tested_signature=None
        self.calibrate_button.setText(self.locale.text('calibrate'))
        self.save_preferences()

    def open_session_check(self,auto_start=False):
        # clicked(bool) is not the automatic-start flag.
        auto_start=auto_start is True
        if self.worker or self.auto_worker or self.calibration_wizard:return
        if auto_start and hasattr(self.hotkeys,'native_health') and not getattr(self,'preparation_window_check',None):
            from src.core.elevation import needs_elevation,request_restart
            from src.core.screen_manager import WindowsBackend
            if needs_elevation(WindowsBackend()):
                # A visible user Start authorizes preparation; UAC remains a
                # separate Windows consent prompt. No session proof is copied.
                self.save_preferences()
                if request_restart(self.root,autostart=True,live_session=getattr(self,'live_session_path',None),
                                   preferences=self.preferences_path if self.preferences_path!=self.root/'data/preferences.json' else None):
                    self.hotkeys.close();self.close()
                else:
                    self.status_key='paused';self.error_message=self.locale.text('elevation_cancelled');self.refresh_status()
                return
        if not self.calibration or not self.calibration.get('user_confirmed'):
            from src.core.input_method import propose_method_recovery
            restored,reason=propose_method_recovery(self.root,self.prefs)
            if restored is None:
                self.status_key='paused'
                needs_mode_check=auto_start and reason in ('input_recovery_method_changed','input_recovery_not_allowed')
                self.error_message=self.locale.text('input_mode_calibration_required' if needs_mode_check else reason)
                self.refresh_status()
                if needs_mode_check:self.open_calibration()
                return
            self.calibration=restored;self.method_recovery_pending=True
        if self.session_check:self.session_check.show();return
        from src.gui.session_check_dialog import SessionCheckDialog
        dialog=SessionCheckDialog(self,auto_start=auto_start)
        if auto_start:dialog.begin()
        else:dialog.show()

    def commit_method_recovery(self):
        if not self.method_recovery_pending:return
        self.method_recovery_pending=False
        self.calibration['method_recovery']['requires_new_physical_f9']=False
        self.calibration['method_recovery']['current_check_kind']=getattr(self,'current_session_check_kind','physical')
        memory=self.calibration.pop('recovered_purchase_scene',None)
        key=self.calibration.pop('recovered_scene_key',None)
        if memory is not None and key:
            self.prefs.data.setdefault('purchase_scenes',{}).setdefault(key,memory)
        self.save_preferences();self.session_button.setText(self.locale.text('session_check'))
        self.calibrate_button.setText(self.locale.text('recalibrate'))

    def cancel_method_recovery(self):
        if self.method_recovery_pending:
            self.method_recovery_pending=False;self.calibration=None

    def confirm_calibration(self):
        if not self.calibration or not self.calibration.get('verified'): return
        self.calibration['user_confirmed']=True
        self.calibrate_button.setText(self.locale.text('recalibrate'))
        self.confirm_button.setEnabled(False); self.save_preferences()
        self.status_key='ready'; self.error_message=''; self.refresh_status()

    def set_developer_mode(self,enabled,save=True):
        """Engineering controls stay in code but appear only in developer mode.

        A normal user's Start must buy: the hidden dry-run and test-run choices
        are reset whenever developer mode is off."""
        enabled=bool(enabled)
        self.developer_action.setChecked(enabled)
        if hasattr(self,'page_developer'):self.page_developer.setChecked(enabled)
        for widget in (self.topmost,self.buttons['pause'],self.scan_button,self.test_button,self.run_mode_label,self.run_mode,
                self.display_card,self.counters_card,self.preview_card,self.debug_card,self.mode_label,self.library_count):
            widget.setVisible(enabled)
        self.recalibrate_action.setVisible(enabled);self.session_action.setVisible(enabled)
        if not enabled and not (self.worker or self.auto_worker):
            self.dry_run.setChecked(False);self.run_mode.setCurrentIndex(self.run_mode.findData('continuous'))
        if save:
            self.prefs.settings['developer_mode']=enabled;self.save_preferences()

    def refresh_autostart_action(self):
        from src.core.autostart import task_installed
        try:installed=task_installed()
        except OSError:installed=False
        self.start_with_dbd.blockSignals(True);self.start_with_dbd.setChecked(installed);self.start_with_dbd.blockSignals(False)

    def autostart_toggled(self,enabled):
        from src.core.autostart import install_task,uninstall_task
        try:install_task(self.root) if enabled else uninstall_task()
        except OSError as exc:
            self.start_with_dbd.setChecked(not enabled);log.warning('Start with DBD failed: %s',exc)
            self.autostart_note.setText(self.locale.text('start_with_dbd_failed'));self.autostart_note.show();return
        self.autostart_note.hide()

    def enter_tray_mode(self):
        """Started with DBD: hidden in the tray, overlay on, purchases only on F8/Start."""
        from PySide6.QtWidgets import QSystemTrayIcon,QMenu
        self.tray=QSystemTrayIcon(app_icon(self.root,tray=True),self)
        menu=QMenu(self);self.tray_menu=menu;t=self.locale.text
        menu.addAction(t('tray_open')).triggered.connect(self.show_from_tray)
        menu.addAction(t('tray_start')).triggered.connect(self.start_automation)
        menu.addAction(t('tray_stop')).triggered.connect(self.stop_analysis)
        self.tray_overlay_action=menu.addAction(t('match_overlay'));self.tray_overlay_action.setCheckable(True)
        self.tray_overlay_action.toggled.connect(self.match_overlay.setChecked)
        menu.addAction(t('tray_exit')).triggered.connect(self.exit_app)
        self.tray.setContextMenu(menu);self.tray.setToolTip('AutoBloodWeb');self.tray.show()
        self.tray.activated.connect(lambda reason:self.show_from_tray() if reason==QSystemTrayIcon.Trigger else None)
        self.match_overlay.setChecked(True);self.tray_overlay_action.setChecked(True)
        self.refresh_overlay_status();self.hide()
        from PySide6.QtCore import QTimer
        self.game_watch=QTimer(self);self.game_watch.setInterval(3000);self.game_watch.timeout.connect(self.exit_when_game_closes)
        self.game_watch.start()

    def show_from_tray(self):
        self.show();self.raise_()

    def exit_app(self):
        """Closing a hidden window does not end Qt's loop; without quit() the process stayed and
        held the single-instance lock, so every later launch closed silently (live 2026-11-08)."""
        self.close();QApplication.quit()

    def exit_when_game_closes(self,running=None):
        from src.core.autostart import game_running
        if not (game_running() if running is None else running):
            log.info('DBD closed: exiting the tray app');self.exit_app()

    def choose_overlay_key(self):
        from PySide6.QtWidgets import QInputDialog
        from src.gui.match_overlay import key_code
        key,ok=QInputDialog.getText(self,self.locale.text('overlay_key'),self.locale.text('overlay_key_prompt'),text=self.prefs.settings['overlay_key'])
        if not ok:return
        try:key_code(key)
        except (ValueError,KeyError):return self.diagnostics.setText(self.locale.text('overlay_key_invalid'))
        self.prefs.settings['overlay_key']=key.strip().upper();self.save_preferences()
        controller=getattr(self,'overlay_controller',None)
        if controller and controller.mode!='off':
            controller.disable();controller.key=self.prefs.settings['overlay_key'];controller.enable()

    def level_limit_changed(self,value):
        self.prefs.settings['max_levels']=int(value);self.save_preferences()

    def overlay_size_changed(self):
        self.prefs.settings['overlay_size']=self.overlay_size.currentData();self.save_preferences()
        if getattr(self,'overlay_controller',None):self.overlay_controller.set_size(self.prefs.settings['overlay_size'])

    def overlay_position_changed(self):
        self.prefs.settings['overlay_position']=self.overlay_position.currentData();self.save_preferences()
        for controller in (getattr(self,'overlay_controller',None),getattr(self,'ds_controller',None)):
            if controller:controller.overlay.position=self.prefs.settings['overlay_position'];controller.overlay.update()

    def overlay_scale_changed(self,value):
        self.prefs.settings['overlay_scale']=int(value);self.overlay_scale_value.setText(f'{value} %');self.save_preferences()
        for controller in (getattr(self,'overlay_controller',None),getattr(self,'ds_controller',None)):
            if controller:controller.overlay.scale=value/100;controller.overlay.update()

    def ds_timer_toggled(self,enabled):
        self.prefs.settings['ds_timer']=bool(enabled);self.save_preferences();self.apply_ds_timer(enabled)

    def apply_ds_timer(self,enabled):
        controller=getattr(self,'ds_controller',None)
        if enabled and controller is None:
            from src.gui.ds_overlay import DsTimerController
            controller=self.ds_controller=DsTimerController(self.root,lambda:self.current_role=='killer',lambda:bool(self.auto_worker),
                expired_seconds=self.prefs.settings['ds_expired_seconds'])
            controller.overlay.position=self.prefs.settings['overlay_position'];controller.overlay.scale=self.prefs.settings['overlay_scale']/100
            controller.status.connect(self.overlay_status_received)
        if controller:(controller.enable if enabled else controller.disable)()

    def match_overlay_toggled(self,enabled):
        self.automation_settings_changed()
        self.apply_overlay(enabled)

    def apply_overlay(self,enabled):
        controller=getattr(self,'overlay_controller',None)
        if enabled and controller is None:
            from src.gui.match_overlay import MatchOverlayController
            controller=self.overlay_controller=MatchOverlayController(self.root,lambda:bool(self.auto_worker),is_killer=lambda:self.current_role=='killer',key=self.prefs.settings['overlay_key'],
                size=self.prefs.settings['overlay_size'])
            controller.overlay.position=self.prefs.settings['overlay_position'];controller.overlay.scale=self.prefs.settings['overlay_scale']/100
            controller.status.connect(self.overlay_status_received)
        if controller:(controller.enable if enabled else controller.disable)()
        (self.overlay_status_timer.start if enabled else self.overlay_status_timer.stop)()
        self.refresh_overlay_status()

    def overlay_status_received(self,key):
        self.diagnostics.setText(self.locale.text(key));self.refresh_overlay_status()
        if key=='ui_scale_unsupported':self.covered_by=self.locale.text(key);self.refresh_status()

    def refresh_overlay_status(self):
        """One line under the overlay checkbox and in the tray tooltip."""
        t=self.locale.text;controller=getattr(self,'overlay_controller',None)
        state,count,names=controller.summary() if controller else ('off',0,'off')
        text=t('overlay_status_'+state,count=count)
        if state!='off' and names=='off':text+=' · '+t('overlay_status_no_names')
        self.overlay_status.setText(text);self.overlay_status.setVisible(self.match_overlay.isChecked())
        if getattr(self,'tray',None):self.tray.setToolTip('AutoBloodWeb'+chr(10)+text)

    def start_automation(self,checked=False,calibrate=False):
        if self.worker or self.auto_worker or self.session_check: return
        if getattr(self,'overlay_controller',None):self.overlay_controller.suspend()
        if getattr(self,'crosshair_controller',None):self.crosshair_controller.window.hide()
        if self.pages.currentIndex()!=0:self.nav_buttons['bloodweb'].setChecked(True);self.open_page(0)
        calibration_window_proof=None
        if calibrate:
            # Keep only the current armed wizard context before invalidation
            # consumes its physical Stop-test signature. This is not a receipt.
            wizard=self.calibration_wizard
            if wizard and wizard.gate_snapshot()['reason']=='wizard_armed':
                frame=wizard.result.live_frame
                target=(wizard.target.x,wizard.target.y,frame.window.signature,frame.roi)
                if target==self.calibration_selection:
                    import time
                    calibration_window_proof=dict(target=target,created=time.monotonic())
            # A new attempt must not retain approval from an older purchase.
            self.invalidate_calibration('new_calibration_attempt')
            self.calibration=None; self.confirm_button.setEnabled(False)
        dry_run=self.dry_run.isChecked() and not calibrate
        approved=bool(self.calibration and self.calibration.get('user_confirmed'))
        if not dry_run and not calibrate:
            # Start is the user's ordinary input authorization. The worker
            # checks two new maps, current focus/access and live Stop health;
            # a historical test purchase or physical F9 is not a prerequisite.
            health=self.hotkeys.native_health() if hasattr(self.hotkeys,'native_health') else dict(ready=False)
            if not health['ready']:
                log.warning('Start refused: Stop hotkey not live %s',health)
                self.status_key='paused';self.error_message=self.locale.text('hotkey_registration_failed');self.refresh_status();return
            approved=True
        settings=Settings(dry_run=dry_run,auto_prestige=self.auto_prestige.isChecked(),
            prestige_price_calibration=self.prefs.settings.get('prestige_price_calibration'),
            fast_early_levels=self.fast_early_levels.isChecked(),
            fast_opening=self.fast_opening.isChecked(),
            turbo_burst=self.turbo_burst.isChecked(),
            burst_mid_level=self.prefs.settings['burst_mid_level'],burst_gap=self.prefs.settings['burst_gap'],
            recovery_supervisor=not dry_run and not calibrate,
            persistent_route=self.experimental_persistent.isChecked(),native_chain=self.native_chain.isChecked(),
            experimental_persistent=self.prefs.settings['experimental_persistent'],shadow_planner=self.shadow_planner.isChecked(),
            purchase_mode=self.purchase_mode.currentData(),hold_seconds=self.hold_duration.value(),
            startup_delay=0 if self._start_prepared else 3,priority_mode=self.priority_mode.currentData(),refresh_timeout=self.transition_timeout.value(),
            run_mode=self.run_mode.currentData(),max_actions=5 if self.run_mode.currentData()=='test' else None,
            max_input_commands=5 if self.run_mode.currentData()=='test' else None,
            max_runtime=120 if self.run_mode.currentData()=='test' else None,
            detailed_diagnostics=self.detailed_diagnostics.isChecked(),
            max_levels=self.level_limit.value() or None,
            center_auto_above_9=not self.prefs.settings.get('center_auto_unavailable'))
        self._start_prepared=False
        settings.character=self.character.currentData() or 'blight'
        engine=make_engine(self.library,self.current_role,self.active_selected,settings,approved,self.hotkeys.ready)
        if not dry_run and not calibrate and hasattr(engine.screen,'backend') and hasattr(engine.screen.backend,'access_status'):
            from src.core.elevation import needs_elevation,request_restart
            if needs_elevation(engine.screen.backend):
                self.save_preferences()
                if request_restart(self.root,autostart=True,live_session=getattr(self,'live_session_path',None),
                                   preferences=self.preferences_path if self.preferences_path!=self.root/'data/preferences.json' else None):self.close()
                else:
                    self.status_key='paused';self.error_message=self.locale.text('input_integrity_mismatch');self.refresh_status()
                return
        engine.prestige_approved=self.auto_prestige.isChecked()
        import time
        engine.input_ready_check=lambda:(self.hotkeys.native_health()['ready'] if hasattr(self.hotkeys,'native_health') else calibrate and self.hotkeys.ready)
        key=self.current_role+'/'+(settings.character if self.current_role=='killer' else '')
        try:self.prefs.refresh_purchase_scenes()
        except (OSError,ValueError):
            self.status_key='paused';self.error_message=self.locale.text('purchase_result_unknown');self.refresh_status();return
        memory=self.prefs.data.get('purchase_scenes',{}).get(key)
        if memory and not dry_run:
            import copy
            engine.persisted_scene=copy.deepcopy(memory);engine.scene_record=copy.deepcopy(memory)
            engine.attempted=[tuple(p) for p in memory.get('attempted',[])]
        engine.calibration_target=self.calibration_selection if calibrate else None
        engine.calibration_window_proof=calibration_window_proof
        if calibrate and self.calibration_wizard and self.calibration_wizard.result:
            engine.screen=self.calibration_wizard.result.capture_manager
        if approved and not dry_run:
            try:
                self.calibration_signature=engine.screen.backend.info().signature
            except ScreenError as exc:
                self.emergency_tested_signature=None; self.status_key='paused'; self.error_message=self.locale.text(str(exc)); self.refresh_status(); return
        if self.resume_engine and not calibrate and not dry_run:
            import time
            previous=self.resume_engine
            if previous.signature==self.calibration_signature:
                remaining=getattr(previous,'runtime_deadline',time.monotonic())-time.monotonic()
                if previous.settings.run_mode!=settings.run_mode:
                    self.error_message=self.locale.text('resume_mode_changed');self.refresh_status();return
                limit=previous.settings.max_actions
                input_limit=previous.settings.max_input_commands
                if remaining<=0 or (limit is not None and previous.actions+previous.unconfirmed_level_actions+previous.prestige_actions>=limit) or (input_limit is not None and previous.attempts>=input_limit):
                    self.error_message=self.locale.text('runtime_limit' if remaining<=0 else 'action_limit');self.refresh_status();return
                engine.actions=previous.actions;engine.unconfirmed_level_actions=previous.unconfirmed_level_actions;engine.attempted=list(previous.attempted)
                engine.attempts=previous.attempts;engine.entity_actions=previous.entity_actions;engine.transitions=previous.transitions
                engine.prestige_actions=previous.prestige_actions
                engine.settings.max_input_commands=input_limit
                engine.entity_points=list(previous.entity_points) if previous.entity_points is not None else None
                engine.completed_layout=previous.completed_layout
                engine.transition_verified=previous.transition_verified;engine.settings.max_runtime=remaining if remaining!=float('inf') else None
            self.resume_engine=None
        if not calibrate and not dry_run: self.emergency_tested_signature=None  # One-use live session proof.
        if not calibrate and not dry_run and getattr(self,'live_session_path',None):
            from src.core.live_session import LiveSession
            try:
                if not getattr(self,'live_session',None):self.live_session=LiveSession(self.live_session_path)
                self.live_session.install(engine)
            except (OSError,ValueError,ScreenError) as exc:
                self.status_key='paused';self.error_message=str(exc);self.refresh_status();return
        if not dry_run and not calibrate and not self.topmost.isChecked():
            self.setAttribute(Qt.WA_ShowWithoutActivating,True)
            self.topmost.setChecked(True)
        if not dry_run and not calibrate:self.keep_clear_of_web(engine);self.keep_clear_of_game_hud(engine)
        self.auto_worker=AutomationWorker(engine,calibrate,self)
        self.last_action_key='live_source'; self.last_analysis=None
        self.auto_worker.update.connect(self.automation_update)
        self.auto_worker.finished.connect(self.automation_finished)
        memory=engine.persisted_scene or {}
        if not dry_run and (settings.auto_prestige or (memory.get('terminal_transition') or {}).get('screen')=='prestige_ready'):
            self.set_reward_layout(True)
        self.set_busy(True); self.buttons['pause'].setEnabled(True)
        for w in (self.dry_run,self.auto_prestige,self.purchase_mode,self.hold_duration,self.priority_list,self.clear_button,self.confirm_button,self.hotkeys_button,self.priority_mode,self.startup_delay,self.detailed_diagnostics,self.run_mode): w.setEnabled(False)
        self.priority_list.setEnabled(True);self.priority_list.allow_remove=False  # Reorder stays live; removing waits for Stop.
        self.icon_grid.setEnabled(False)
        self.runtime_started_at=time.monotonic()
        self.error_message=''; self.auto_worker.start()

    def keep_clear_of_web(self,engine):
        """The panel lives on the right; the web is in the left two-thirds of the game."""
        try:client=engine.screen.backend.info().client
        except (ScreenError,AttributeError):return
        ratio=self.devicePixelRatioF() or 1;limit=round((client.x+client.width*2/3)/ratio);frame=self.frameGeometry()
        if frame.left()<limit<=self.screen().availableGeometry().right()-frame.width()+1:
            self.move(limit,frame.top());log.info('Moved AutoBloodWeb window off the Bloodweb')
        elif frame.left()<limit:
            self.move(self.screen().availableGeometry().right()-frame.width()+1,frame.top());log.info('Moved AutoBloodWeb window off the Bloodweb')

    def keep_clear_of_game_hud(self,engine):
        """Capture blanks covered pixels, so covering the HUD would hide the BP counter."""
        try:client=engine.screen.backend.info().client
        except (ScreenError,AttributeError):return
        ratio=self.devicePixelRatioF() or 1;frame=self.frameGeometry()
        left,top=frame.left()*ratio,frame.top()*ratio;right,bottom=frame.right()*ratio,frame.bottom()*ratio
        hud_left=client.x+client.width*.6;hud_bottom=client.y+client.height*.15
        if right<hud_left or left>client.x+client.width or bottom<client.y or top>=hud_bottom:return
        self.move(frame.left(),round(hud_bottom/ratio))
        room=self.screen().availableGeometry().bottom()-self.frameGeometry().top()
        if self.frameGeometry().height()>room>=self.minimumHeight():self.resize(self.width(),room-(self.frameGeometry().height()-self.height()))
        log.info('Moved AutoBloodWeb window below the game HUD')

    def set_reward_layout(self,compact):
        # Keep controls/status visible on the right while freeing the native
        # bottom-right OK button. Restore the user's exact window afterwards.
        saved=getattr(self,'_reward_layout',None)
        if compact and saved is None:
            self._reward_layout=(self.geometry(),self.minimumSize())
            self.pages.hide();self.setMinimumHeight(0)
            self.resize(self.width(),min(self.height(),520))
            # The reward title row (y~23-27% of the client) carries the item name, which can be
            # long enough to run under this window (live 7.11 20:36: "...CHARM UNLO|"). Sit
            # between the title and the bottom-right OK instead.
            # Both reward searches have a margin (4% of the height): the title band ends at ~31%,
            # the OK band starts at ~80%. The whole frame must stay between them.
            screen=self.screen().geometry() if self.screen() else None
            if screen is not None:
                extra=self.frameGeometry().height()-self.height()
                self.resize(self.width(),max(200,min(self.height(),round(screen.height()*.48)-extra)))
                self.move(self.x(),screen.y()+round(screen.height()*.31))
        elif not compact and saved is not None:
            self._reward_layout=None;self.pages.show()
            self.setMinimumSize(saved[1]);self.setGeometry(saved[0])

    def update_runtime_summary(self,engine):
        import time
        started=getattr(self,'runtime_started_at',None)
        if started is None:self.runtime_started_at=time.monotonic();started=self.runtime_started_at
        speed=getattr(engine,'actions',0)*60/max(1,time.monotonic()-started)
        self.runtime_summary.setText(self.locale.text('runtime_progress',done=getattr(engine,'levels_completed',0),
            limit=getattr(getattr(engine,'settings',None),'max_levels',None) or '∞',speed=speed))
        self.runtime_summary.show()

    def direct_burst_notice_text(self,notice):
        from src.core.localization import STRINGS
        key='direct_burst_reason_'+notice['reason']
        reason=self.locale.text(key) if key in STRINGS['en'] else notice['reason']
        return self.locale.text('direct_burst_fallback_notice',level=notice.get('level') or '—',
            node=notice.get('node') or '—',reason=reason)

    def automation_update(self,state,message,packet):
        if getattr(self,'_closing',False):return
        log.info('Automation state=%s reason=%s',state,message)
        if self.auto_worker:self.update_runtime_summary(self.auto_worker.engine)
        if getattr(self,'live_session',None) and self.auto_worker:
            self.live_session.status(self.auto_worker.engine,state.value,message)
        if message in ('direct_burst_fallback','direct_burst_ready'):
            old=getattr(self,'direct_burst_notice',None)
            text=self.diagnostics.text()
            if old:text=text.replace(self.direct_burst_notice_text(old),'').strip()
            self.direct_burst_notice=dict(packet) if message=='direct_burst_fallback' else None
            notice=self.direct_burst_notice_text(packet) if self.direct_burst_notice else ''
            self.diagnostics.setText((text+'\n'+notice).strip())
            self.turbo_burst.setToolTip(self.locale.text('turbo_burst_hint')+('\n'+notice if notice else ''))
            return
        self.status_key=state.value
        if message:self.action_stage_message=message
        elif state!=State.RECOVERING:self.action_stage_message=''
        if message=='prepare_countdown':
            self.countdown.setText(str(packet['seconds']));self.countdown.show()
        elif message!='restore_window':self.countdown.hide()
        from src.core.localization import STRINGS
        node=packet[2].next_node if isinstance(packet,tuple) else None
        self.covered_by=''
        if message=='bloodweb_occluded' and self.auto_worker:
            backend=getattr(self.auto_worker.engine.screen,'backend',None);own=getattr(backend,'own_occlusions',[])
            names=[d['title'] or d['cls'] for d in getattr(backend,'occluders',[]) if d['rect'] not in own]
            if names:self.covered_by=self.locale.text('covered_by',names=', '.join(names));log.warning('Bloodweb covered by %s',getattr(backend,'occluders',[]))
        if message not in ('restore_window','hide_for_dialog'):
            if message:
                values=dict(node=node)
                if isinstance(packet,dict): values.update(packet)
                self.error_message=self.locale.text(message,**values) if message in STRINGS['en'] else self.locale.text('error_details')
                if isinstance(packet,dict) and packet.get('error_detail'): self.error_message+='\n'+packet['error_detail']
            elif state not in (State.PAUSED,State.ERROR): self.error_message=''
        if message=='restore_window' and not self.isVisible():
            self.setAttribute(Qt.WA_ShowWithoutActivating,True); self.show()
        if isinstance(packet,tuple):
            frame,result,plan=packet
            if self.auto_worker and not self.auto_worker.engine.settings.dry_run:
                from src.core.models import ScreenState
                self.set_reward_layout(result.screen_state in (ScreenState.REWARD,ScreenState.PRESTIGE_READY)
                    or result.level==50)
            result.live_frame=frame
            detector=self.auto_worker.engine.detector
            result.renderer=detector; result.raw_image=frame.image
            result.automation_plan=plan
            engine=self.auto_worker.engine
            result.automation_priorities=tuple(getattr(engine,'priorities',self.active_selected))
            result.automation_priority_mode=getattr(getattr(engine,'settings',None),'priority_mode',self.priority_mode.currentData())
            self.last_live=result; self.last_analysis=result
            c=frame.window.client
            self.display_info.setText(f'{frame.window.title}\n{c.width}×{c.height} · ({c.x}, {c.y}) · DPI {frame.window.dpi} · {frame.provider}')
            self.refresh_table()
        elif isinstance(packet,dict) and message=='calibration_verified':
            self.calibration=packet; self.calibration['role']=self.current_role; self.calibration['character']=self.character.currentData()
            self.calibration_signature=self.auto_worker.engine.signature
            self.save_preferences()
        self.refresh_status()
        if self.calibration_wizard: self.calibration_wizard.update_status(self.status.text())

        if state==State.DRY_RUN:
            QTimer.singleShot(0,lambda:self.body_scroll.ensureWidgetVisible(self.main_preview,0,20))

    def automation_finished(self):
        self.countdown.hide();self.runtime_summary.hide()
        self.set_reward_layout(False)
        worker=self.auto_worker; self.auto_worker=None
        if worker:
            self.save_purchase_scene(worker.engine)
            if getattr(worker.engine,'center_auto_unavailable',False) and not self.prefs.settings.get('center_auto_unavailable'):
                # Verified in the game: the centre does not auto-buy above level 9. Remember it.
                self.prefs.settings['center_auto_unavailable']=True;self.save_preferences()
            self.last_action_report=worker.engine.report
            if getattr(worker,'user_paused',False):self.status_key='paused';self.resume_engine=worker.engine
            failures=[e.get('reason') for e in worker.engine.report.entries if e['stage']=='failure']
            unresolved=worker.engine.pending_action
            input_errors=('sendinput_failed','cursor_move_failed','cursor_read_failed','cursor_position_mismatch','cursor_outside_node','mouse_still_held')
            error=next((r for r in failures if r in input_errors),None)
            if error:self.invalidate_calibration(error)
            elif unresolved:
                self.prefs.data['last_purchase_outcome']=dict(reason=worker.engine.last_purchase_outcome or 'purchase_result_unknown',report=str(worker.engine.report.path))
                self.save_preferences()
            worker.deleteLater()
        self.set_busy(False); self.buttons['pause'].setEnabled(self.status_key=='paused')
        self.buttons['pause'].setText(self.locale.text('resume' if self.status_key=='paused' else 'pause'))
        for w in (self.dry_run,self.purchase_mode,self.hold_duration,self.priority_list,self.clear_button,self.hotkeys_button,self.priority_mode,self.startup_delay,self.detailed_diagnostics,self.run_mode): w.setEnabled(True)
        self.priority_list.allow_remove=True
        self.icon_grid.setEnabled(True)
        self.confirm_button.setEnabled(bool(self.calibration and self.calibration.get('verified') and not self.calibration.get('user_confirmed')))
        if self.calibration_wizard: self.calibration_wizard.finished()
        self.refresh_status()

    def save_purchase_scene(self,engine):
        memory=engine.scene_record
        if memory:
            import copy
            memory=copy.deepcopy(memory);memory['attempted']=[list(p) for p in engine.attempted]
            key=self.current_role+'/'+(engine.settings.character if self.current_role=='killer' else '')
            self.prefs.data.setdefault('purchase_scenes',{})[key]=memory
            self.save_preferences()

    def open_calibration(self):
        if self.calibration_wizard: self.calibration_wizard.show(); self.calibration_wizard.raise_(); return
        if self.calibration:self.invalidate_calibration('explicit_recalibration')
        from src.gui.calibration_dialog import CalibrationDialog
        CalibrationDialog(self).show()

    def configure_hotkeys(self):
        from src.core.hotkey_config import validate_bindings,DEFAULT_BINDINGS
        dialog=QDialog(self); dialog.setWindowTitle(self.locale.text('hotkey_settings')); layout=QVBoxLayout(dialog)
        fields={}
        for action,key in (('toggle','hotkey_toggle'),('stop','hotkey_stop')):
            layout.addWidget(QLabel(self.locale.text(key)))
            fields[action]=QLineEdit(self.prefs.settings['hotkeys'][action]); layout.addWidget(fields[action])
        layout.addWidget(QLabel(self.locale.text('cross_key_prompt')));crosshair=QLineEdit(self.prefs.settings['crosshair']['key']);layout.addWidget(crosshair)
        layout.addWidget(QLabel(self.locale.text('overlay_key_prompt')));overlay_key=QLineEdit(self.prefs.settings['overlay_key']);layout.addWidget(overlay_key)
        error=QLabel(); error.setWordWrap(True); layout.addWidget(error)
        reset=BloodButton(self.locale.text('reset_defaults')); layout.addWidget(reset)
        reset.clicked.connect(lambda:[fields[k].setText(v) for k,v in DEFAULT_BINDINGS.items()])
        check=BloodButton(self.locale.text('hotkey_diagnostics')); layout.addWidget(check)
        check.clicked.connect(self.open_hotkey_diagnostics)
        apply=BloodButton(self.locale.text('apply')); layout.addWidget(apply)
        def commit():
            bindings={k:f.text().strip() for k,f in fields.items()}
            try: validate_bindings(bindings)
            except ValueError as exc: error.setText(self.locale.text(str(exc))); return
            from src.core.hotkey_config import extra_keys_conflict
            from src.gui.crosshair_overlay import valid_key
            from src.gui.match_overlay import key_code
            cross=valid_key(crosshair.text());details=overlay_key.text().strip().upper()
            try:key_code(details)
            except (ValueError,KeyError):details=None
            if cross is None or details is None or extra_keys_conflict(bindings,cross,details):
                error.setText(self.locale.text('hotkey_conflict'));return
            if cross!=self.prefs.settings['crosshair']['key']:
                self.prefs.settings['crosshair']['key']=cross
                if getattr(self,'crosshair_controller',None):self.crosshair_controller.set_key(cross)
                if self.crosshair_page:self.crosshair_page.publish()
            if details!=self.prefs.settings['overlay_key']:
                self.prefs.settings['overlay_key']=details;controller=getattr(self,'overlay_controller',None)
                if controller and controller.mode!='off':controller.disable();controller.key=details;controller.enable()
            self.hotkeys.close(); self.hotkeys=GlobalHotkeys(self.hotkey_received,bindings)
            self.attach_hotkey_diagnostics()
            self.emergency_tested_signature=None
            self.confirm_button.setEnabled(False)
            if self.calibration_wizard:
                self.calibration_wizard.confirm.setEnabled(False); self.calibration_wizard.refresh_gate()
            self.prefs.settings['hotkeys']=bindings; self.save_preferences(); self.apply_language()
            if not self.hotkeys.ready: error.setText(self.locale.text('hotkey_registration_failed')+'\n'+self.hotkeys.error_detail)
            else: dialog.accept()
        apply.clicked.connect(commit); dialog.exec()

    def notice_due(self):
        """A dismissed notice comes back when its content version changes."""
        from src.core.notice_strings import NOTICE_VERSION
        return not self.prefs.data.get('hide_notices') or self.prefs.data.get('notice_version')!=NOTICE_VERSION

    def show_notices(self):
        dialog=QDialog(self); dialog.setWindowTitle(self.locale.text('startup_notice')); layout=QVBoxLayout(dialog)
        from src.core.notice_strings import NOTICE_SECTIONS,NOTICE_VERSION
        for key in NOTICE_SECTIONS:
            title=QLabel(self.locale.text(key+'_title')); title.setStyleSheet('font-weight:700;font-size:15px;'); layout.addWidget(title)
            label=QLabel(self.locale.text(key)); label.setWordWrap(True); layout.addWidget(label)
        hide=QCheckBox(self.locale.text('warning_hide')); hide.setChecked(bool(self.prefs.data.get('hide_notices'))); layout.addWidget(hide)
        ack=BloodButton(self.locale.text('warning_ack')); layout.addWidget(ack)
        def accept():
            self.prefs.data['hide_notices']=hide.isChecked(); self.prefs.data['notice_version']=NOTICE_VERSION; self.save_preferences(); dialog.accept()
        ack.clicked.connect(accept); dialog.resize(620,640); dialog.exec()

    def review_node(self,row,column=0):
        if self.last_analysis is None or self.auto_worker: return
        from src.gui.review_dialog import ReviewDialog
        ReviewDialog(self,self.last_analysis,self.last_analysis.nodes[row]).exec()

    def save_preferences(self):
        try:
            self.preferences_path.parent.mkdir(parents=True,exist_ok=True)
            self.prefs.save(self.locale.language,self.current_role,self.calibration)
        except OSError: log.exception('Could not save preferences')

    def closeEvent(self,event):
        # A queued final restore_window must not resurrect a just-closed GUI
        # after waiting for its worker. That also kept the live scope locked.
        self._closing=True
        if getattr(self,'ds_controller',None):self.ds_controller.disable()
        if getattr(self,'overlay_controller',None):self.overlay_controller.disable()
        if getattr(self,'crosshair_controller',None):self.crosshair_controller.disable()
        if getattr(self,'tray',None):self.tray.hide()
        if self.session_check:self.session_check.close()
        if self.auto_worker:
            self.auto_worker.engine.halt(); self.auto_worker.wait()
            self.save_purchase_scene(self.auto_worker.engine)
            if self.auto_worker.engine.pending_action:
                self.prefs.data['last_purchase_outcome']=dict(reason='purchase_result_unknown',report=str(self.auto_worker.engine.report.path))
        self.hotkeys.close()
        self.save_preferences()
        if self.worker:
            self.worker.requestInterruption()
            self.worker.wait()
        super().closeEvent(event)
