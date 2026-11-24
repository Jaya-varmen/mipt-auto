from dataclasses import dataclass, field
import json
from pathlib import Path
from urllib.parse import urlsplit

from .errors import AppError


def origin(url: str) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise AppError("Некорректный адрес страницы.") from None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AppError("Ожидается адрес http(s).")
    if parsed.username or parsed.password:
        raise AppError("Адрес не должен содержать логин или пароль.")
    suffix = f":{port}" if port and port != {"https": 443, "http": 80}[parsed.scheme] else ""
    return f"{parsed.scheme}://{parsed.hostname.lower()}{suffix}"


@dataclass(frozen=True)
class LocatorSpec:
    css: str = ""
    text: str = ""
    role: str = ""
    name: str = ""
    scope_css: str = ""

    @classmethod
    def parse(cls, raw: dict) -> "LocatorSpec":
        if not isinstance(raw, dict) or set(raw) - {"css", "text", "role", "name", "scope_css"}:
            raise AppError("Неизвестные поля описания элемента.")
        if not all(isinstance(v, str) for v in raw.values()):
            raise AppError("Поля описания элемента должны быть строками.")
        spec = cls(**raw)
        if sum(bool(x) for x in (spec.css, spec.text, spec.role)) != 1:
            raise AppError("Задайте ровно один способ поиска: css, text или role.")
        if bool(spec.role) != bool(spec.name):
            raise AppError("Для role требуется точное name.")
        return spec

    def locate(self, scope):
        if self.scope_css:
            scope = scope.locator(self.scope_css)
        if self.css:
            return scope.locator(self.css)
        if self.role:
            return scope.get_by_role(self.role, name=self.name, exact=True)
        return scope.get_by_text(self.text, exact=True)


@dataclass
class Config:
    base_url: str = "https://edu.mipt.ru"
    course_url: str = ""
    course_key: str = "python-10-11"
    course_title: str = "Программирование на Python для 10–11 классов (очно)"
    module_title: str = "Модуль 1. Базовый Python в формате ЕГЭ"
    browser_channel: str = ""
    timeout_ms: int = 10000
    navigation_timeout_ms: int = 30000
    navigation_pause_min_seconds: float = 5.0
    navigation_pause_max_seconds: float = 10.0
    result_timeout_ms: int = 45000
    collection: dict = field(default_factory=dict)
    execution: dict = field(default_factory=dict)

    @property
    def portal_origin(self) -> str:
        return origin(self.base_url)

    def validate(self) -> None:
        for key in ("base_url", "course_url", "course_key", "course_title", "module_title", "browser_channel"):
            if not isinstance(getattr(self, key), str):
                raise AppError("Адреса, названия и browser_channel должны быть строками.")
        if not isinstance(self.collection, dict) or not isinstance(self.execution, dict):
            raise AppError("collection и execution должны быть JSON-объектами.")
        if any(type(value) is not int for value in (self.timeout_ms, self.result_timeout_ms, self.navigation_timeout_ms)):
            raise AppError("Тайм-ауты должны быть целыми числами миллисекунд.")
        if self.portal_origin != "https://edu.mipt.ru" and urlsplit(self.base_url).hostname != "127.0.0.1":
            raise AppError("Профиль поддерживает edu.mipt.ru или локальный стенд 127.0.0.1.")
        if self.course_url and origin(self.course_url) != self.portal_origin:
            raise AppError("Адрес курса должен принадлежать тому же сайту.")
        if self.browser_channel not in {"", "chrome"}:
            raise AppError("browser_channel: используйте пустую строку или chrome.")
        if not 500 <= self.timeout_ms <= 120000 or not 500 <= self.result_timeout_ms <= 300000:
            raise AppError("Недопустимый тайм-аут конфигурации.")
        if not 500 <= self.navigation_timeout_ms <= 120000:
            raise AppError("Недопустимый тайм-аут перехода.")
        pauses = (self.navigation_pause_min_seconds, self.navigation_pause_max_seconds)
        if any(type(value) not in (int, float) for value in pauses) or not 0 <= pauses[0] <= pauses[1] <= 30:
            raise AppError("Пауза между переходами должна быть от 0 до 30 секунд.")
        for key in ("course_key", "course_title", "module_title"):
            if not isinstance(getattr(self, key), str) or not getattr(self, key).strip():
                raise AppError("Укажите курс и модуль в конфигурации.")
        allowed_collection = {"scope_css", "row_css", "title_css", "link_css", "statuses"}
        if set(self.collection) - allowed_collection:
            raise AppError("Неизвестное поле collection.")
        for key, value in self.collection.items():
            if key != "statuses" and not isinstance(value, str):
                raise AppError("Селекторы collection должны быть строками.")
        statuses = self.collection.get("statuses", {})
        if not isinstance(statuses, dict) or set(statuses) - {"completed", "not_started", "in_progress", "incomplete"}:
            raise AppError("Неизвестный статус collection.")
        if not all(isinstance(v, str) and v for v in statuses.values()):
            raise AppError("Маркеры статуса должны быть CSS-селекторами.")
        if self.execution:
            required = {"task_heading", "start", "editor", "submit", "accepted", "editor_kind"}
            optional = {"frame_css", "next_task", "timer", "attempt_limit_seconds", "rejected"}
            if not required <= set(self.execution) or set(self.execution) - required - optional:
                raise AppError("Профиль execution неполон или содержит неизвестные поля.")
            if self.execution["editor_kind"] not in {"textarea", "contenteditable", "monaco"}:
                raise AppError("Поддерживаются textarea, contenteditable и Monaco с EditContext.")
            for key in required - {"editor_kind"}:
                LocatorSpec.parse(self.execution[key])
            for key in ("next_task", "timer", "rejected"):
                if self.execution.get(key):
                    LocatorSpec.parse(self.execution[key])
            limit = self.execution.get("attempt_limit_seconds", 540)
            if type(limit) is not int or limit < 30:
                raise AppError("Лимит попытки должен быть целым числом секунд от 30.")
            if not isinstance(self.execution.get("frame_css", ""), str):
                raise AppError("frame_css должен быть строкой.")


def load_config(path: Path) -> Config:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise AppError("Конфигурация должна быть JSON-объектом.")
        config = Config(**raw)
        config.validate()
        return config
    except (OSError, ValueError, TypeError) as exc:
        raise AppError("Не удалось прочитать конфигурацию. Проверьте JSON и типы полей.") from exc

