"""Создаёт отдельный профиль Windows, не меняя настройки macOS."""
import json
from pathlib import Path

from .errors import AppError
from .storage import private_json


def create_windows_config(project_dir: Path) -> Path:
    target = project_dir / "config.windows.json"
    if target.is_file():
        return target
    local = project_dir / "config.local.json"
    source = local if local.is_file() else project_dir / "config" / "edu-observed.json"
    try:
        config = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError
        config["browser_channel"] = ""  # Playwright Chromium, установленный Windows-Setup.cmd.
        private_json(target, config)
    except (OSError, ValueError):
        raise AppError("Не удалось создать конфигурацию Windows. Проверьте файлы проекта.") from None
    return target


def create_windows_8_9_config(project_dir: Path) -> Path:
    target = project_dir / "config.windows.8-9.json"
    if target.is_file():
        return target
    source = project_dir / "config" / "edu-8-9.json"
    try:
        config = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError
        config["browser_channel"] = ""
        private_json(target, config)
    except (OSError, ValueError):
        raise AppError("Не удалось создать профиль Windows для курса 8–9.") from None
    return target


def main():
    try:
        target = create_windows_config(Path.cwd())
        second = create_windows_8_9_config(Path.cwd())
    except AppError as exc:
        print(exc)
        return 1
    print(f"Windows config ready: {target.resolve()}")
    print(f"Windows 8-9 config ready: {second.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
