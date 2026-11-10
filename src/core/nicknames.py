"""Nickname matching for the teammate overlay: Tab nickname <-> HUD nickname.

Uses the Windows built-in OCR (Windows.Media.Ocr) through a long-lived Windows
PowerShell helper (src/core/ocr_helper.ps1): no network, no extra installs. OCR runs
only on the small nickname strips, only after a Tab read. Unsure text stays unused.
"""
from difflib import SequenceMatcher
import json
import logging
import queue
import re
import subprocess
import tempfile
import threading
from pathlib import Path
import cv2
import numpy as np
from src.core.match_reader import Geometry, SLOTS

log=logging.getLogger(__name__)
CREATE_NO_WINDOW=0x08000000
SCALE=3
PAD=24
GREY_PEAK=200  # Tab: the nickname is grey (~140); character name and ping are white (~250).
# Cyrillic letters that look like Latin ones, so 'kakcaxap' and 'какcахар' compare equal.
HOMOGLYPHS=str.maketrans('асеорхукмнвтАСЕОРХУКМНВТ','aceopxykmhbtACEOPXYKMHBT')
NAME_MINIMUM=.75
NAME_MARGIN=.2


def normalize(text):
    t=(text or '').translate(HOMOGLYPHS).casefold()
    t=re.sub(r'(^|[\s_\[\(\|])ttv([\s_\]\)\|]|$)',' ',t)
    t=re.sub(r'[\s_]+\d{1,2}\s*$','',t.strip())  # Default '_01' suffixes.
    return re.sub(r'[\W_]+','',t)


def name_similarity(a,b):
    """0-1. Whole-string ratio, or the best window when one side carries OCR junk around it."""
    a,b=normalize(a),normalize(b)
    if len(a)<2 or len(b)<2:return 0.
    full=SequenceMatcher(None,a,b).ratio()
    short,long=sorted((a,b),key=len)
    if len(short)<3:return full
    window=max(SequenceMatcher(None,short,long[i:i+len(short)]).ratio() for i in range(len(long)-len(short)+1))
    return max(full,window*len(short)/(len(short)+1))


def name_scores(tab_names,hud_names):
    """{(row,slot): best similarity over all OCR readings of both sides}."""
    scores={}
    for row,names in tab_names.items():
        for slot in SLOTS:
            scores[(row,slot)]=max([name_similarity(a,b) for a in names for b in hud_names.get(slot,())] or [0.])
    return scores


def prepare(gray):
    big=cv2.resize(gray,None,fx=SCALE,fy=SCALE,interpolation=cv2.INTER_CUBIC)
    return cv2.copyMakeBorder((255-big).clip(0,255).astype(np.uint8),PAD,PAD,PAD,PAD,cv2.BORDER_CONSTANT,value=255)


def tab_strip(image,layout,row_y):
    g=Geometry(layout,image.shape);n=layout['tab']['name']
    x0,_=g.tab(n['x'][0],0);x1,_=g.tab(n['x'][1],0);_,cy=g.tab(0,row_y+n['dy']);half=g.size(n['half'])
    strip=image[max(0,cy-half):cy+half,max(0,x0):x1]
    return cv2.cvtColor(strip,cv2.COLOR_BGR2GRAY) if strip.size else None


def hud_strips(image,layout):
    g=Geometry(layout,image.shape);h=layout['hud'];n=h['name']
    strips=[]
    for y in h['portrait_y']:
        x0,cy=g.hud(n['x'][0],y+n['dy']);x1,_=g.hud(n['x'][1],y);half=g.size(n['half'])
        strip=image[max(0,cy-half):cy+half,x0:x1]
        strips.append(strip.copy() if strip.size else None)
    return strips


def hud_variants(strip):
    """Plain and contrast-stretched: white italic text over any game background."""
    gray=cv2.cvtColor(strip,cv2.COLOR_BGR2GRAY).astype(np.float32)
    lo,hi=np.percentile(gray,50),np.percentile(gray,99.5)
    return [prepare(gray),prepare(np.clip((gray-lo)/max(hi-lo,1.),0,1)*255)]


def grey_only(gray):
    """Tab line with the white character name and ping darkened, leaving the grey nickname."""
    return np.where((gray>90)&(gray<GREY_PEAK),gray,np.where(gray>=GREY_PEAK,40,gray)).astype(np.float32)


