"""Strict text evidence from real UI crops. Unknown text remains unavailable."""
import json,hashlib
from functools import lru_cache
import cv2,numpy as np
from src.utils.images import read_image

def text_mask(image,saturation=100,value=130):
    hsv=cv2.cvtColor(image[:,:,:3],cv2.COLOR_BGR2HSV)
    return ((hsv[:,:,1]<saturation)&(hsv[:,:,2]>value)).astype(np.uint8)*255


# A bright, moving background behind the header (Cheryl Mason's red-pink sky, live
# 7.11 20:08-20:32) lifts the anti-aliased letter edges and tinted sky pixels over the
# fixed mask: the label scored 0.82-0.84 on every frame and the level stayed unknown.
# Only when the normal read fails, the live image alone is masked against its own local
# background (letters must stand LOCAL_TEXT_DELTA above it); the templates and the
# label/digit thresholds are unchanged.
LOCAL_TEXT_DELTA=70
LOCAL_TEXT_KERNEL=9


def local_text_mask(image):
    hsv=cv2.cvtColor(image[:,:,:3],cv2.COLOR_BGR2HSV);value=hsv[:,:,2]
    background=cv2.morphologyEx(value,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(LOCAL_TEXT_KERNEL,LOCAL_TEXT_KERNEL)))
    return (((value.astype(np.int16)-background)>LOCAL_TEXT_DELTA)&(hsv[:,:,1]<100)&(value>130)).astype(np.uint8)*255


