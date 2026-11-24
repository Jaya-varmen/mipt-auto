"""Обновление исходного приложения из публичного GitHub Release."""
from dataclasses import dataclass
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile

from . import __version__
from .errors import AppError


REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
MAX_ARCHIVE_SIZE = 250 * 1024 * 1024
DEFAULT_REPOSITORY = "Jaya-varmen/mipt-auto"


def _https_context() -> ssl.SSLContext:
    # В python.org Python для macOS путь к корневым сертификатам может быть пустым.
    # Системный пакет macOS работает независимо от окружения Finder и терминала.
    macos_bundle = Path("/etc/ssl/cert.pem")
    if sys.platform == "darwin" and macos_bundle.is_file():
        return ssl.create_default_context(cafile=str(macos_bundle))
    return ssl.create_default_context()


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    digest: str
    name: str


def asset_name(system: str | None = None, machine: str | None = None) -> str:
    system, machine = system or sys.platform, (machine or platform.machine()).lower()
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        return "mipt-auto-macos-arm64.zip"
    if system == "win32" and machine in {"amd64", "x86_64"}:
        return "mipt-auto-windows-x64.zip"
    raise AppError("Для этой операционной системы пока нет пакета обновления.")


def load_repository(project_dir: Path) -> str:
    path = Path(project_dir) / "update-source.json"
    if not path.exists():
        return DEFAULT_REPOSITORY
    try:
        repository = json.loads(path.read_text(encoding="utf-8"))["repository"]
    except (OSError, ValueError, KeyError, TypeError):
        raise AppError("Обновления будут доступны после подключения GitHub-репозитория.") from None
    if not isinstance(repository, str) or not REPOSITORY_PATTERN.fullmatch(repository):
        raise AppError("Укажите repository в формате владелец/репозиторий.")
    return repository


