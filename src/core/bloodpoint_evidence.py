"""Reject an impossible one-frame HUD jump without inventing a balance."""
from src.core.screen_manager import ScreenError


class BloodpointEvidence:
    def __init__(self,baseline=None):
        self.last=baseline;self.candidate=None

    def observe(self,frame,max_debit=None):
        value=frame.bloodpoints
        if value is None:return
        if type(value) is not int or value<0:raise ScreenError('frame_unstable','bloodpoints_reading_unproven')
        suspicious=(self.last is not None and
            (value>self.last or type(max_debit) is int and self.last-value>max_debit))
        if not suspicious:
            self.last=value;self.candidate=None;return
        key=(frame.window.signature,value)
        old=self.candidate
        if old is None or old['key']!=key:
            old=dict(key=key,count=0,first=frame.captured_at,last=float('-inf'))
        if frame.captured_at>old['last']:old['count']+=1;old['last']=frame.captured_at
        self.candidate=old
        if old['count']>=3 and frame.captured_at-old['first']>=.6:
            raise ScreenError('bp_changed_unexpectedly',f'{self.last} -> {value}; permitted debit {max_debit}')
        raise ScreenError('frame_unstable','bloodpoints_reading_unproven')
