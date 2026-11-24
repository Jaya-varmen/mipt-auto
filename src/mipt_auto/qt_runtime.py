"""Подготовка Qt без создания окна и без изменения системных настроек."""
from pathlib import Path
import shutil
import sys

from .errors import AppError


def prepare_qt_plugins(cache_dir: Path):
    if sys.platform != "darwin":
        return
    from PySide6.QtCore import QCoreApplication, QDir, QFileInfo, QLibraryInfo, QPluginLoader, qVersion

    original = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
    cocoa = original / "platforms" / "libqcocoa.dylib"
    if not cocoa.is_file():
        raise AppError("Не установлен графический модуль Qt для macOS.")
    # На этом Mac QFileInfo считает плагины внутри .venv скрытыми.
    # QFactoryLoader исключает их при перечислении, хотя dlopen работает.
    root = original
    if QFileInfo(str(cocoa)).isHidden():
        root = cache_dir / f"qt-{qVersion()}"
        root.mkdir(parents=True, exist_ok=True)
        for source in original.rglob("*"):
            target = root / source.relative_to(original)
            if source.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif source.is_file() and (not target.is_file() or target.stat().st_size != source.stat().st_size):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
    platforms = QDir(str(root / "platforms"))
    visible = platforms.entryList(["libqcocoa.dylib"], QDir.Filter.Files)
    if not visible:
        raise AppError("Qt не видит свой графический модуль. Папка проекта и кэша Qt не должна быть скрытой.")
    loader = QPluginLoader(str(root / "platforms" / "libqcocoa.dylib"))
    if not loader.load():
        raise AppError("Не удалось загрузить графический модуль Qt. Проверьте установку зависимостей.")
    QCoreApplication.setLibraryPaths([str(root)])