def version_tuple(value: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.fullmatch(value)
    if not match:
        raise AppError("Неизвестный формат версии выпуска GitHub.")
    return tuple(int(part) for part in match.groups())


def select_release(payload: dict, current_version: str, name: str) -> Release | None:
    if not isinstance(payload, dict) or payload.get("draft") or payload.get("prerelease"):
        raise AppError("GitHub не вернул опубликованный выпуск.")
    latest = str(payload.get("tag_name", ""))
    if version_tuple(latest) <= version_tuple(current_version):
        return None
    matches = [item for item in payload.get("assets", []) if item.get("name") == name]
    if len(matches) != 1:
        raise AppError("В выпуске нет пакета для этой операционной системы.")
    item = matches[0]
    url, digest = item.get("browser_download_url", ""), item.get("digest", "")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "github.com" or not digest.startswith("sha256:"):
        raise AppError("В выпуске нет проверяемого файла обновления.")
    checksum = digest.partition(":")[2]
    if not re.fullmatch(r"[a-fA-F0-9]{64}", checksum):
        raise AppError("Некорректная контрольная сумма обновления.")
    return Release(latest.lstrip("v"), url, checksum.lower(), name)


def check_latest_release(project_dir: Path) -> Release | None:
    repository = load_repository(project_dir)
    context = _https_context()
    request = Request(f"https://api.github.com/repos/{repository}/releases/latest",
                      headers={"Accept": "application/vnd.github+json", "User-Agent": "MIPT-Auto-Updater"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=15, context=context) as response:
                payload = json.load(response)
            break
        except HTTPError as exc:
            if exc.code in {429, 500, 502, 503, 504} and attempt < 2:
                time.sleep(attempt + 1)
                continue
            if exc.code == 404:
                raise AppError("В GitHub пока нет опубликованного выпуска. Проверьте страницу Releases.") from None
            raise AppError(f"GitHub вернул ошибку HTTP {exc.code}. Повторите проверку позже.") from None
        except (URLError, TimeoutError) as exc:
            if attempt < 2:
                time.sleep(attempt + 1)
                continue
            reason = getattr(exc, "reason", exc)
            raise AppError(f"Не удалось подключиться к api.github.com: {reason}") from None
        except ValueError:
            raise AppError("GitHub вернул некорректный ответ. Повторите проверку позже.") from None
    return select_release(payload, __version__, asset_name())


def download_release(release: Release, data_dir: Path) -> Path:
    directory = Path(data_dir) / "updates"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / release.name
    temporary = directory / (release.name + ".part")
    digest, size = hashlib.sha256(), 0
    try:
        with urlopen(Request(release.url, headers={"User-Agent": "MIPT-Auto-Updater"}),
                     timeout=30, context=_https_context()) as response, temporary.open("wb") as output:
            while block := response.read(1024 * 1024):
                size += len(block)
                if size > MAX_ARCHIVE_SIZE:
                    raise AppError("Файл обновления слишком велик.")
                digest.update(block)
                output.write(block)
        if digest.hexdigest() != release.digest:
            raise AppError("Контрольная сумма обновления не совпала.")
        os.replace(temporary, target)
    except (HTTPError, URLError, TimeoutError, OSError):
        raise AppError("Не удалось скачать обновление.") from None
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _allowed(name: str) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or len(path.parts) < 1:
        return False
    if name in {"pyproject.toml", "README.md", "Windows-Setup.cmd", "Windows-Start.cmd",
                "Запустить MIPT Auto.command"}:
        return True
    if len(path.parts) >= 3 and path.parts[:2] == ("src", "mipt_auto"):
        return path.suffix in {".py", ".html"}
    if len(path.parts) == 2 and path.parts[0] == "config":
        return path.suffix == ".json" and path.name in {"edu-observed.json", "edu-8-9.json", "site.example.json"}
    return False


def install_archive(project_dir: Path, archive: Path, *, expected_version: str = "", run_pip: bool = True) -> None:
    """Ставит только файлы программы; локальные CSV/JSON/data не затрагивает."""
    project_dir = Path(project_dir)
    updates_dir = project_dir / "data" / "updates"
    updates_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=updates_dir, prefix="install-") as temporary:
        stage = Path(temporary) / "stage"
        backup = Path(temporary) / "backup"
        stage.mkdir()
        backup.mkdir()
        try:
            bundle = ZipFile(archive)
        except (OSError, BadZipFile):
            raise AppError("Не удалось открыть архив обновления.") from None
        with bundle:
            files = [item for item in bundle.infolist() if not item.is_dir()]
            names = [item.filename for item in files]
            if not files or len(names) != len(set(names)) or any(not _allowed(name) for name in names):
                raise AppError("Архив обновления содержит неожиданные файлы.")
            if sum(item.file_size for item in files) > MAX_ARCHIVE_SIZE:
                raise AppError("Распакованный архив обновления слишком велик.")
            if "pyproject.toml" not in names or "src/mipt_auto/__init__.py" not in names:
                raise AppError("Архив обновления неполон.")
            for item in files:
                if (item.external_attr >> 16) & 0o170000 == 0o120000:
                    raise AppError("Архив обновления содержит ссылку.")
                if item.file_size > MAX_ARCHIVE_SIZE:
                    raise AppError("Архив обновления слишком велик.")
                target = stage / item.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(item) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                mode = (item.external_attr >> 16) & 0o777
                if mode:
                    target.chmod(mode)
        if expected_version:
            version_source = (stage / "src" / "mipt_auto" / "__init__.py").read_text(encoding="utf-8")
            project_source = (stage / "pyproject.toml").read_text(encoding="utf-8")
            if (f'__version__ = "{expected_version}"' not in version_source
                    or f'version = "{expected_version}"' not in project_source):
                raise AppError("Версия в архиве не совпадает с выпуском GitHub.")
        changed: list[tuple[Path, Path | None]] = []
        try:
            for item in files:
                target = project_dir / item.filename
                old = backup / item.filename if target.exists() else None
                if old is not None:
                    old.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, old)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(stage / item.filename, target)
                changed.append((target, old))
            if run_pip:
                subprocess.run([sys.executable, "-m", "pip", "install", "-e", "."], cwd=project_dir,
                               check=True, timeout=300, stdout=subprocess.DEVNULL)
                if sys.platform == "win32":
                    subprocess.run([sys.executable, "-m", "mipt_auto.windows_setup"], cwd=project_dir,
                                   check=True, timeout=30, stdout=subprocess.DEVNULL)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            for target, old in reversed(changed):
                if old is None:
                    target.unlink(missing_ok=True)
                else:
                    os.replace(old, target)
            if run_pip:
                try:
                    subprocess.run([sys.executable, "-m", "pip", "install", "-e", "."], cwd=project_dir,
                                   timeout=300, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            raise AppError("Установка обновления не удалась. Файлы программы восстановлены.") from None


def _wait_for_parent(pid: int, timeout: float = 60) -> None:
    if sys.platform == "win32":
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if handle:
            result = kernel.WaitForSingleObject(handle, int(timeout * 1000))
            kernel.CloseHandle(handle)
            if result == 0x00000102:
                raise AppError("Программа не закрылась перед установкой обновления.")
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)
    raise AppError("Программа не закрылась перед установкой обновления.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("apply", choices=["apply"])
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--expected-version", required=True)
    args = parser.parse_args()
    try:
        _wait_for_parent(args.parent_pid)
        install_archive(args.project_dir, args.archive, expected_version=args.expected_version)
    except AppError as exc:
        print(exc, file=sys.stderr)
        subprocess.Popen([sys.executable, "-m", "mipt_auto.gui"], cwd=args.project_dir)
        return 1
    subprocess.Popen([sys.executable, "-m", "mipt_auto.gui"], cwd=args.project_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
