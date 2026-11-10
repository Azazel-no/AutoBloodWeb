"""Conservative candidate search for binary text, with exhaustive fallback."""
import math
import cv2
import numpy as np


def binary_match(mask,template,threshold=.96,*,fast=False,workspace=None,stats=None):
    """Find a peak and a second separate peak over the entire search domain.

    For binary Pearson correlation >= r, white occupancy q must lie between
    p*r*r/(1-p+p*r*r) and p/(p+(1-p)*r*r), where p is template occupancy.
    This necessary bound rejects impossible windows, not unobserved screens.
    Every surviving region still uses the original OpenCV correlation/limit.
    """
    h,w=mask.shape;th,tw=template.shape
    if h<th or w<tw:return 0.,(0,0),False
    def count(name):
        if stats is not None:stats[name]=stats.get(name,0)+1
    def exhaustive():
        count('ui_exhaustive_searches')
        response=cv2.matchTemplate(mask,template,cv2.TM_CCOEFF_NORMED)
        _,score,_,point=cv2.minMaxLoc(response)
        response[max(0,point[1]-th):point[1]+th+1,max(0,point[0]-tw):point[0]+tw+1]=-1
        return float(score),point,bool(response.max(initial=-1)>=threshold)
    if not fast or (h-th+1)*(w-tw+1)<20000:return exhaustive()
    p=float(np.count_nonzero(template))/template.size
    if not 0<p<1:return exhaustive()
    count('ui_candidate_searches')
    workspace={} if workspace is None else workspace
    key=(mask.ctypes.data,mask.shape,mask.strides)
    integrals=workspace.setdefault('integrals',{})
    integral=integrals.get(key)
    if integral is None:
        integral=cv2.integral(mask,sdepth=cv2.CV_32S if mask.size*255<2**31 else cv2.CV_64F)
        integrals[key]=integral
    counts=integral[th:,tw:]-integral[:-th,tw:]-integral[th:,:-tw]+integral[:-th,:-tw]
    r2=threshold*threshold
    low=math.floor(p*r2/(1-p+p*r2)*template.size-2)*255
    high=math.ceil(p/(p+(1-p)*r2)*template.size+2)*255
    possible=((counts>=low)&(counts<=high)).astype(np.uint8)
    candidates=int(np.count_nonzero(possible))
    if not candidates:return 0.,(0,0),False
    # Correlation also bounds total binary Hamming distance. Row occupancy
    # differences on disjoint rows are a lower bound for that distance. This
    # rejects white scenery without FFT-searching its large connected boxes.
    qlow=p*r2/(1-p+p*r2);qhigh=p/(p+(1-p)*r2)
    max_mismatch=math.ceil(max(p-qlow,qhigh-p)*template.size+4)
    expected=np.count_nonzero(template,axis=1)
    rows=dict.fromkeys((0,th-1,int(expected.argmin()),int(expected.argmax()),int(np.argsort(expected)[-2]) if th>1 else 0))
    positions=cv2.findNonZero(possible).reshape(-1,2)
    xx,yy=positions[:,0],positions[:,1]
    lower=np.zeros(len(xx),np.int32)
    for row in rows:
        y=yy+row
        row_white=(integral[y+1,xx+tw]-integral[y,xx+tw]-integral[y+1,xx]+integral[y,xx])//255
        lower+=np.abs(row_white-int(expected[row])).astype(np.int32)
    keep=lower<=max_mismatch
    positions=positions[keep]
    candidates=len(positions)
    if not candidates:return 0.,(0,0),False
    if stats is not None:stats['ui_candidate_positions']=stats.get('ui_candidate_positions',0)+candidates
    if candidates>possible.size*.15:return exhaustive()
    if candidates<4096:
        # The conservative gates usually leave only a few text positions.
        # Group their exact 8-connected components without labelling another
        # full-client array. Dense cases retain OpenCV's original grouping.
        remaining=set(map(tuple,positions.tolist()));boxes=[]
        while remaining:
            x,y=remaining.pop();left=right=x;top=bottom=y;todo=[(x,y)];area=0
            while todo:
                x,y=todo.pop();area+=1
                left=min(left,x);right=max(right,x);top=min(top,y);bottom=max(bottom,y)
                for dx,dy in ((-1,-1),(0,-1),(1,-1),(-1,0),(1,0),(-1,1),(0,1),(1,1)):
                    point=(x+dx,y+dy)
                    if point in remaining:remaining.remove(point);todo.append(point)
            boxes.append((left,top,right-left+1,bottom-top+1,area))
            if len(boxes)>128:return exhaustive()
    else:
        possible[yy[~keep],xx[~keep]]=0
        n,_,all_boxes,_=cv2.connectedComponentsWithStats(possible,8)
        if n>129:return exhaustive()
        boxes=all_boxes[1:]
    peaks=[]
    for x,y,bw,bh,area in boxes:
        response=cv2.matchTemplate(mask[y:y+bh+th-1,x:x+bw+tw-1],template,cv2.TM_CCOEFF_NORMED)
        # FFT/ROI roundoff at the actual decision boundary must use the old
        # full-domain computation, rather than changing a strict threshold.
        if np.any(np.abs(response-threshold)<1e-5):return exhaustive()
        yy,xx=np.where(response>=threshold)
        peaks.extend((float(response[b,a]),(int(x+a),int(y+b))) for a,b in zip(xx,yy))
        if len(peaks)>100000:return exhaustive()
    if not peaks:return 0.,(0,0),False
    score,point=max(peaks,key=lambda item:(item[0],-item[1][1],-item[1][0]))
    separate=any(abs(q[0]-point[0])>tw or abs(q[1]-point[1])>th for value,q in peaks)
    return score,point,separate
