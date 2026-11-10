"""One shared asset/data root for source runs and the Windows executable."""
import sys
from pathlib import Path


def project_root():
    if getattr(sys,'frozen',False):
        root=Path(sys.executable).resolve().parent
        if not (root/'assets/manifest.json').is_file():
            raise RuntimeError('Keep AutoBloodWeb.exe beside the existing assets, references and data folders.')
        return root
    return Path(__file__).resolve().parents[2]
