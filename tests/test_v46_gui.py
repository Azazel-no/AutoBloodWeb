"""Transactional historical restoration and unknown-scene persistence; no OS input."""
import copy
from tests.test_gui import app,isolate_preferences_and_hotkeys
from src.gui.main_window import MainWindow


def test_cancel_method_recovery_does_not_modify_saved_receipt(app,library):
    window=MainWindow(library)
    old=dict(mode='click',hold_seconds=.25,verified=True,user_confirmed=True,blocked_reason='purchase_not_confirmed')
    window.prefs.data['calibration']=copy.deepcopy(old)
    window.calibration=dict(old);window.calibration.pop('blocked_reason');window.method_recovery_pending=True
    window.cancel_method_recovery()
    assert window.calibration is None and window.prefs.data['calibration']==old
    window.close()


def test_committed_method_recovery_keeps_scene_guard_and_f9_separate(app,library):
    window=MainWindow(library);window.method_recovery_pending=True
    memory=dict(pending=dict(point=[684,685]),attempted=[[684,685]])
    window.calibration=dict(verified=True,user_confirmed=True,mode='click',hold_seconds=.25,
        method_recovery=dict(requires_new_physical_f9=True),recovered_purchase_scene=copy.deepcopy(memory),recovered_scene_key='survivor/')
    window.commit_method_recovery()
    assert window.prefs.data['purchase_scenes']['survivor/']==memory
    assert not window.calibration['method_recovery']['requires_new_physical_f9']
    assert window.emergency_tested_signature is None
    assert 'recovered_purchase_scene' not in window.calibration
    window.close()
