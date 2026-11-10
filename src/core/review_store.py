"""Reversible user labels for offline validation; never imported by recognition."""
import hashlib,json
from datetime import datetime,timezone
from pathlib import Path
from src.utils.images import write_image


class ReviewStore:
    def __init__(self,path):
        self.path=Path(path)
        try: self.data=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError): self.data={}

    def key(self,image,node):
        return hashlib.sha256(image.tobytes()).hexdigest()+f'_{node.x}_{node.y}'

    def save(self,image,node,expected,kind,source):
        key=self.key(image,node); record=self.data.setdefault(key,{'history':[],'current':None})
        record['history'].append(record['current'])
        r=node.content_radius or node.radius
        sample=self.path.parent/'review_samples'/f'{key}.png'
        write_image(sample,image[max(0,node.y-r):node.y+r+1,max(0,node.x-r):node.x+r+1])
        record['current']=dict(expected=expected,type=kind,source=source,sample=str(sample),
            time=datetime.now(timezone.utc).isoformat(),x=node.x,y=node.y)
        self.persist()

    def undo(self,image,node):
        record=self.data.get(self.key(image,node))
        if record and record['history']: record['current']=record['history'].pop(); self.persist()

    def persist(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(self.data,ensure_ascii=False,indent=2),encoding='utf-8'); temporary.replace(self.path)
