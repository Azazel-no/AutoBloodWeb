"""Fresh native hover pixels in the same coordinate system as a clean map."""
from dataclasses import replace
import cv2
from src.core.screen_manager import ScreenError


def capture_preview(screen,clean_frame):
    full=screen.capture_overlay(clean_frame)
    if (not full.probe_only or full.window.signature!=clean_frame.window.signature
            or full.captured_at<=clean_frame.captured_at):raise ScreenError('preview_context_unproven')
    roi=clean_frame.roi;image=full.image
    if roi.x<0 or roi.y<0 or roi.x+roi.width>image.shape[1] or roi.y+roi.height>image.shape[0]:
        raise ScreenError('roi_invalid')
    crop=image[roi.y:roi.y+roi.height,roi.x:roi.x+roi.width]
    h,w=clean_frame.image.shape[:2]
    if crop.shape[:2]!=(h,w):crop=cv2.resize(crop,(w,h),interpolation=cv2.INTER_AREA)
    # Price labels may be outside Bloodweb ROI. Preserve the full actual client
    # pixels independently, without feeding them to the compact node detector.
    return replace(full,image=crop,roi=roi,level=clean_frame.level,
                   level_confidence=clean_frame.level_confidence,raw_image=full.image)
