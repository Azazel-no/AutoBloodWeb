"""Decisive Strike reminder: survivor status on the killer's HUD and the timer per slot.

Only the left HUD column is read. A status is the white hook or skull glyph inside the
portrait; everything else (healthy, injured, dying, disconnected) is OTHER, which is all the
trigger needs. Pure logic with an explicit clock, so sequences can be tested without a game.
"""
from pathlib import Path

import cv2
import numpy as np

from src.core.match_reader import Geometry

HOOKED='hooked'
DEAD='dead'
OTHER='other'

PATCH_HALF=44  # Reference pixels around a portrait centre; the portrait radius is 36.
GLYPH_RADIUS=38
GLYPH_WHITE=(150,110,80)  # Glyphs dim as a slot fades; a portrait stays far below the score at each level.
# Measured on 1920x1080 samples (best level per glyph): true glyphs 0.86-1.0, others at most 0.48.
GLYPH_SCORE=.7

ACTIVE_SECONDS=60.
EXPIRED_SECONDS=15.
STABLE_READS=3
HUD_GONE_SECONDS=8.


def load_templates(root):
    folder=Path(root)/'assets/ui/killer_hud'
    return {name:cv2.imread(str(folder/f'{name}.png'),cv2.IMREAD_GRAYSCALE) for name in (HOOKED,DEAD)}


def glyph_masks(patch):
    size=PATCH_HALF*2
    if patch.shape[:2]!=(size,size):return []
    yy,xx=np.ogrid[-PATCH_HALF:PATCH_HALF,-PATCH_HALF:PATCH_HALF]
    inside=(xx*xx+yy*yy)<=GLYPH_RADIUS**2;darkest=patch.min(axis=2)
    return [((darkest>level)&inside).astype(np.uint8)*255 for level in GLYPH_WHITE]


def classify(patch,templates):
    """patch: BGR crop of PATCH_HALF*2 square around a portrait centre."""
    best,score=OTHER,GLYPH_SCORE
    for mask in glyph_masks(patch):
        if not mask.any():continue
        for name,template in templates.items():
            value=float(cv2.matchTemplate(mask,template,cv2.TM_CCOEFF_NORMED).max())
            if value>=score:best,score=name,value
    return best


def column_rect(layout,width,height):
    """(x, y, w, h) inside the client: portraits and nicknames of all four slots."""
    g=Geometry(layout,(height,width));h=layout['hud'];half=g.size(PATCH_HALF*h.get('fit_scale',1))
    left=g.hud(h['portrait_x'],0)[0]-half;right=g.hud(h['name']['x'][1],0)[0]
    top=g.hud(0,h['portrait_y'][0])[1]-half;bottom=g.hud(0,h['portrait_y'][-1])[1]+half
    return left,top,right-left,bottom-top


def read_column(image,layout,width,height,templates):
    """image: pixels of column_rect. Returns (four statuses, whether the HUD is visible)."""
    g=Geometry(layout,(height,width));h=layout['hud'];n=h['name'];half=g.size(PATCH_HALF*h.get('fit_scale',1))
    left,top,_,_=column_rect(layout,width,height)
    statuses=[];names=0;kernel=cv2.getStructuringElement(cv2.MORPH_RECT,(9,9))
    for y in h['portrait_y']:
        cx,cy=g.hud(h['portrait_x'],y);cx-=left;cy-=top
        patch=image[max(0,cy-half):cy+half,max(0,cx-half):cx+half,:3]
        if patch.shape[0]!=half*2 or patch.shape[1]!=half*2:statuses.append(OTHER)
        else:
            if half!=PATCH_HALF:patch=cv2.resize(patch,(PATCH_HALF*2,PATCH_HALF*2),interpolation=cv2.INTER_AREA)
            statuses.append(classify(patch,templates))
        x0,sy=g.hud(n['x'][0],y+n['dy']);x1=g.hud(n['x'][1],y)[0];name_half=g.size(n['half'])
        strip=image[max(0,sy-top-name_half):sy-top+name_half,x0-left:x1-left,:3]
        if strip.size:
            bright=cv2.morphologyEx(cv2.cvtColor(strip,cv2.COLOR_BGR2GRAY),cv2.MORPH_TOPHAT,kernel)>70
            names+=bright.any(axis=0).mean()>=.05
    return statuses,names>=2


class DsTracker:
    """Hooked -> alive starts the timer; hooked -> dead does nothing; hooked again cancels."""
    def __init__(self,expired_seconds=EXPIRED_SECONDS):
        self.expired_seconds=expired_seconds
        self.clear()

    def clear(self):
        self.stable=[None]*4;self.candidate=[None]*4;self.count=[0]*4;self.started=[None]*4

    def update(self,statuses,now):
        for slot,status in enumerate(statuses):
            if status!=self.candidate[slot]:self.candidate[slot]=status;self.count[slot]=0
            self.count[slot]+=1
            if self.count[slot]<STABLE_READS or status==self.stable[slot]:continue
            previous=self.stable[slot];self.stable[slot]=status
            if status==OTHER and previous==HOOKED:self.started[slot]=now
            elif status in (HOOKED,DEAD):self.started[slot]=None

    def display(self,now):
        """{slot: ('running', 0..1) or ('expired', 1..0 of the remaining red time)}."""
        shown={}
        for slot,started in enumerate(self.started):
            if started is None:continue
            elapsed=now-started
            if elapsed<ACTIVE_SECONDS:shown[slot]=('running',elapsed/ACTIVE_SECONDS)
            elif elapsed<ACTIVE_SECONDS+self.expired_seconds:shown[slot]=('expired',1-(elapsed-ACTIVE_SECONDS)/self.expired_seconds)
            else:self.started[slot]=None
        return shown
