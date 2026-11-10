"""Whether a topmost overlay can appear over the game. Queries Windows only."""
import ctypes

QUNS_RUNNING_D3D_FULL_SCREEN=3


def exclusive_fullscreen(query=None):
    """True when Windows reports an exclusive Direct3D fullscreen app in front."""
    state=ctypes.c_int(0)
    try:
        status=(query or ctypes.windll.shell32.SHQueryUserNotificationState)(ctypes.byref(state))
    except (OSError,AttributeError):return False
    return status==0 and state.value==QUNS_RUNNING_D3D_FULL_SCREEN
