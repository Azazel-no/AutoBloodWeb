import sys
import pytest
from src.core.runtime_paths import project_root


def test_source_root_is_independent_of_current_directory(root,tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert project_root()==root


def test_frozen_runtime_uses_one_existing_asset_root_without_copying_profiles(tmp_path,monkeypatch):
    (tmp_path/'assets').mkdir();(tmp_path/'assets/manifest.json').write_text('{}',encoding='utf-8')
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'executable',str(tmp_path/'AutoBloodWeb-Preview.exe'))
    assert project_root()==tmp_path
    assert not (tmp_path/'data/preferences.json').exists()


def test_moved_executable_does_not_create_a_second_empty_library(tmp_path,monkeypatch):
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'executable',str(tmp_path/'AutoBloodWeb-Preview.exe'))
    with pytest.raises(RuntimeError,match='existing assets'):project_root()
    assert not (tmp_path/'assets').exists()
