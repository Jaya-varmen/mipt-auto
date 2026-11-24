import json
from io import BytesIO
from pathlib import Path
import subprocess
import sys
from urllib.error import URLError
from zipfile import ZipFile

import pytest

from mipt_auto.errors import AppError
from mipt_auto.updater import asset_name, check_latest_release, install_archive, load_repository, select_release


ROOT = Path(__file__).parents[1]


def test_update_repository_defaults_to_public_project(tmp_path):
    assert load_repository(tmp_path) == "Jaya-varmen/mipt-auto"


def test_release_selects_matching_system_and_newer_version():
    assert asset_name("darwin", "arm64") == "mipt-auto-macos-arm64.zip"
    assert asset_name("win32", "AMD64") == "mipt-auto-windows-x64.zip"
    name = "mipt-auto-windows-x64.zip"
    payload = {"tag_name": "v0.3.1", "draft": False, "prerelease": False,
               "assets": [{"name": name,
                           "browser_download_url": "https://github.com/example/project/releases/download/v0.3.1/" + name,
                           "digest": "sha256:" + "a" * 64}]}
    assert select_release(payload, "0.3.0", name).version == "0.3.1"
    assert select_release(payload, "0.3.1", name) is None
    with pytest.raises(AppError):
        select_release(payload, "0.3.0", "mipt-auto-macos-arm64.zip")


def test_release_check_retries_temporary_network_error(monkeypatch, tmp_path):
    import mipt_auto.updater as updater

    name = "mipt-auto-macos-arm64.zip"
    payload = {"tag_name": "v0.3.2", "draft": False, "prerelease": False,
               "assets": [{"name": name,
                           "browser_download_url": "https://github.com/example/project/releases/download/v0.3.2/" + name,
                           "digest": "sha256:" + "a" * 64}]}
    calls = []

    def fake_urlopen(_request, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise URLError("temporary DNS failure")
        return BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(updater, "urlopen", fake_urlopen)
    monkeypatch.setattr(updater, "asset_name", lambda: name)
    monkeypatch.setattr(updater.time, "sleep", lambda _seconds: None)
    assert check_latest_release(tmp_path).version == "0.3.2"
    assert calls == [15, 15]


def test_release_archive_preserves_local_data_and_rejects_secrets(tmp_path):
    output = tmp_path / "dist"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_release.py"), "--output", str(output),
                    "--tag", "v0.3.1"], check=True, capture_output=True)
    archive = output / "mipt-auto-macos-arm64.zip"
    with ZipFile(archive) as bundle:
        assert "config/edu-8-9.json" in bundle.namelist()
        assert "accounts.csv" not in bundle.namelist()
        assert "solutions.local.json" not in bundle.namelist()
    project = tmp_path / "installed"
    project.mkdir()
    (project / "accounts.csv").write_text("email,password\nuser@example.test,secret\n", encoding="utf-8")
    (project / "solutions.local.json").write_text("[]", encoding="utf-8")
    (project / "config.local.json").write_text(json.dumps({"course_key": "private"}), encoding="utf-8")
    (project / "data").mkdir()
    (project / "data" / "progress.sqlite3").write_bytes(b"history")
    install_archive(project, archive, expected_version="0.3.1", run_pip=False)
    assert (project / "accounts.csv").read_text(encoding="utf-8").endswith("secret\n")
    assert (project / "solutions.local.json").read_text(encoding="utf-8") == "[]"
    assert json.loads((project / "config.local.json").read_text(encoding="utf-8"))["course_key"] == "private"
    assert (project / "data" / "progress.sqlite3").read_bytes() == b"history"
    with pytest.raises(AppError):
        install_archive(project, archive, expected_version="0.3.2", run_pip=False)
    bad = tmp_path / "bad.zip"
    with ZipFile(bad, "w") as bundle:
        bundle.writestr("accounts.csv", "email,password\n")
    with pytest.raises(AppError):
        install_archive(project, bad, run_pip=False)
