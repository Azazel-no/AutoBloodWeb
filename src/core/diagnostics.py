"""Immutable per-analysis evidence; retention applies only to owned temporary runs."""
from pathlib import Path
import uuid
import json
import hashlib
import time
import shutil
import cv2
from src.utils.images import write_image


class DiagnosticStore:
    def __init__(self, root, max_runs=160, max_bytes=512*1024*1024):
        self.root=Path(root).resolve(); self.max_runs=max_runs; self.max_bytes=max_bytes

    def record(self,image,result,detector,frame=None,plan=None,phase='scan',reason=None,raw=None,full=True):
        identity=f'{time.time_ns()}_{uuid.uuid4().hex[:8]}'
        folder=self.root/identity; folder.mkdir(parents=True,exist_ok=False)
        if not full and phase in ('failure','capture_failed'):
            # Keep original pixels, geometry and the actual attempted point.
            # Per-node PNGs/masks are available in detailed mode, rather than
            # blocking every stopped worker on dozens of redundant encodes.
            result.analysis_id=identity;files={};sizes={}
            def write(name,pixels):
                ok,buffer=cv2.imencode('.png',pixels,[cv2.IMWRITE_PNG_COMPRESSION,1])
                if not ok:raise ValueError('Cannot encode failure evidence')
                payload=buffer.tobytes();(folder/name).write_bytes(payload)
                files[name]=hashlib.sha256(payload).hexdigest();sizes[name]=len(payload)
            source=raw if raw is not None else getattr(frame,'raw_image',None)
            write('raw.png',source if source is not None else image)
            canvas=image.copy()
            for node in result.nodes:cv2.circle(canvas,(round(node.x),round(node.y)),round(node.radius),(0,220,220),1)
            for point in result.details.get('attempted_screen_points',[]):
                if frame:
                    x=round((point[0]-frame.window.client.x-frame.roi.x)*image.shape[1]/frame.roi.width)
                    y=round((point[1]-frame.window.client.y-frame.roi.y)*image.shape[0]/frame.roi.height)
                    cv2.drawMarker(canvas,(x,y),(0,0,255),cv2.MARKER_CROSS,16,2)
            write('click_geometry.png',canvas)
            payload=json.dumps(result.to_dict(),ensure_ascii=False,indent=2).encode('utf-8')
            (folder/'analysis.json').write_bytes(payload);files['analysis.json']=hashlib.sha256(payload).hexdigest();sizes['analysis.json']=len(payload)
            metadata=dict(owner='autobloodweb_v43',pinned=False,phase=phase,reason=reason,mode='compact_failure',
                frame=frame.metadata() if frame else None,analysis_id=identity,files=files,total_bytes=sum(sizes.values()))
            (folder/'manifest.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
            self._maintain(metadata);return folder
        if not full:
            result.analysis_id=identity
            payload=json.dumps(result.to_dict(),ensure_ascii=False,indent=2).encode('utf-8')
            (folder/'analysis.json').write_bytes(payload)
            metadata=dict(owner='autobloodweb_v43',pinned=False,phase=phase,reason=reason,mode='normal',
                frame=frame.metadata() if frame else None,analysis_id=identity,files={'analysis.json':hashlib.sha256(payload).hexdigest()},
                route=vars(plan) if plan else None,screen=result.screen_state.value,diagnostics=result.diagnostics,total_bytes=len(payload))
            (folder/'manifest.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
            self._maintain(metadata)
            return folder
        files={}; sizes={}
        def write(path,pixels):
            path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
            ok,buffer=cv2.imencode('.png',pixels,[cv2.IMWRITE_PNG_COMPRESSION,1])
            if not ok:raise ValueError('Cannot encode diagnostic image')
            payload=buffer.tobytes();path.write_bytes(payload)
            key=str(path.relative_to(folder));files[key]=hashlib.sha256(payload).hexdigest();sizes[key]=len(payload)
        source=raw if raw is not None else getattr(frame,'raw_image',None)
        write(folder/'raw.png',source if source is not None else image)
        write(folder/'input.png',image)
        gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
        write(folder/'gray.png',gray)
        from src.core.bloodweb_detector import red_mask
        write(folder/'red_mask.png',red_mask(cv2.cvtColor(image,cv2.COLOR_BGR2HSV)))
        canvas=image.copy()
        for candidate in result.details.get('candidates',[]):
            x,y,r=candidate['circle']
            cv2.circle(canvas,(x,y),r,(0,180,0) if candidate.get('accepted') else (0,80,200),1)
        write(folder/'candidates.png',canvas)
        result.analysis_id=identity
        detector.save_debug(image,result,folder/'accepted_route.png',plan,writer=write)
        # Only the small JSON needs reading. PNG hashes come from the exact
        # encoded bytes written above, avoiding 50+ redundant disk opens.
        json_path=folder/'accepted_route.json';payload=json_path.read_bytes()
        files[json_path.name]=hashlib.sha256(payload).hexdigest();sizes[json_path.name]=len(payload)
        metadata=dict(owner='autobloodweb_v43',pinned=False,phase=phase,reason=reason,
            frame=frame.metadata() if frame else None,analysis_id=identity,files=files,
            route=vars(plan) if plan else None,screen=result.screen_state.value,diagnostics=result.diagnostics,total_bytes=sum(sizes.values()))
        (folder/'manifest.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
        self._maintain(metadata)
        return folder

    def _maintain(self,metadata):
        self._writes=getattr(self,'_writes',0)+1
        self._bytes=getattr(self,'_bytes',0)+metadata['total_bytes']
        self._runs=getattr(self,'_runs',0)+1
        if self._writes==1 or self._writes%8==0:self.prune();return
        if not metadata.get('pinned'):
            self._retention.append((time.time(),self.root/metadata['analysis_id'],metadata['total_bytes']))
        self._trim_cached()

    def _trim_cached(self):
        while (self._bytes>self.max_bytes or self._runs>self.max_runs) and self._retention:
            _,folder,size=self._retention.pop(0)
            if folder.resolve().parent!=self.root or folder.is_symlink():continue
            try:data=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
            except (OSError,ValueError):
                # Unknown contents are never a deletion candidate.
                self._bytes-=size;self._runs-=1;continue
            if data.get('owner')!='autobloodweb_v43' or data.get('pinned'):
                self._bytes-=size;self._runs-=1;continue
            shutil.rmtree(folder);self._bytes-=size;self._runs-=1
            getattr(self,'_manifests',{}).pop(folder.name,None)

    def prune(self):
        runs=[]
        cache=getattr(self,'_manifests',{});self._manifests=cache
        for folder in self.root.iterdir():
            if not folder.is_dir() or folder.is_symlink(): continue
            if folder.resolve().parent!=self.root: continue
            manifest=folder/'manifest.json'
            try:
                stamp=manifest.stat().st_mtime_ns
                cached=cache.get(folder.name)
                data=cached[1] if cached and cached[0]==stamp else json.loads(manifest.read_text(encoding='utf-8'))
                cache[folder.name]=(stamp,data)
            except (OSError,ValueError): continue
            if data.get('owner')!='autobloodweb_v43' or data.get('pinned') is not False: continue
            size=data.get('total_bytes')
            if not isinstance(size,int):size=sum(p.stat().st_size for p in folder.rglob('*') if p.is_file())
            runs.append((folder.stat().st_mtime,folder,size))
        total=sum(x[2] for x in runs); count=len(runs)
        for _,folder,size in sorted(runs):
            if count<=self.max_runs and total<=self.max_bytes: break
            # The target must remain a direct, owned child of this store.
            if folder.resolve().parent!=self.root or folder.is_symlink(): continue
            shutil.rmtree(folder); total-=size; count-=1
            cache.pop(folder.name,None)
        self._bytes=total;self._runs=count
        self._retention=sorted((stamp,folder,size) for stamp,folder,size in runs if folder.is_dir())
