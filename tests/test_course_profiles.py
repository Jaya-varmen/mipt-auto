from pathlib import Path

import pytest

from mipt_auto.config import load_config
from mipt_auto.course_profiles import course_config_path
from mipt_auto.errors import AppError


ROOT = Path(__file__).parents[1]


def test_8_9_profile_uses_observed_course_and_module():
    config = load_config(ROOT / "config" / "edu-8-9.json")
    assert config.course_url.endswith("/python-base-8-9-class-offline/online-course/268/")
    assert config.module_title == "Модуль 1. Базовый синтаксис, условия и циклы"
    assert config.course_key == "python-8-9"


def test_course_config_paths_and_invalid_choice(tmp_path):
    (tmp_path / "config.windows.json").write_text("{}", encoding="utf-8")
    assert course_config_path(tmp_path, "10-11", "darwin") == tmp_path / "config.local.json"
    assert course_config_path(tmp_path, "10-11", "win32") == tmp_path / "config.windows.json"
    assert course_config_path(tmp_path, "8-9", "darwin") == tmp_path / "config" / "edu-8-9.json"
    assert course_config_path(tmp_path, "8-9", "win32") == tmp_path / "config.windows.8-9.json"
    with pytest.raises(AppError):
        course_config_path(tmp_path, "unknown", "darwin")
