from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QComboBox
from src.gui.widgets import Preview,BloodButton
from src.core.review_store import ReviewStore
from pathlib import Path
import cv2
from src.core.node_classifier import glyph,node_mask


class ReviewDialog(QDialog):
    def __init__(self,owner,result,node):
        super().__init__(owner); self.setWindowTitle(owner.locale.text('review_node'))
        self.resize(min(owner.width(),600),min(520,owner.height()-60)); self.move(owner.x(),owner.y()+40)
        layout=QVBoxLayout(self); row=QHBoxLayout()
        raw=getattr(result,'raw_image',None)
        r=node.content_radius or node.radius
        patch=raw[max(0,node.y-r):min(raw.shape[0],node.y+r+1),
            max(0,node.x-r):min(raw.shape[1],node.x+r+1)] if raw is not None else None
        for index,path in enumerate((node.crop_path,node.normalized_path)):
            preview=Preview(); preview.setMinimumHeight(160)
            if path and Path(path).is_file():preview.set_image(path)
            elif patch is not None and patch.size:
                pixels=patch
                if index:
                    normalized=glyph(patch,node_mask(r,patch.shape[:2]),remove_badge=True)
                    pixels=cv2.cvtColor(normalized,cv2.COLOR_GRAY2BGR) if normalized is not None else None
                if pixels is not None:preview.set_pixels(pixels.copy())
            row.addWidget(preview)
        layout.addLayout(row)
        reason=QLabel(f'{node.icon_id or owner.locale.text(node.recognition_status)} · Δ={node.icon_margin:.4f} · contour={node.contour_score:.3f}\n'+owner.locale.text(node.match_reason))
        reason.setWordWrap(True); layout.addWidget(reason)
        for ident,score in node.candidates:
            label=QLabel(f'{ident} · {score:.4f}'); layout.addWidget(label)
        self.choice=QComboBox(); self.choice.setMaxVisibleItems(12)
        self.choice.addItem(owner.locale.text('unknown'),(None,'unknown'))
        self.choice.addItem(owner.locale.text('suspected_perk'),(None,'perk'))
        for entry in owner.library.filter(owner.current_role):
            self.choice.addItem(QIcon(str(owner.root/entry.image)),entry.id,(entry.id,entry.category[:-1]))
        layout.addWidget(self.choice)
        notice=QLabel(owner.locale.text('review_notice')); notice.setWordWrap(True); layout.addWidget(notice)
        store=ReviewStore(owner.root/'data/review_annotations.json')
        status=QLabel(); layout.addWidget(status)
        save=BloodButton(owner.locale.text('review_save')); layout.addWidget(save)
        def commit():
            ident,kind=self.choice.currentData()
            store.save(result.raw_image,node,ident,kind,result.source)
            status.setText(owner.locale.text('review_save')+' ✓')
        save.clicked.connect(commit)
        undo=BloodButton(owner.locale.text('review_undo')); layout.addWidget(undo)
        undo.clicked.connect(lambda:(store.undo(result.raw_image,node),status.setText(owner.locale.text('review_undo')+' ✓')))
