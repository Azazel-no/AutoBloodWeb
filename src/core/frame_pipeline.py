"""Per-capture visual work. No state or purchase is inferred from a cache."""
from dataclasses import dataclass,field
import weakref
from src.core.bloodweb_header import text_mask
from src.core.video_ui import certificate

FAST_FRAME_CHECK='FAST_FRAME_CHECK'
SCENE_TRANSITION_CHECK='SCENE_TRANSITION_CHECK'
FULL_SCENE_ANALYSIS='FULL_SCENE_ANALYSIS'


@dataclass
class FrameVisuals:
    pixels:object
    signature:tuple
    stamp:float
    generation:int
    mode:str
    root:object
    visual_version:str='v59-pinned-ui-1'
    stats:dict=field(default_factory=dict)
    center:tuple|None=None
    tutorial:dict|None=None
    reward:tuple|None=None
    frame:object=None
    workspace:dict=field(default_factory=dict)
    _mask:object=None
    capture_seconds:float=0.

    def bind(self,frame):self.frame=weakref.ref(frame)

    def release_working_arrays(self):
        self._mask=None;self.workspace.pop('integrals',None)

    @property
    def mask(self):
        if self._mask is None:
            self._mask=text_mask(self.pixels)
            self.stats['text_normalizations']=self.stats.get('text_normalizations',0)+1
        return self._mask

    def certificate(self,kind):
        proofs=self.workspace.setdefault('certificates',{})
        if kind not in proofs:
            proofs[kind]=certificate(self.pixels,self.root,kind,mask=self.mask,fast=True,workspace=self.workspace,stats=self.stats)
        else:self.stats['certificate_reuses']=self.stats.get('certificate_reuses',0)+1
        return proofs[kind]

    def valid(self,frame):
        if self.frame is None or frame is not self.frame() or frame.probe_only or not frame.live:return False
        if frame.captured_at!=self.stamp or frame.window.signature!=self.signature:return False
        r=frame.roi;expected=self.pixels[r.y:r.y+r.height,r.x:r.x+r.width]
        return (frame.image.shape==expected.shape and frame.image.strides==expected.strides
            and frame.image.ctypes.data==expected.ctypes.data and not frame.image.flags.writeable)

    def center_for(self,frame):
        if not self.valid(frame) or self.center is None:return None
        x,y,r=self.center
        return x-frame.roi.x,y-frame.roi.y,r

    def summary(self):
        return dict(mode=self.mode,generation=self.generation,visual_version=self.visual_version,capture_seconds=self.capture_seconds,stats=dict(self.stats))


def visuals_for(frame):
    context=getattr(frame,'visuals',None)
    return context if isinstance(context,FrameVisuals) and context.valid(frame) else None
