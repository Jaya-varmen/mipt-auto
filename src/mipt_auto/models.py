from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import re
import unicodedata

from .errors import AppError

TASK_PATTERN = re.compile(r"^Задача\s+(\d+(?:\.\d+){2,})\.?\s+(.+)$", re.IGNORECASE)


class Result(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class Task:
    task_id: str
    title: str
    url: str
    status: str = "unknown"


@dataclass(frozen=True)
class Solution:
    course_key: str
    task_id: str
    title: str
    code: str


def normalize_title(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split()).replace("ё", "е")


def parse_task_title(text: str):
    match = TASK_PATTERN.fullmatch(" ".join(text.split()))
    return (match.group(1), match.group(2)) if match else None


def load_solution(path: Path, course_key: str, task: Task) -> Solution:
    solution = SolutionLibrary.from_file(path).find(course_key, task)
    if solution is None:
        raise AppError("Для этого курса и ID нужно ровно одно решение.")
    return solution


class SolutionLibrary:
    def __init__(self, entries: list[Solution]):
        self.entries = {}
        for entry in entries:
            if not all(isinstance(v, str) and v.strip() for v in (entry.course_key, entry.task_id, entry.title, entry.code)):
                raise AppError("В библиотеке есть пустые или некорректные поля.")
            key = (entry.course_key, entry.task_id)
            if key in self.entries:
                raise AppError("В библиотеке повторяются курс и ID задачи.")
            self.entries[key] = entry

    @classmethod
    def from_file(cls, path):
        return cls(_read_solutions(path))

    def find(self, course_key, task):
        solution = self.entries.get((course_key, task.task_id))
        if solution and normalize_title(solution.title) != normalize_title(task.title):
            raise AppError("Название задачи не совпадает с библиотекой решений.")
        return solution


def _read_solutions(path: Path) -> list[Solution]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError
        entries = [Solution(**item) for item in raw]
        if any(not all(isinstance(v, str) for v in (e.course_key, e.task_id, e.title, e.code)) for e in entries):
            raise ValueError
    except (OSError, ValueError, TypeError):
        raise AppError("Не удалось прочитать библиотеку решений.") from None
    return entries

