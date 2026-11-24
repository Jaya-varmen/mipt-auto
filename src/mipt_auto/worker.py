from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
import threading

from PySide6.QtCore import QThread, Signal
from playwright.sync_api import Error as PlaywrightError

from .accounts import CredentialVault
from .browser import account_session, browser_engine, return_to_portal
from .config import load_config
from .control import Cancelled, RunControl, TimingPolicy
from .demo import demo_config, demo_server
from .diagnostics import capture_editor_structure
from .errors import AppError
from .login import prepare_login, wait_for_automatic_login
from .models import Solution, SolutionLibrary
from .portal import Portal
from .runner import run_sequence
from .storage import Store


def select_single_task(tasks, task_id, library, course_key):
    matches = [task for task in tasks if task.task_id == task_id]
    if len(matches) != 1:
        raise AppError("Выбранная задача не найдена в модуле.")
    task = matches[0]
    if task.status == "completed":
        raise AppError("Эта задача уже отмечена выполненной. Выберите невыполненную задачу для теста.")
    if library.find(course_key, task) is None:
        raise AppError("В базе нет решения для выбранных курса, ID и названия задачи.")
    return [task]


@dataclass
class RunOptions:
    accounts: list[dict]
    mode: str = "demo"
    task_id: str = ""
    config_path: Path = Path("config.local.json")
    solutions_path: Path = Path("solutions.local.json")
    data_dir: Path = Path("data")
    timing: TimingPolicy = field(default_factory=TimingPolicy)
    fast_demo: bool = False
    headless: bool = False


