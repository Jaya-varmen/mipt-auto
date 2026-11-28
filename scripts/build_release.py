"""Собирает два архива исходной программы без локальных данных."""
import argparse
from pathlib import Path
import re
import sys
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "src" / "mipt_auto" / "__init__.py"
ASSETS = ("mipt-auto-macos-arm64.zip", "mipt-auto-windows-x64.zip")
TOP_LEVEL = ("pyproject.toml", "Windows-Setup.cmd", "Windows-Start.cmd",
             "Запустить MIPT Auto.command")
CONFIG = ("edu-observed.json", "edu-8-9.json", "site.example.json")


def version() -> str:
    match = re.search(r'^__version__\s*=\s*"(\d+\.\d+\.\d+)"',
                      VERSION_FILE.read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        raise RuntimeError("Не найдена версия приложения")
    return match.group(1)


def release_files() -> list[Path]:
    files = [ROOT / name for name in TOP_LEVEL]
    readme = ROOT / "README.md"
    if readme.is_file():
        files.append(readme)
    files += [ROOT / "config" / name for name in CONFIG]
    files += [p for p in (ROOT / "src" / "mipt_auto").rglob("*")
              if p.is_file() and p.suffix in {".py", ".html"}]
    if not all(path.is_file() for path in files):
        raise RuntimeError("В проекте отсутствует файл выпуска")
    return sorted(files)


def build(output: Path, expected_version: str | None = None) -> list[Path]:
    if expected_version and expected_version.lstrip("v") != version():
        raise RuntimeError("Тег GitHub не совпадает с версией приложения")
    output.mkdir(parents=True, exist_ok=True)
    created = []
    for name in ASSETS:
        target = output / name
        with ZipFile(target, "w", ZIP_DEFLATED) as archive:
            for path in release_files():
                archive.write(path, path.relative_to(ROOT).as_posix())
        created.append(target)
    return created


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--tag", default="")
    args = parser.parse_args()
    for path in build(args.output, args.tag):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
