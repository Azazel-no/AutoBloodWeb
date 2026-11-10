"""Independent background/marker evidence; uncertain shapes never filter IDs."""
from dataclasses import dataclass,field
import cv2,numpy as np


@dataclass
class CategoryEvidence:
    category: str='unknown'
    confidence: float=0.
    candidates: list[str]=field(default_factory=lambda:['item','addon','offering'])
    polygon_sides: int=0
    orange_fraction: float=0.
    event_offering: bool=False
    reason: str='background_uncertain'


def classify_category(image,radius):
    h,w=image.shape[:2];yy,xx=np.ogrid[:h,:w]
    core=(xx-w//2)**2+(yy-h//2)**2<(radius*.80)**2
    hsv=cv2.cvtColor(image[:,:,:3],cv2.COLOR_BGR2HSV)
    colored=((hsv[:,:,1]>85)&(hsv[:,:,2]>45)&core).astype(np.uint8)*255
    count,labels,stats,_=cv2.connectedComponentsWithStats(colored)
    if count<2:return CategoryEvidence()
    index=1+int(np.argmax(stats[1:,cv2.CC_STAT_AREA]))
    if stats[index,cv2.CC_STAT_AREA]<max(30,core.sum()*.12):return CategoryEvidence()
    points=cv2.findNonZero((labels==index).astype(np.uint8))
    hull=cv2.convexHull(points);perimeter=cv2.arcLength(hull,True)
    polygon=cv2.approxPolyDP(hull,.035*perimeter,True)
    vertices=polygon[:,0];sides=len(vertices)
    orange=(hsv[:,:,0]>=12)&(hsv[:,:,0]<=28)&(hsv[:,:,1]>=180)&(hsv[:,:,2]>=145)&core
    orange_fraction=float(orange.sum()/max(1,core.sum()))
    # Six symmetric corners on the coloured offering background are mandatory.
    # A coloured rim, diamond perk, square item, upgrade cross or mystery box
    # cannot establish this abstract identity. No white glyph is required.
    center=np.array([w/2,h/2]);dist=np.linalg.norm(vertices-center,axis=1)
    angles=np.sort(np.mod(np.arctan2(vertices[:,1]-center[1],vertices[:,0]-center[0]),2*np.pi))
    gaps=np.diff(np.r_[angles,angles[0]+2*np.pi])
    hexagon=(sides==6 and dist.min()>radius*.48 and dist.max()/max(1,dist.min())<1.22 and
             np.max(np.abs(gaps-np.pi/3))<.24 and cv2.contourArea(hull)>radius*radius*.85)
    if hexagon:
        return CategoryEvidence('offering',.95,['offering'],sides,orange_fraction,
            orange_fraction>=.45,'offering_hexagon')
    if sides==4:
        # Squares remain ambiguous Item/Add-on; ID matching checks both.
        return CategoryEvidence('unknown',.5,['item','addon','offering'],sides,orange_fraction,False,'quadrilateral_uncertain')
    return CategoryEvidence(polygon_sides=sides,orange_fraction=orange_fraction)
