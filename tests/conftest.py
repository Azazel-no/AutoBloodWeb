from pathlib import Path
import pytest
from src.core.icon_library import IconLibrary
from src.core.bloodweb_detector import BloodwebDetector

ROOT=Path(__file__).resolve().parents[1]


def pytest_configure(config):
    # Keep each run in a fresh workspace directory. Sandboxed and ordinary
    # Windows users cannot necessarily share the same system pytest temp ACL.
    if not config.option.basetemp:
        import uuid
        (ROOT/'debug/tests/tmp').mkdir(parents=True,exist_ok=True)
        config.option.basetemp=str(ROOT/'debug/tests/tmp'/uuid.uuid4().hex)


@pytest.fixture(scope='session')
def library():
    return IconLibrary(ROOT).build()


@pytest.fixture(scope='session')
def detector(library):
    return BloodwebDetector(library)


@pytest.fixture(scope='session')
def root():
    return ROOT


@pytest.fixture(autouse=True)
def no_recommended_priorities(monkeypatch):
    """A fresh test profile starts with empty lists unless a test fills them itself."""
    monkeypatch.setattr('src.core.tierlist.recommended', lambda root, characters=(): {})
