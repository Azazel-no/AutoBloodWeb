"""Two-region UI certificates from reviewed, SHA-pinned original video pixels."""
from functools import lru_cache
import hashlib,json
from pathlib import Path
import cv2
from src.core.bloodweb_header import text_mask
from src.utils.images import read_image


@lru_cache(maxsize=4)
def references(root,kind):
    root=Path(root);folder=root/'assets/ui/video_v54'
    record=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))[kind]
    source=(root/record['source']).resolve()
    if not source.is_relative_to((root/'references/bloodweb').resolve()):raise ValueError('ui_reference_path')
    if hashlib.sha256(source.read_bytes()).hexdigest()!=record['source_sha256']:raise ValueError('ui_source_sha')
    tiles={}
    for name,region in record['regions'].items():
        path=(root/region['path']).resolve()
        if not path.is_relative_to(folder.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=region['sha256']:
            raise ValueError('ui_crop_sha')
        tiles[name]=text_mask(read_image(path))
    return record,tiles


def certificate(image,root,kind,*,mask=None,fast=False,workspace=None,stats=None):
    if image.shape[1]/image.shape[0]<1.5:return None
    try:record,tiles=references(str(root),kind)
    except (OSError,ValueError,KeyError):return None
    if mask is None:mask=text_mask(image)
    result=match_certificate(image,record,tiles,kind,mask=mask,fast=fast,workspace=workspace,stats=stats)
    if kind=='automatic':
        extra=automatic_native_variant(str(root))
        other=match_certificate(image,*extra,kind,mask=mask,fast=fast,workspace=workspace,stats=stats) if extra else None
        if result and other and any(abs(result['regions']['title'][i]-other['regions']['title'][i])>8 for i in (0,1)):return None
        return result or other
    if kind=='reward' and result is None:return unlocked_reward(image,record,tiles,mask,fast=fast,workspace=workspace,stats=stats)
    if kind!='continue':return result
    extra=continue_variant(str(root))
    other=match_certificate(image,*extra,kind,mask=mask,fast=fast,workspace=workspace,stats=stats) if extra else None
    if result and other:
        a=result['regions']['continue'];b=other['regions']['continue']
        if abs(a[0]-b[0])>max(a[2],b[2]) or abs(a[1]-b[1])>max(a[3],b[3]):return None
    return result or other


def unlocked_reward(image,record,tiles,mask,*,fast=False,workspace=None,stats=None):
    """A prestige reward with its own item name ("IRIDESCENT SOUL GUARD CHARM UNLOCKED",
    live 7.11 20:36): the reviewed title's word UNLOCKED, anywhere along the title row,
    plus the reviewed OK in its own area. Same 0.96 binary thresholds; both are required."""
    from src.core.visual_search import binary_match
    h,w=image.shape[:2];scale=h/record['client_size'][1];pad=round(h*.04)
    title=tiles['title'];word=title[:,203:]  # Columns 190-203 are the space after REWARDS.
    if not (word>0).any():return None
    found={};scores={}
    for name,template,full_row in (('button',tiles['button'],False),('title',word,True)):
        if scale!=1:template=cv2.resize(template,None,fx=scale,fy=scale,interpolation=cv2.INTER_NEAREST)
        th,tw=template.shape;bx,by,bw,bh=record['regions'][name]['box']
        top=max(0,round(by*scale)-pad);bottom=min(h,round((by+bh)*scale)+pad)
        left,right=((round(w*.2),round(w*.85)) if full_row else (max(0,round(bx*scale)-pad),min(w,round((bx+bw)*scale)+pad)))
        if right-left<tw or bottom-top<th:return None
        score,point,separate=binary_match(mask[top:bottom,left:right],template,fast=fast,workspace=workspace,stats=stats)
        if score<.96 or separate:return None
        found[name]=(left+point[0],top+point[1],tw,th);scores[name]=float(score)
    return dict(regions=found,scores=scores,source_sha256=record['source_sha256'],variant='unlocked_word')


@lru_cache(maxsize=4)
def automatic_native_variant(root):
    root=Path(root);folder=root/'assets/ui/video_v58'
    try:
        record=json.loads((folder/'automatic.json').read_text(encoding='utf-8'))
        source=(root/record['source']).resolve()
        if not source.is_relative_to((root/'references/bloodweb').resolve()):return None
        if hashlib.sha256(source.read_bytes()).hexdigest()!=record['source_sha256']:return None
        import numpy as np
        pixels=read_image(source);tiles={}
        for name,region in record['regions'].items():
            path=(root/region['path']).resolve()
            if not path.is_relative_to(folder.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=region['sha256']:return None
            x,y,w,h=region['box'];tile=read_image(path)
            if not np.array_equal(tile,pixels[y:y+h,x:x+w]):return None
            tiles[name]=text_mask(tile)
        if set(tiles)!={'title','description'}:return None
        return record,tiles
    except (OSError,ValueError,KeyError):return None


@lru_cache(maxsize=4)
def continue_variant(root):
    """An additional reviewed native crop; verify exact original pixels."""
    root=Path(root);folder=root/'assets/ui/video_v57'
    try:
        record=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        source=(root/record['source']).resolve();crop=(root/record['regions']['continue']['path']).resolve()
        if not source.is_relative_to((root/'references/bloodweb').resolve()) or not crop.is_relative_to(folder.resolve()):return None
        if hashlib.sha256(source.read_bytes()).hexdigest()!=record['source_sha256']:return None
        region=record['regions']['continue']
        if hashlib.sha256(crop.read_bytes()).hexdigest()!=region['sha256']:return None
        import numpy as np
        x,y,w,h=region['box'];pixels=read_image(source);tile=read_image(crop)
        if not np.array_equal(pixels[y:y+h,x:x+w],tile):return None
        return record,{'continue':text_mask(tile)}
    except (OSError,ValueError,KeyError):return None


def match_certificate(image,record,tiles,kind,mask=None,*,fast=False,workspace=None,stats=None):
    h,w=image.shape[:2]
    if w/h<1.5:return None
    scale=h/record['client_size'][1]
    if mask is None:mask=text_mask(image)
    found={};scores={}
    for name,template in tiles.items():
        if scale!=1:template=cv2.resize(template,None,fx=scale,fy=scale,interpolation=cv2.INTER_NEAREST)
        th,tw=template.shape
        if th>h or tw>w:return None
        # Reward text and OK must be in their separate, reviewed screen areas.
        if kind in ('reward','tutorial_unpredictability'):
            bx,by,bw,bh=record['regions'][name]['box'];pad=round(h*.04)
            left=max(0,round(bx*scale)-pad);top=max(0,round(by*scale)-pad)
            right=min(w,round((bx+bw)*scale)+pad);bottom=min(h,round((by+bh)*scale)+pad)
        else:left=top=0;right=w;bottom=h
        if right-left<tw or bottom-top<th:return None
        from src.core.visual_search import binary_match
        score,point,separate=binary_match(mask[top:bottom,left:right],template,fast=fast,workspace=workspace,stats=stats)
        if score<.96:return None
        if kind=='continue':
            # Search the complete game client; a second separate occurrence
            # cannot supply an unambiguous continuation target.
            if separate:return None
        found[name]=(left+point[0],top+point[1],tw,th);scores[name]=float(score)
    names=list(found)
    for name in names[1:]:
        actual=(found[name][0]-found[names[0]][0],found[name][1]-found[names[0]][1])
        a=record['regions'][name]['box'];b=record['regions'][names[0]]['box']
        if abs(actual[0]-(a[0]-b[0])*scale)>8*scale or abs(actual[1]-(a[1]-b[1])*scale)>8*scale:return None
    return dict(regions=found,scores=scores,source_sha256=record['source_sha256'])
