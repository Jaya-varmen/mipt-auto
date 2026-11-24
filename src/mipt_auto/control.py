from dataclasses import dataclass
import random
import threading
import time

from .errors import AppError


class Cancelled(AppError):
    def __init__(self):
        super().__init__("Остановлено оператором.")


class RunControl:
    """Поток GUI может остановить рабочий поток без обращения к Playwright."""
    def __init__(self, notify=lambda *_: None):
        self.stop_event = threading.Event()
        self.notify = notify

    def stop(self):
        self.stop_event.set()

    def check(self):
        if self.stop_event.is_set():
            raise Cancelled()

    def wait_until(self, deadline, page, *, remaining=None, reserve_seconds=30, wall_deadline=None):
        last_second = None
        while True:
            self.check()
            seconds = deadline - time.monotonic()
            if wall_deadline is not None:
                # На macOS монотонные часы могут не учитывать сон компьютера.
                seconds = min(seconds, wall_deadline - time.time())
            seconds = max(0, seconds)
            shown = int(seconds + 0.999)
            if shown != last_second:
                if remaining is not None and remaining() < seconds + reserve_seconds:
                    raise AppError("Недостаточно времени попытки для выбранной задержки. Код не отправлен.")
                self.notify("countdown", shown)
                last_second = shown
            if seconds <= 0:
                return
            page.wait_for_timeout(min(200, seconds * 1000))


@dataclass(frozen=True)
class TimingPolicy:
    min_seconds: float = 60
    max_seconds: float = 240
    reserve_seconds: float = 30
    post_input_min_seconds: float = 3
    post_input_max_seconds: float = 6

    def validate(self):
        if (not 0 <= self.min_seconds <= self.max_seconds <= 240
                or not 0 <= self.post_input_min_seconds <= self.post_input_max_seconds <= 6
                or self.reserve_seconds < 0):
            raise AppError("Начальная задержка должна быть 0–240 секунд, после ввода — 0–6 секунд.")

    def choose(self, attempt_limit: float, rng=None) -> float:
        self.validate()
        if self.max_seconds + self.post_input_max_seconds + self.reserve_seconds >= attempt_limit:
            raise AppError("Диапазон задержки не помещается в лимит попытки с запасом на проверку.")
        return (rng or random.SystemRandom()).uniform(self.min_seconds, self.max_seconds)

    def choose_post_input(self, rng=None) -> float:
        self.validate()
        return (rng or random.SystemRandom()).uniform(self.post_input_min_seconds, self.post_input_max_seconds)
