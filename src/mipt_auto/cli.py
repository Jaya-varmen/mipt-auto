import argparse
import json
import logging
from pathlib import Path
import sys

from playwright.sync_api import Error as PlaywrightError

from .browser import browser_session, portal_page
from .config import load_config
from .demo import demo_config, demo_server
from .diagnostics import capture_structure
from .errors import AppError
from .models import Result, Solution, load_solution
from .portal import Portal
from .storage import Store, configure_logging, private_json

STATUS_LABELS = {"completed": "выполнена", "not_started": "не начата", "incomplete": "не выполнена", "in_progress": "в процессе", "unknown": "неизвестно"}
RESULT_LABELS = {"accepted": "Решение принято", "rejected": "Решение отклонено", "timeout": "Время ожидания истекло; результат не подтверждён",
                 "unknown": "Неизвестный результат; проверьте страницу вручную", "cancelled": "Отменено оператором"}


def prompt(message: str) -> str:
    try:
        return input(message)
    except EOFError:
        raise AppError("Нужен интерактивный Terminal. Выполнение остановлено.") from None


def print_tasks(tasks):
    for task in tasks:
        print(f"  {task.task_id:<10} {STATUS_LABELS[task.status]:<12} {task.title}")


def confirm_one(task, solution) -> bool:
    print(f"\nВыбрана задача {task.task_id}: {task.title}\n")
    print("──────── Код для отправки ────────")
    print(solution.code)
    print("─────────────────────────────────")
    print("Будет начата одна попытка и выполнена одна отправка. Время попытки задаёт сайт.")
    return prompt(f"Введите ID {task.task_id}, чтобы начать, или Enter для отмены: ").strip() == task.task_id


def run_demo(args, store):
    with demo_server() as url:
        config = demo_config(url, args.channel)
        with browser_session(config, headless=args.headless) as (_, page):
            portal = Portal(page, config)
            portal.navigate(lambda _: None)
            tasks = portal.collect_tasks()
            print_tasks(tasks)
            store.save_tasks(config.course_key, tasks)
            capture_structure(page, config, args.data_dir / "structure.demo.json")
            task = next(t for t in tasks if t.task_id == "1.1.1")
            portal.open_task(task)
            solution = Solution("demo", "1.1.1", "Приветствие стенда", "print('Привет, стенд!')\n")
            print("\nЛокальная демонстрация: отправляется встроенный пример стенда.")
            result = portal.execute_one(task, solution, store, lambda *_: True)
            print(RESULT_LABELS[result.value])
            if args.keep_open and not args.headless:
                prompt("Enter — закрыть демонстрационный браузер: ")
            return 0 if result == Result.ACCEPTED else 2


def run_live(args, store):
    config = load_config(args.config)
    if args.command == "run-one" and not config.execution:
        raise AppError("Профиль выполнения ещё пуст. Сначала запустите inspect и настройте execution по тестовой странице.")
    with browser_session(config) as (context, _):
        print("В браузере выберите «УНИВЕРСИТЕТ 20.35» и войдите в тестовый аккаунт.")
        print("Логин, пароль и CAPTCHA вводятся вручную. Завершите возврат на edu.mipt.ru.")
        prompt("После возвращения на платформу нажмите Enter здесь: ")
        page = portal_page(context, config)
        portal = Portal(page, config)
        if args.command == "inspect":
            prompt("Откройте нужный экран курса или задачи. Enter — сохранить структуру элементов: ")
            page = portal_page(context, config)
            output = args.data_dir / "structure.json"
            structure = capture_structure(page, config, output)
            print(f"Структура сохранена: {output.resolve()} ({len(structure['elements'])} элементов).")
            print("Подсказки о редакторе:", json.dumps(structure["editor_hints"], ensure_ascii=False))
            prompt("Enter — закрыть сессию: ")
            return 0
        portal.navigate(prompt)
        tasks = portal.collect_tasks()
        store.save_tasks(config.course_key, tasks)
        private_json(args.data_dir / "tasks.json", [{"task_id": t.task_id, "title": t.title, "status": t.status} for t in tasks])
        print_tasks(tasks)
        if not config.collection.get("scope_css"):
            print("Контейнер модуля ещё не настроен: показаны видимые ссылки задач всей страницы.")
        if args.command == "navigate":
            prompt("Список сохранён. Enter — закрыть сессию: ")
            return 0
        matches = [task for task in tasks if task.task_id == args.task_id]
        if len(matches) != 1:
            raise AppError("Выбранная задача не найдена в открытом списке.")
        task = matches[0]
        solution = load_solution(args.solutions, config.course_key, task)
        portal.open_task(task)
        result = portal.execute_one(task, solution, store, confirm_one)
        print(RESULT_LABELS[result.value])
        prompt("Проверьте результат в браузере. Enter — закрыть сессию: ")
        return 0 if result in {Result.ACCEPTED, Result.CANCELLED} else 2


def build_parser():
    parser = argparse.ArgumentParser(description="MIPT Auto: локальный прототип Python + Playwright")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in [("demo", "Локальный сквозной пример без аккаунта"),
                            ("navigate", "Ручной вход, курс и чтение списка задач"),
                            ("inspect", "Структурная диагностика после ручного входа"),
                            ("run-one", "Одна выбранная тестовая задача"),
                            ("history", "Последние результаты из SQLite")]:
        child = commands.add_parser(name, help=help_text)
        child.add_argument("--data-dir", type=Path, default=Path("data"))
        if name in {"navigate", "inspect", "run-one"}:
            child.add_argument("--config", type=Path, default=Path("config.local.json"))
        if name == "demo":
            child.add_argument("--headless", action="store_true", help="Без видимого окна")
            child.add_argument("--keep-open", action="store_true", help="Оставить результат до Enter")
            child.add_argument("--channel", choices=["chrome"], default="", help="Использовать установленный Google Chrome")
        if name == "run-one":
            child.add_argument("--task-id", required=True)
            child.add_argument("--solutions", type=Path, default=Path("solutions.local.json"))
    return parser


def main(argv=None):
    args_in = sys.argv[1:] if argv is None else argv
    if not args_in or args_in == ["gui"]:
        from .gui import main as gui_main
        return gui_main()
    args = build_parser().parse_args(args_in)
    try:
        configure_logging(args.data_dir)
        with Store(args.data_dir) as store:
            if args.command == "history":
                for run in store.recent_runs():
                    print(f"{run['started_at']}  {run['course_key']} / {run['task_id']}  {run['state']}")
                return 0
            if args.command == "demo":
                return run_demo(args, store)
            return run_live(args, store)
    except (KeyboardInterrupt, EOFError):
        print("\nОстановлено. Автоматическая повторная отправка не выполняется.", file=sys.stderr)
        return 130
    except AppError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    except PlaywrightError:
        # Текст Playwright может включать URL с токенами или содержимое DOM.
        logging.getLogger("mipt_auto").error("browser_operation_failed")
        print("Ошибка браузера. Проверьте установку Chromium/Chrome и профиль элементов. "
              "Если отправка уже началась, проверьте результат вручную; повторного нажатия нет.", file=sys.stderr)
        return 1
    except OSError:
        print("Не удалось открыть локальный файл или сервер. Проверьте права и доступность папки данных.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

