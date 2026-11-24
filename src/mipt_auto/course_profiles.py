"""Выбор профиля сайта для выбранного учебного курса."""
from pathlib import Path

from .errors import AppError


COURSES = {
    "10-11": "10–11 класс",
    "8-9": "8–9 класс",
}


def course_config_path(project_dir: Path, course: str, platform: str) -> Path:
    project_dir = Path(project_dir)
    if course not in COURSES:
        raise AppError("Выберите курс 8–9 или 10–11 классов.")
    if course == "10-11":
        windows = project_dir / "config.windows.json"
        return windows if platform == "win32" and windows.is_file() else project_dir / "config.local.json"
    if platform == "win32":
        return project_dir / "config.windows.8-9.json"
    return project_dir / "config" / "edu-8-9.json"