def verified_text(root,record):
    path=(root/record['path']).resolve()
    if not path.is_relative_to((root/'assets/ui/bloodweb_header').resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256']:raise ValueError('header_template_hash')
    return text_mask(read_image(path))

@lru_cache(maxsize=4)
def templates(root):
    folder=root/'assets/ui/bloodweb_header';data=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    return data,verified_text(root,data['label']),{k:verified_text(root,v) for k,v in data['digits'].items()}


@lru_cache(maxsize=4)
def label_references(root):
    _,original,_=templates(root)
    extra=root/'assets/ui/bloodweb_header/label_variants_v58.json'
    records=json.loads(extra.read_text(encoding='utf-8'))['labels'] if extra.is_file() else []
    return [original]+[verified_text(root,record) for record in records]

# DBD's 90 % UI scale draws the same header smaller. These are native pixels from that
# scale, used as they are; each match reads its digits at the scale of its own reference.
UI90_SCALE=.9
UI90_LABELS='assets/ui/bloodweb_header/label_variants_ui90.json'
UNSUPPORTED_LABEL_SCORE=.45  # Real headers at other sizes score .51-.64, anything else at most .34.


@lru_cache(maxsize=4)
def ui90_label_references(root):
    path=root/UI90_LABELS
    if not path.is_file():return []
    return [verified_text(root,record) for record in json.loads(path.read_text(encoding='utf-8'))['labels']]


@lru_cache(maxsize=4)
def ui90_digits(root):
    path=root/UI90_LABELS
    if not path.is_file():return {}
    return {k:verified_text(root,v) for k,v in json.loads(path.read_text(encoding='utf-8')).get('digits',{}).items()}


def unsupported_ui_scale(image,root):
    """The header's scale when it is clearly drawn at a size without references, else None.

    Called only after the level could not be read. It never reads a level or allows input.
    """
    h,w=image.shape[:2]
    if w/max(1,h)<1.5:return None
    try:references=label_references(root);ui90=ui90_label_references(root)
    except (OSError,ValueError,KeyError):return None
    base=h/1080;roi=text_mask(image)[:round(h*.18),:round(w*.64)]
    def score(reference,factor):
        if factor!=1:reference=cv2.resize(reference,None,fx=factor,fy=factor,interpolation=cv2.INTER_NEAREST)
        if roi.shape[0]<reference.shape[0] or roi.shape[1]<reference.shape[1]:return 0.
        return float(cv2.matchTemplate(roi,reference,cv2.TM_CCOEFF_NORMED).max())
    if any(score(r,base)>=.96 for r in references+ui90):return None
    best=max(((score(references[0],base*s/100),s/100) for s in range(50,131,2)),key=lambda row:row[0])
    if best[0]<UNSUPPORTED_LABEL_SCORE or .86<=best[1]<=.94 or .96<=best[1]<=1.04:return None
    return best[1]


def read_level(image,root):
    result=read_level_mask(image,root,None)
    if result[0] is not None or image.shape[1]/max(1,image.shape[0])<1.5:return result
    h,w=image.shape[:2]
    mask=np.zeros(image.shape[:2],np.uint8);top,left=round(h*.18),round(w*.64)
    mask[:top,:left]=local_text_mask(image[:top,:left])
    fallback=read_level_mask(image,root,mask)
    return fallback if fallback[0] is not None else result


def read_level_mask(image,root,strict):
    try:data,label,digits=templates(root)
    except (OSError,ValueError,KeyError):return None,0.
    h,w=image.shape[:2];scale=h/data['reference_height']
    if w/h<1.5:return None,0.
    mask=text_mask(image) if strict is None else strict if isinstance(strict,np.ndarray) else text_mask(image,*strict);roi=mask[:round(h*.18),:round(w*.64)]
    matches=[]
    try:references=label_references(root)
    except (OSError,ValueError,KeyError):references=[label]
    try:ui90=ui90_label_references(root)
    except (OSError,ValueError,KeyError):ui90=[]
    for reference,factor in [(r,1.) for r in references]+[(r,UI90_SCALE) for r in ui90]:
        if scale!=1:reference=cv2.resize(reference,None,fx=scale,fy=scale,interpolation=cv2.INTER_NEAREST)
        if roi.shape[0]<reference.shape[0] or roi.shape[1]<reference.shape[1]:continue
        response=cv2.matchTemplate(roi,reference,cv2.TM_CCOEFF_NORMED);_,score,_,point=cv2.minMaxLoc(response)
        matches.append((score,point,reference,factor))
    if not matches:return None,0.
    score,point,label,factor=max(matches,key=lambda row:row[0])
    if score<.96:return None,score
    scale*=factor
    if factor!=1:digits=ui90_digits(root)
    if not digits:return None,0.
    x,y=point;x+=label.shape[1];strip=mask[max(0,y-round(3*scale)):y+label.shape[0]+round(3*scale),x:x+round(60*scale)]
    count,labels,stats,_=cv2.connectedComponentsWithStats((strip>0).astype(np.uint8))
    boxes=sorted([list(map(int,s[:4])) for s in stats[1:] if s[3]>=10*scale and s[4]>=10*scale*scale])
    if not 1<=len(boxes)<=2:return None,0.
    number='';confidence=score
    for x,y,bw,bh in boxes:
        tile=np.pad(strip[y:y+bh,x:x+bw],round(2*scale))
        ranked=[]
        for digit,reference in digits.items():
            candidate=cv2.resize(tile,(reference.shape[1],reference.shape[0]),interpolation=cv2.INTER_NEAREST)>0
            target=reference>0
            # Different aspect ratios are not silently stretched into matches.
            aspect=(bw/bh)/((reference.shape[1]-4)/(reference.shape[0]-4))
            value=2*np.count_nonzero(candidate&target)/max(1,np.count_nonzero(candidate)+np.count_nonzero(target)) if .8<aspect<1.25 else 0.
            ranked.append((value,digit))
        ranked.sort(reverse=True)
        if ranked[0][0]<.95 or len(ranked)>1 and ranked[0][0]-ranked[1][0]<.07:return None,0.
        confidence=min(confidence,ranked[0][0]);number+=ranked[0][1]
    value=int(number)
    return (value,confidence) if 1<=value<=50 else (None,0.)

@lru_cache(maxsize=4)
def automatic_templates(root):
    folder=root/'assets/ui/bloodweb_header'
    data=json.loads((folder/'automatic.json').read_text(encoding='utf-8'))
    return data,{key:verified_text(root,data[key]) for key in ('title','description')}

def automatic_tooltip(image,root):
    from src.core.video_ui import certificate
    if certificate(image,root,'automatic'):return True
    return legacy_automatic_tooltip(image,root)

def legacy_automatic_tooltip(image,root):
    try:data,references=automatic_templates(root)
    except (OSError,ValueError,KeyError):return False
    mask=text_mask(image);h,w=mask.shape;points=[];scale=h/data['reference_height']
    for key in ('title','description'):
        reference=references[key]
        if scale!=1:reference=cv2.resize(reference,None,fx=scale,fy=scale,interpolation=cv2.INTER_NEAREST)
        if reference.shape[0]>h or reference.shape[1]>w:return False
        scores=cv2.matchTemplate(mask,reference,cv2.TM_CCOEFF_NORMED);_,score,_,loc=cv2.minMaxLoc(scores)
        if score<.97:return False
        points.append(loc)
    return abs(points[0][0]-points[1][0])<10*scale and 0<points[1][1]-points[0][1]<35*scale
