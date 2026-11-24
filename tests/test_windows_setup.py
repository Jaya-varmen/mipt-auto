import json

from mipt_auto.windows_setup import create_windows_config, create_windows_8_9_config


def test_windows_setup_creates_separate_chromium_config(tmp_path):
    source = tmp_path / "config.local.json"
    source.write_text(json.dumps({"browser_channel": "chrome", "course_key": "test"}), encoding="utf-8")
    target = create_windows_config(tmp_path)
    assert target == tmp_path / "config.windows.json"
    assert json.loads(target.read_text(encoding="utf-8"))["browser_channel"] == ""
    assert json.loads(source.read_text(encoding="utf-8"))["browser_channel"] == "chrome"
    assert create_windows_config(tmp_path) == target


def test_windows_8_9_config_uses_chromium(tmp_path):
    directory = tmp_path / "config"
    directory.mkdir()
    source = directory / "edu-8-9.json"
    source.write_text(json.dumps({"browser_channel": "chrome", "course_key": "python-8-9"}), encoding="utf-8")
    target = create_windows_8_9_config(tmp_path)
    assert json.loads(target.read_text(encoding="utf-8")) == {"browser_channel": "", "course_key": "python-8-9"}
