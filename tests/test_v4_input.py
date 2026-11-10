import ctypes,threading
import pytest
from src.core.input_control import Mouse
from src.core.screen_manager import ScreenError
from tests.test_input_control import InputStub


class Function:
    def __init__(self,call): self.call=call
    def __call__(self,*args): return self.call(*args)


@pytest.mark.parametrize('readback_matches',[True,False])
def test_compatible_cursor_api_still_requires_physical_readback(readback_matches):
    mouse=Mouse(); stub=InputStub(); mouse.u=stub; contexts=[]; point=(-400,240)
    stub.SetPhysicalCursorPos=lambda *args:0
    stub.SetThreadDpiAwarenessContext=Function(lambda context:contexts.append(context) or 17)
    stub.SetCursorPos=Function(lambda x,y:setattr(stub,'point',(x,y)) or 1)
    def read(pointer):
        pointer._obj.x=point[0] if readback_matches else 0
        pointer._obj.y=point[1]
        return 1
    stub.GetPhysicalCursorPos=read
    calls=[]; mouse.audit=lambda stage,**values:calls.append(stage)
    if readback_matches:
        mouse.purchase(point,.05,threading.Event(),lambda:None)
        assert stub.flags==[2,4] and not mouse.held
    else:
        with pytest.raises(ScreenError,match='cursor_position_mismatch'):
            mouse.purchase(point,.05,threading.Event(),lambda:None)
        assert not stub.flags
    assert contexts[0].value==ctypes.c_void_p(-4).value and contexts[-1]==17
    assert 'SetPhysicalCursorPos' in calls and 'SetCursorPos_physical_context' in calls
