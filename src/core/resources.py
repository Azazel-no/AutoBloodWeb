"""Conservative HUD digit reader using only the supplied UI reference.

Missing or ambiguous digits return None. No inferred prices or game rules.
"""
import cv2
import numpy as np
from src.utils.images import read_image
import hashlib,json


class ResourceReader:
    def __init__(self,root):
        path=root/'references/bloodweb/bloodweb_prestige_reward_ok.png'
        self.templates={}; self.anchor=None;self.anchors=[];self.extra_templates={}
        if not path.is_file(): return
        image=read_image(path)
        self.anchor=cv2.cvtColor(image[52:79,1592:1620],cv2.COLOR_BGR2GRAY)
        self.anchors.append(self.anchor)
        for x1,x2,text in ((1420,1478,'9570'),(1518,1583,'25770'),(1620,1720,'3712232')):
            pieces=self.components(image[58:81,x1:x2],1)
            if len(pieces)==len(text):
                for digit,(_,glyph) in zip(text,pieces): self.templates[digit]=self.normalize(glyph)
        extra=root/'assets/reference_versions/v46_hud_digits.json'
        if extra.is_file():
            data=json.loads(extra.read_text(encoding='utf-8'));records=data['records']
            for record in records:
                path=root/record['path']
                if record['digit'] in self.templates or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256']:continue
                pieces=self.components(read_image(path),1)
                if len(pieces)==1:self.templates[record['digit']]=self.normalize(pieces[0][1])
            record=data.get('anchor',{});path=root/record.get('path','missing')
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==record.get('sha256'):
                self.anchors.append(cv2.cvtColor(read_image(path),cv2.COLOR_BGR2GRAY))
        extra=root/'assets/reference_versions/v58_native_digits.json'
        if extra.is_file():
            for record in json.loads(extra.read_text(encoding='utf-8'))['records']:
                path=(root/record['path']).resolve();source=(root/record['source']).resolve()
                if (not path.is_relative_to((root/'assets/ui/hud').resolve()) or
                        not source.is_relative_to((root/'references/bloodweb').resolve()) or
                        not path.is_file() or not source.is_file() or
                        hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256'] or
                        hashlib.sha256(source.read_bytes()).hexdigest()!=record['source_sha256']):continue
                x,y,w,h=record['box'];pixels=read_image(source);tile=read_image(path)
                if not np.array_equal(tile,pixels[y:y+h,x:x+w]):continue
                pieces=self.components(tile,1)
                index=record.get('component_index',0);text=record.get('reviewed_text',record['digit'])
                if (type(index) is int and len(pieces)==len(text) and 0<=index<len(pieces)
                        and text[index]==record['digit'] and record['digit'] in self.templates):
                    self.extra_templates.setdefault(record['digit'],[]).append(self.normalize(pieces[index][1]))

    @staticmethod
    def components(image,scale):
        if not image.size: return []
        gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY) if image.ndim==3 else image
        binary=cv2.threshold(gray,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)[1]
        _,labels,stats,_=cv2.connectedComponentsWithStats(binary)
        result=[]
        for index,(x,y,w,h,area) in enumerate(stats[1:],1):
            if area>max(3,6*scale*scale) and h>8*scale:
                result.append((int(x),binary[y:y+h,x:x+w]))
        return sorted(result,key=lambda pair:pair[0])

    @staticmethod
    def normalize(glyph):
        h,w=glyph.shape
        small=cv2.resize(glyph,(max(1,round(w*18/h)),18),interpolation=cv2.INTER_AREA)
        if small.shape[1]>16: return np.zeros((20,18),np.float32)
        out=np.zeros((20,18),np.float32); x=(18-small.shape[1])//2
        out[1:19,x:x+small.shape[1]]=small/255
        return out

    def decode(self,image,scale=1):
        pieces=self.components(image,scale)
        if not 1<=len(pieces)<=9: return None
        digits=[]
        for _,glyph in pieces:
            normalized=self.normalize(glyph)
            scores=sorted(((max(float(2*np.sum(normalized*v)/(np.sum(normalized**2)+np.sum(v**2)+1e-6))
                               for v in [t,*self.extra_templates.get(digit,[])]),digit)
                           for digit,t in self.templates.items()),reverse=True)
            if len(scores)<2 or scores[0][0]<.92 or scores[0][0]-scores[1][0]<.06: return None
            digits.append(scores[0][1])
        return int(''.join(digits))

    def read(self,image):
        if self.anchor is None: return None
        h,w=image.shape[:2]; scale=h/1080
        if w/h<1.5 or scale<.4: return None
        gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
        left=round(w*.60); bottom=round(h*.15)
        region=gray[:bottom,left:]
        best=None
        for anchor in self.anchors:
            template=cv2.resize(anchor,None,fx=scale,fy=scale,interpolation=cv2.INTER_AREA)
            if region.shape[0]<template.shape[0] or region.shape[1]<template.shape[1]:continue
            scores=cv2.matchTemplate(region,template,cv2.TM_CCOEFF_NORMED)
            _,score,_,(x,y)=cv2.minMaxLoc(scores)
            if best is None or score>best[0]:best=(score,x,y,template.shape[1])
        if best is None:return None
        score,x,y,width=best
        if score<.92: return None
        x+=left+width; y+=round(6*scale)
        crop=image[y:y+round(21*scale),x:x+round(105*scale)]
        return self.decode(crop,scale)