def tab_nickname(result,gray):
    """Grey words of the Tab line: the nickname, without character name, ping or BOT badge."""
    words=[]
    for w in result.get('words',()):
        x0,y0=(w['x']-PAD)//SCALE,(w['y']-PAD)//SCALE
        box=gray[max(0,y0):max(0,y0)+w['h']//SCALE+1,max(0,x0):max(0,x0)+w['w']//SCALE+1]
        text=w['text']
        if not box.size or normalize(text) in ('bot','ms') or re.fullmatch(r'\d+\s*(ms)?|\d*ms',text.casefold()):continue
        if np.percentile(box,95)<GREY_PEAK:words.append(text)
    return ' '.join(words)


def hud_text(result):
    return re.sub(r'\s+(BOT|ВОТ|BОТ)\s*$','',result.get('text','').strip(),flags=re.I)


class NicknameOcr:
    """Client for the PowerShell OCR helper. Thread-safe; `ready` is False until it answers."""
    timeout=4.

    def __init__(self,root,popen=None):
        self.root=Path(root);self.popen=popen or subprocess.Popen
        self.process=None;self.ready=False;self.failed=None;self.languages=[]
        self.lock=threading.Lock();self.lines=queue.Queue();self.folder=None;self.counter=0

    def start(self):
        threading.Thread(target=self._start,name='OverlayOcrStart',daemon=True).start()

    def _start(self):
        with self.lock:
            try:
                self.folder=Path(tempfile.mkdtemp(prefix='abw_ocr_'));self.ready=False;self.failed=None
                self.lines=queue.Queue()  # The previous helper's reader may still post its end marker to the old queue.
                script=self.root/'src/core/ocr_helper.ps1'
                self.process=self.popen(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script)],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',
                    creationflags=CREATE_NO_WINDOW)
                threading.Thread(target=self._read,args=(self.process,self.lines),name='OverlayOcrRead',daemon=True).start()
                first=self.lines.get(timeout=20)
                if not first.strip():raise OSError('ocr_helper_exited_before_starting')
                hello=json.loads(first)
                self.languages=hello.get('languages',[])
                self.ready=bool(hello.get('ready')) and bool(self.languages)
                if not self.ready:self.failed='no_ocr_language'
            except Exception as exc:
                self.failed=repr(exc);self.ready=False
                log.warning('Nickname OCR unavailable: %s',exc)

    @staticmethod
    def _read(process,lines):
        try:
            for line in process.stdout:lines.put(line)
        except Exception:pass
        lines.put('')

    def recognize(self,images):
        """One result list per image: [{'lang','text','words'}]. Empty lists when OCR is unavailable."""
        if not self.ready:return [[] for _ in images]
        out=[]
        with self.lock:
            for image in images:
                if image is None:out.append([]);continue
                self.counter+=1;path=self.folder/f'{self.counter%16}.png'
                cv2.imwrite(str(path),image)
                try:
                    self.process.stdin.write(str(path)+'\n');self.process.stdin.flush()
                    line=self.lines.get(timeout=self.timeout)
                    if not line:raise OSError('ocr_helper_exited')
                    out.append(json.loads(line).get('results',[]))
                except (OSError,ValueError,queue.Empty) as exc:
                    log.warning('Nickname OCR failed: %r',exc);self.ready=False;self.failed=repr(exc);self.close()
                    out.extend([] for _ in range(len(images)-len(out)));break
        return out

    def close(self):
        process,self.process=self.process,None;self.ready=False
        if process is not None:
            try:process.stdin.write('\n');process.stdin.flush()
            except Exception:pass
            try:process.kill()
            except Exception:pass


def read_tab_names(ocr,image,layout,indexes):
    """{row index: [nickname per OCR language]} for the given Tab rows."""
    teammates=list(indexes);grays=[tab_strip(image,layout,layout['tab']['rows_y'][i]) for i in teammates]
    results=ocr.recognize([prepare(g) if g is not None else None for g in grays])
    names={}
    for row,gray,result in zip(teammates,grays,results):
        found=[n for n in (tab_nickname(res,gray) for res in result) if normalize(n)]
        if found:names[row]=found
    # A short nickname next to a bold character name can be dropped: retry with only the grey text.
    retry=[(row,gray) for row,gray in zip(teammates,grays) if gray is not None and row not in names]
    for (row,gray),result in zip(retry,ocr.recognize([prepare(grey_only(g)) for _,g in retry])):
        found=[n for n in (tab_nickname(res,gray) for res in result) if normalize(n)]
        if found:names[row]=found
    return names


def read_hud_names(ocr,strips,slots=SLOTS):
    """{slot: [texts]} from the HUD nickname strips (both preprocessing variants, all languages)."""
    jobs=[(slot,image) for slot in slots if strips[slot] is not None for image in hud_variants(strips[slot])]
    results=ocr.recognize([image for _,image in jobs])
    names={}
    for (slot,_),result in zip(jobs,results):
        for res in result:
            text=hud_text(res)
            if normalize(text):names.setdefault(slot,[]).append(text)
    return names