class RunnerWorker(QThread):
    event = Signal(str, object)
    needs_user = Signal(str, bool)
    completed = Signal(str, bool)

    def __init__(self, options: RunOptions, parent=None):
        super().__init__(parent)
        self.options = options
        self.control = RunControl(self.event.emit)
        self.resume_event = threading.Event()
        self.last_phase = "запуск"

    def report_event(self, kind, value):
        if kind == "phase":
            self.last_phase = value
        self.event.emit(kind, value)

    def stop(self):
        self.control.stop()
        self.resume_event.set()

    def resume(self):
        self.resume_event.set()

    def ask_user(self, message, page, *, inspection=False):
        self.control.check()
        self.resume_event.clear()
        self.needs_user.emit(message, inspection)
        while not self.resume_event.is_set():
            self.control.check()
            page.wait_for_timeout(100)
        self.control.check()
        self.event.emit("resumed", None)

    def run(self):
        opts = self.options
        try:
            opts.timing.validate()
            if not opts.accounts:
                raise AppError("Выберите хотя бы один аккаунт.")
            if opts.mode not in {"demo", "read", "execute", "single"}:
                raise AppError("Неизвестный режим запуска.")
            if opts.mode == "single" and (len(opts.accounts) != 1 or not opts.task_id):
                raise AppError("Для теста укажите один аккаунт и ID задачи.")
            with demo_server() if opts.mode == "demo" else nullcontext(None) as demo_url:
                config = demo_config(demo_url, "chrome") if demo_url else load_config(opts.config_path)
                if opts.mode in {"execute", "single"} and not config.execution:
                    raise AppError("Профиль отправки ещё не проверен. Навигация доступна в режиме просмотра.")
                if demo_url:
                    library = SolutionLibrary([
                        Solution("demo", "1.1.1", "Приветствие стенда", "print('Привет, стенд!')\n"),
                        Solution("demo", "1.1.3", "Эхо тестирующей системы", "text = input()\nprint(text)\n"),
                    ])
                else:
                    library = SolutionLibrary.from_file(opts.solutions_path) if opts.mode in {"execute", "single"} else None
                timing = (TimingPolicy(0, 0, post_input_min_seconds=0, post_input_max_seconds=0)
                          if demo_url and opts.fast_demo else opts.timing)
                with browser_engine(config, headless=opts.headless) as browser:
                    total_accepted = 0
                    total_completed = 0
                    total_missing = 0
                    for index, account in enumerate(opts.accounts):
                        self.control.check()
                        self.event.emit("account", {"index": index + 1, "total": len(opts.accounts), "email": account["email"]})
                        with account_session(browser, config) as (context, page):
                            if not demo_url:
                                message = prepare_login(context, page, account,
                                                        CredentialVault(opts.config_path.parent / "accounts.csv"), self.control)
                                ready = wait_for_automatic_login(context, page, self.control) if account.get("has_password") else None
                                if ready is None:
                                    self.ask_user(f"{account['email']}\n{message}\nПосле возврата на платформу проверьте аккаунт и нажмите «Продолжить».", page)
                                    page = return_to_portal(context, config)
                                else:
                                    page = ready
                                    self.event.emit("log", "Вход выполнен автоматически.")
                            portal = Portal(page, config, self.report_event, self.control)
                            portal.navigate(lambda text: self.ask_user(text.replace("Enter", "«Продолжить»"), page))
                            self.control.check()
                            tasks = portal.collect_tasks()
                            self.event.emit("tasks", [{"id": t.task_id, "title": t.title, "status": t.status} for t in tasks])
                            if opts.mode == "single":
                                tasks = select_single_task(tasks, opts.task_id, library, config.course_key)
                            with Store(opts.data_dir, account_id=account["id"]) as store:
                                store.save_tasks(config.course_key, tasks)
                                if opts.mode == "read":
                                    self.event.emit("log", f"Список прочитан: {len(tasks)} задач.")
                                    continue
                                try:
                                    result = run_sequence(portal, tasks, library, store, self.control, timing, self.report_event)
                                except (AppError, PlaywrightError) as exc:
                                    if not page.is_closed():
                                        diagnostic_note = ""
                                        try:
                                            diagnostic_path = (opts.data_dir / "editor-diagnostic.json").resolve()
                                            capture_editor_structure(page, config, diagnostic_path)
                                            diagnostic_note = f"\nДиагностика редактора: {diagnostic_path}"
                                        except (AppError, PlaywrightError, OSError):
                                            pass
                                        try:
                                            self.ask_user(
                                                f"{exc if isinstance(exc, AppError) else 'Ошибка браузера'}\n"
                                                "Попытка остановлена. Проверьте открытый браузер вручную; "
                                                f"затем нажмите «Закрыть браузер».{diagnostic_note}",
                                                page, inspection=True,
                                            )
                                        except PlaywrightError:
                                            pass
                                    raise
                                total_accepted += result.accepted
                                total_completed += result.completed
                                total_missing += result.missing
                                if result.stopped:
                                    reason = ("Результат проверки не подтверждён за время ожидания"
                                              if result.stopped == "timeout" else f"Результат: {result.stopped}")
                                    if not page.is_closed():
                                        diagnostic_note = ""
                                        try:
                                            diagnostic_path = (opts.data_dir / "editor-diagnostic.json").resolve()
                                            capture_editor_structure(page, config, diagnostic_path, portal.last_submission_trace)
                                            diagnostic_note = f" Диагностика: {diagnostic_path}."
                                        except (AppError, PlaywrightError, OSError):
                                            pass
                                        self.ask_user(
                                            f"{reason}. Очередь остановлена. "
                                            "Проверьте открытый браузер вручную; затем нажмите «Закрыть браузер»."
                                            f"{diagnostic_note}",
                                            page, inspection=True,
                                        )
                                    raise AppError(f"{reason}. Повторной отправки нет.")
                    self.completed.emit(f"Готово. Принято: {total_accepted}; уже выполнены: {total_completed}; без решения: {total_missing}.", True)
        except Cancelled:
            self.completed.emit("Остановлено оператором. Неопределённую отправку проверьте на сайте.", False)
        except AppError as exc:
            self.completed.emit(str(exc), False)
        except PlaywrightError:
            self.completed.emit(f"Ошибка браузера на этапе «{self.last_phase}». Проверьте открытый экран; повторной отправки нет.", False)
        except Exception as exc:
            self.completed.emit(f"Не удалось завершить запуск ({type(exc).__name__}). Проверьте установку и локальные файлы.", False)
