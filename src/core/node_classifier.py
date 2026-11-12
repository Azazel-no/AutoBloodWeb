"""Compare inner glyphs, not inventory frames or Bloodweb rings."""
import cv2
import numpy as np
from collections import OrderedDict
from functools import lru_cache
from dataclasses import replace
import threading
import time

from src.core.icon_library import IconLibrary, rarity_of,interior_mask
from src.core.models import Match,RecognitionStatus

_PREPARED_LOCK=threading.RLock()


def rarity_evidence(image,mask):
    rarity=rarity_of(image,mask)
    hsv=cv2.cvtColor(image[:,:,:3],cv2.COLOR_BGR2HSV)
    valid=(hsv[:,:,1]>65)&(hsv[:,:,2]>25)&(mask>0)
    hue=hsv[:,:,0][valid]
    if len(hue)<max(20,int(np.count_nonzero(mask)*.12)):return rarity,0.
    bins=np.where((hue>155)|(hue<8),'pink',np.where(hue>125,'purple',np.where(hue>85,'blue',np.where(hue>32,'green','brown'))))
    return rarity,float(np.mean(bins==rarity))


def glyph(image: np.ndarray, mask: np.ndarray | None = None, remove_badge: bool = False) -> np.ndarray | None:
    if mask is None:
        mask = np.ones(image.shape[:2],np.uint8)*255
    gray = cv2.cvtColor(image[:,:,:3],cv2.COLOR_BGR2GRAY)
    pixels = gray[mask>0]
    if len(pixels)<30 or np.percentile(pixels,95)-np.percentile(pixels,15)<30:
        return None
    threshold,_ = cv2.threshold(pixels,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    threshold = max(threshold,float(np.percentile(pixels,95))*.52)
    binary = ((gray>threshold)&(mask>0)).astype(np.uint8)*255
    # Small isolated flecks, quantity remnants and grunge are not icon features.
    count,labels,stats,_ = cv2.connectedComponentsWithStats(binary)
    for i in range(1,count):
        x,y,w,h,area=stats[i]
        # The circle mask clips the left arm of the upgrade cross. Testing the
        # bounding-box left edge missed that component and made it part of the glyph.
        badge=(remove_badge and x>image.shape[1]*.55 and x+w/2>image.shape[1]*.65
               and y+h<image.shape[0]*.42 and w<image.shape[1]*.32
               and area<image.shape[0]*image.shape[1]*.06)
        if area<max(3,image.shape[0]*image.shape[1]*.001) or badge:
            binary[labels==i]=0
    points=cv2.findNonZero(binary)
    if points is None or len(points)<20:
        return None
    x,y,w,h=cv2.boundingRect(points)
    size=56
    scale=size/max(w,h)
    small=cv2.resize(binary[y:y+h,x:x+w],(max(1,round(w*scale)),max(1,round(h*scale))),interpolation=cv2.INTER_AREA)
    out=np.zeros((64,64),np.uint8)
    sy,sx=(64-small.shape[0])//2,(64-small.shape[1])//2
    out[sy:sy+small.shape[0],sx:sx+small.shape[1]]=small
    return out


@lru_cache(maxsize=128)
def node_mask(radius: int, shape: tuple[int,int]) -> np.ndarray:
    h,w=shape
    yy,xx=np.ogrid[:h,:w]
    dx,dy=xx-w//2,yy-h//2
    mask=(dx*dx+dy*dy<(radius*.86)**2).astype(np.uint8)*255
    mask.setflags(write=False)
    return mask


def node_glyph(image,radius):
    """Separate light ink from a bright event tile before identity matching.

    Orange saturation only selects preprocessing, never an item identity or
    a confidence bonus. The resulting glyph must pass the usual thresholds.
    The exact same feature is used for the memory fingerprint.
    """
    mask=node_mask(radius,image.shape[:2]);hsv=cv2.cvtColor(image[:,:,:3],cv2.COLOR_BGR2HSV)
    # Locked tiles dim their value, not their background hue. A value cutoff
    # of145 switched preprocessing when the same tile became available.
    orange=(hsv[:,:,0]>=12)&(hsv[:,:,0]<=28)&(hsv[:,:,1]>=160)&(hsv[:,:,2]>=25)&(mask>0)
    if np.count_nonzero(orange)>=np.count_nonzero(mask)*.25:
        image=image[:,:,:3].copy();image[hsv[:,:,1]>100]=0
    return glyph(image,mask,remove_badge=True)


class IconMatcher:
    def __init__(self, library: IconLibrary, role: str = 'survivor', threshold: float = .86, margin: float = .035,character='blight'):
        self.library, self.role = library,role
        self.threshold,self.min_margin=threshold,margin
        self.match_cache=OrderedDict();self.cache_hits=0;self.cache_misses=0
        self._gradient_cache={};self.last_timings={}
        entries=library.filter(role,character=character)
        def stamp(path):
            try:s=path.stat();return (s.st_mtime_ns,s.st_size)
            except OSError:return None
        key=(role,character if role=='killer' else None,tuple((e.id,e.fingerprint,stamp(library.root/e.image),stamp(library.root/e.source),
            tuple((r['sha256'],stamp(library.root/r['source'])) for r in getattr(library,'wiki_references',{}).get(e.id,[]))) for e in entries),
            tuple((e.id,meta.get('display_sha256')) for e in entries if (meta:=getattr(library,'event_metadata',{}).get(e.id))))
        started=time.perf_counter()
        with _PREPARED_LOCK:
            cache=getattr(library,'_prepared_matchers',None)
            if cache is None:cache=library._prepared_matchers=OrderedDict()
            if key in cache:
                self.__dict__.update(cache[key]);cache.move_to_end(key);self.preparation_reused=True
            else:
                self._prepare(entries)
                fields=('features','references','variants','templates','norms','detail_templates','detail_norms','by_id','template_offsets','category_indices','rarity_indices')
                cache[key]={name:getattr(self,name) for name in fields}
                cache.move_to_end(key)
                while len(cache)>3:cache.popitem(last=False)
                self.preparation_reused=False
        self.preparation_seconds=time.perf_counter()-started

    def clear_matches(self):
        self.match_cache.clear()

    def _prepare(self,entries):
        self.features=[]
        self.references={}; sources={}
        library=self.library
        for entry in entries:
            raw=cv2.imdecode(np.fromfile(library.root/entry.image,np.uint8),cv2.IMREAD_UNCHANGED)
            mask=raw[:,:,3] if raw.shape[2]==4 else None
            feature=glyph(raw[:,:,:3],mask)
            if feature is not None:
                self.features.append((entry,feature))
                refs=[feature]
                source=library.root/entry.source
                if source.is_file() and len(entry.box)==4:
                    if entry.source not in sources: sources[entry.source]=cv2.imdecode(np.fromfile(source,np.uint8),cv2.IMREAD_UNCHANGED)
                    original=sources[entry.source]; x,y,w,h=entry.box
                    crop=original[y:y+h,x:x+w]
                    if crop.shape[:2]==(h,w):
                        # Wiki originals already have their own alpha mask.
                        original_mask=(crop[:,:,3] if crop.shape[2]==4 else None) if not entry.source.startswith('references/inventory/') else interior_mask((h,w),entry.category=='offerings')
                        original_feature=glyph(crop[:,:,:3],original_mask)
                        if original_feature is not None: refs.append(original_feature)
                for extra in getattr(library,'wiki_references',{}).get(entry.id,[]):
                    import hashlib
                    path=library.root/extra['source']
                    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=extra['sha256']: continue
                    raw_extra=cv2.imdecode(np.fromfile(path,np.uint8),cv2.IMREAD_UNCHANGED)
                    if raw_extra is None: continue
                    if raw_extra.ndim==2: raw_extra=cv2.cvtColor(raw_extra,cv2.COLOR_GRAY2BGRA)
                    extra_feature=glyph(raw_extra[:,:,:3],raw_extra[:,:,3] if raw_extra.shape[2]==4 else None)
                    if extra_feature is not None: refs.append(extra_feature)
                event=getattr(library,'event_metadata',{}).get(entry.id)
                if not event and entry.id in getattr(library,'rendered_reference_ids',()) and entry.id in getattr(library,'display_images',{}):
                    event=dict(display=library.display_images[entry.id])
                if event:
                    # The Wiki's verified layered tile supplies realistic alpha
                    # compositing, clipping and downsampling before normalization.
                    # These are source-derived features, never captured game crops.
                    display=cv2.imdecode(np.fromfile(library.root/event['display'],np.uint8),cv2.IMREAD_COLOR)
                    for size in (71,81):
                        rendered=cv2.resize(display,(size,size),interpolation=cv2.INTER_LINEAR)
                        feature_at_size=node_glyph(rendered,size//2)
                        if feature_at_size is not None:refs.append(feature_at_size)
                self.references[entry.id]=refs
        self.variants={}
        for entry,feature in self.features:
            variants=[]
            refs=self.references[entry.id]
            # Both references come from the same original crop; retain the native
            # source detail as well as the cached 96px GUI tile under the same ID.
            unique_refs={r.tobytes():r for r in refs}
            for reference in unique_refs.values():
                variants.append(reference)
                for angle,scale in ((-6,1),(6,1),(0,.94),(0,1.06)):
                    transform=cv2.getRotationMatrix2D((31.5,31.5),angle,scale)
                    variants.append(cv2.warpAffine(reference,transform,(64,64),flags=cv2.INTER_LINEAR,borderValue=0))
            self.variants[entry.id]=[cv2.GaussianBlur(v,(3,3),.7).astype(np.float32)/255 for v in variants]
        flat=[];offsets=[]
        for entry,_ in self.features:
            offsets.append(len(flat));seen=set()
            for b in self.variants[entry.id]:
                for dy,dx in ((0,0),(-2,0),(2,0),(0,-2),(0,2)):
                    tile=cv2.warpAffine(b,np.float32([[1,0,dx],[0,1,dy]]),(64,64),borderValue=0).ravel()
                    key=tile.tobytes()
                    if key not in seen:flat.append(tile);seen.add(key)
        self.templates=np.stack(flat) if flat else np.empty((0,4096),np.float32)
        self.template_offsets=np.array(offsets,dtype=int)
        self.norms=np.sum(self.templates*self.templates,axis=1)
        self.detail_templates=self.templates>.5;self.detail_norms=self.detail_templates.sum(axis=1)
        self.templates.setflags(write=False);self.norms.setflags(write=False)
        self.by_id={e.id:e for e in library.entries}
        self.category_indices={c:np.array([i for i,(e,_) in enumerate(self.features) if e.category==c]) for c in ('items','addons','offerings')}
        self.rarity_indices={c:np.array([i for i,(e,_) in enumerate(self.features) if e.rarity==c]) for c in {e.rarity for e,_ in self.features}}

    def match(self, image: np.ndarray, radius: int,use_cache=True,prepared_feature=None,prepared_rarity=None) -> Match:
        started=time.perf_counter();self.last_timings={'recognition_stage1':0.,'recognition_stage2':0.}
        mask=node_mask(radius,image.shape[:2])
        feature=prepared_feature if prepared_feature is not None else node_glyph(image,radius)
        if feature is None:
            return Match()
        rarity,color_confidence=prepared_rarity if prepared_rarity is not None else rarity_evidence(image,mask)
        key=(feature.tobytes(),rarity,color_confidence>=.7,self.threshold,self.min_margin)
        if use_cache and key in self.match_cache:
            self.cache_hits+=1;self.match_cache.move_to_end(key)
            saved=self.match_cache[key]
            self.last_timings['recognition_stage1']=time.perf_counter()-started
            reused=replace(saved,candidates=list(saved.candidates),top10=list(saved.top10),reranked=list(saved.reranked),rarity_confidence=color_confidence)
            reused._opening_overlaps=getattr(saved,'_opening_overlaps',None)
            return reused
        self.cache_misses+=1
        a=cv2.GaussianBlur(feature,(3,3),.7).astype(np.float32)/255
        overlaps=(2*np.einsum('ij,j->i',self.templates,a.ravel(),optimize=False)
                /(self.norms+np.sum(a*a)+1e-6)) if self.features else np.empty(0)
        scores=np.maximum.reduceat(overlaps,self.template_offsets) if self.features else []
        scored=[]
        for index,(entry,template) in enumerate(self.features):
            # Binary overlap, allowing a small translation for noisy screenshots.
            score=float(scores[index])
            scored.append((score,entry))
        if not scored:
            return Match()
        scored.sort(key=lambda pair:pair[0],reverse=True)
        score,entry=scored[0]
        gap=score-scored[1][0] if len(scored)>1 else score
        color_tie=False
        if gap<self.min_margin and color_confidence>=.7:
            relevant=[pair for pair in scored if pair[1].rarity==rarity]
            if relevant:
                preferred,preferred_entry=relevant[0]
                color_gap=preferred-relevant[1][0] if len(relevant)>1 else preferred
                # Colour is an independent tie breaker, never a bonus to the
                # glyph score or a rescue for a poor glyph/contour match.
                if score-preferred<=self.min_margin and color_gap>=self.min_margin:
                    score,entry,gap=preferred,preferred_entry,color_gap;color_tie=True
        index=next(i for i,(e,_) in enumerate(self.features) if e.id==entry.id)
        start=self.template_offsets[index];end=self.template_offsets[index+1] if index+1<len(self.features) else len(overlaps)
        template=self.templates[start+overlaps[start:end].argmax()].reshape(64,64)
        contour=self.contour_agreement(a,template)
        detailed=False;detail_score=0.
        self.last_timings['recognition_stage1']=time.perf_counter()-started;second_started=time.perf_counter()
        if score>=self.threshold and contour>=.68 and gap<self.min_margin:
            # A second glyph descriptor retains small strokes lost by blur.
            # It can resolve close candidates only with its own unchanged
            # score/margin/contour thresholds, never by boosting colour.
            sharp=feature.ravel().astype(np.float32)/255
            eligible=[];sharp_norm=np.sum(sharp*sharp)
            for i,(e,_) in enumerate(self.features):
                if float(scores[i])<scored[0][0]-self.min_margin:continue
                begin=self.template_offsets[i];finish=self.template_offsets[i+1] if i+1<len(self.features) else len(self.templates)
                detail=2*np.einsum('ij,j->i',self.detail_templates[begin:finish],sharp,optimize=False)/(self.detail_norms[begin:finish]+sharp_norm+1e-6)
                best=int(detail.argmax());eligible.append((float(detail[best]),i,e,begin+best))
            eligible.sort(reverse=True,key=lambda p:p[0])
            ds,di,de,best=eligible[0];dg=ds-eligible[1][0] if len(eligible)>1 else ds
            dc=self.contour_agreement(sharp.reshape(64,64),self.detail_templates[best].reshape(64,64))
            if ds>=self.threshold and dg>=self.min_margin and dc>=.68:
                index,entry,gap=di,de,dg;score=float(scores[index]);detailed=True;detail_score=ds;color_tie=False
                begin=self.template_offsets[index];finish=self.template_offsets[index+1] if index+1<len(self.features) else len(overlaps)
                contour=self.contour_agreement(a,self.templates[begin+overlaps[begin:finish].argmax()].reshape(64,64))
        reason=('contour_mismatch' if contour<.68 else 'low_glyph_score' if score<self.threshold
                else 'ambiguous_candidates' if gap<self.min_margin else 'accepted')
        method='fine_glyph' if detailed else 'rarity_tie' if color_tie else 'glyph';reranked=[]
        if reason!='accepted':
            descriptor=self.gradient_descriptor(feature)
            for primary,e in scored[:10]:
                i=next(i for i,(item,_) in enumerate(self.features) if item.id==e.id)
                begin=self.template_offsets[i];finish=self.template_offsets[i+1] if i+1<len(self.features) else len(overlaps)
                best=begin+int(overlaps[begin:finish].argmax());candidate_contour=self.contour_agreement(a,self.templates[best].reshape(64,64))
                if e.id not in self._gradient_cache:self._gradient_cache[e.id]=[self.gradient_descriptor(r) for r in self.references[e.id]]
                gradient=max(float(np.dot(descriptor,r)) for r in self._gradient_cache[e.id])
                reranked.append(dict(id=e.id,glyph=round(primary,4),contour=round(candidate_contour,4),gradient=round(gradient,4)))
            reranked.sort(key=lambda item:item['gradient'],reverse=True)
            first=reranked[0];gradient_gap=first['gradient']-reranked[1]['gradient'] if len(reranked)>1 else first['gradient']
            if first['glyph']>=self.threshold and first['contour']>=.68 and first['gradient']>=.92 and gradient_gap>=.08:
                entry=self.by_id[first['id']];score=first['glyph'];contour=first['contour'];gap=gradient_gap
                index=next(i for i,(e,_) in enumerate(self.features) if e.id==entry.id);reason='accepted';method='gradient';color_tie=False
        self.last_timings['recognition_stage2']=time.perf_counter()-second_started
        accepted=reason=='accepted'
        candidates=[(e.id,round(s,4)) for s,e in scored[:3]]
        result=Match(entry.id if accepted else None,entry.id,round(score,4),round(gap,4),candidates,
            reason,rarity,round(float(scores[index]),4),round(contour,4),
            rarity_confidence=color_confidence,rarity_tie_break=color_tie and accepted,
            glyph_detail_score=detail_score,glyph_detail_tie_break=detailed and accepted,
            recognition_status=RecognitionStatus.RECOGNIZED if accepted else RecognitionStatus.AMBIGUOUS if reason=='ambiguous_candidates' else RecognitionStatus.UNKNOWN,
            top10=[(e.id,round(s,4)) for s,e in scored[:10]],reranked=reranked,recognition_method=method)
        # Preserve the already computed initial scores in RAM only. Compact
        # checks bound omitted templates without recomparing their images.
        result._opening_overlaps=overlaps.copy();result._opening_overlaps.setflags(write=False)
        self.match_cache[key]=replace(result,candidates=list(result.candidates))
        self.match_cache[key]._opening_overlaps=result._opening_overlaps
        while len(self.match_cache)>512:self.match_cache.popitem(last=False)
        return result

    @staticmethod
    def gradient_descriptor(feature):
        image=feature.astype(np.float32)/255
        gx=cv2.Sobel(image,cv2.CV_32F,1,0,ksize=3);gy=cv2.Sobel(image,cv2.CV_32F,0,1,ksize=3)
        magnitude,angle=cv2.cartToPolar(gx,gy)
        yy,xx=np.indices(image.shape);bins=((yy//8)*8+xx//8)*8+(angle*8/(2*np.pi)).astype(int)%8
        vector=np.bincount(bins.ravel(),weights=magnitude.ravel(),minlength=512).astype(np.float32)
        return vector/(np.linalg.norm(vector)+1e-6)

    @staticmethod
    def contour_agreement(a,b):
        def boundary(c):
            binary=(c>.45).astype(np.uint8)
            return cv2.morphologyEx(binary,cv2.MORPH_GRADIENT,np.ones((3,3),np.uint8))
        ea,eb=boundary(a),boundary(b)
        da=cv2.dilate(ea,np.ones((3,3),np.uint8)); db=cv2.dilate(eb,np.ones((3,3),np.uint8))
        precision=float(np.sum(ea*db))/max(1,int(ea.sum()))
        recall=float(np.sum(eb*da))/max(1,int(eb.sum()))
        return 2*precision*recall/max(1e-6,precision+recall)
