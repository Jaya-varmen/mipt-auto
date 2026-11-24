import json

import pytest

from mipt_auto.config import Config, LocatorSpec, load_config, origin
from mipt_auto.errors import AppError
from mipt_auto.models import Task, load_solution, parse_task_title
from mipt_auto.storage import Store
from mipt_auto.portal import Portal


def test_task_title():
    assert parse_task_title("Задача 1.1.5. Псевдоним участника") == ("1.1.5", "Псевдоним участника")
    assert parse_task_title("Теория по теме 1.1") is None


def test_solution_requires_id_course_and_title(tmp_path):
    path = tmp_path / "solutions.json"
    entry = {"course_key": "test", "task_id": "1.1.5", "title": "Псевдоним участника", "code": "print(1)"}
    path.write_text(json.dumps([entry]), encoding="utf-8")
    task = Task("1.1.5", "Псевдоним   участника", "https://edu.mipt.ru/task")
    assert load_solution(path, "test", task).code == "print(1)"
    with pytest.raises(AppError):
        load_solution(path, "other-course", task)
    with pytest.raises(AppError):
        load_solution(path, "test", Task("1.1.5", "Другое название", task.url))
    path.write_text(json.dumps([entry, entry]), encoding="utf-8")
    with pytest.raises(AppError):
        load_solution(path, "test", task)


@pytest.mark.parametrize("url", ["https://edu.mipt.ru.attacker.test", "https://user:secret@edu.mipt.ru", "file:///tmp/test"])
def test_rejects_wrong_origins(url):
    with pytest.raises(AppError):
        Config(base_url=url).validate()


def test_locator_rejects_ambiguous_spec():
    with pytest.raises(AppError):
        LocatorSpec.parse({"css": "button", "text": "Войти"})
    assert origin("https://edu.mipt.ru:443/path") == "https://edu.mipt.ru"


def test_example_has_no_fabricated_selectors():
    from pathlib import Path
    config = load_config(Path(__file__).parents[1] / "config/site.example.json")
    assert config.collection == {}
    assert config.execution == {}


def test_real_start_button_matches_both_observed_labels():
    class Page:
        url = "https://edu.mipt.ru/task"

        def get_by_role(self, role, name):
            return role, name

    result = Portal(Page(), Config()).execution_locator("start")
    assert result[0] == "button"
    assert result[1].search("Начать попытку")
    assert result[1].search("Начать новую попытку")


def test_navigation_pause_defaults_and_bounds():
    config = Config()
    assert (config.navigation_pause_min_seconds, config.navigation_pause_max_seconds) == (5, 10)
    config.navigation_pause_min_seconds = 11
    with pytest.raises(AppError):
        config.validate()


def test_navigation_pause_is_7_seconds_with_virtual_clock(monkeypatch):
    from mipt_auto import portal as module

    clock = [0.0]

    class Page:
        def wait_for_timeout(self, milliseconds):
            clock[0] += milliseconds / 1000

    class Random:
        def uniform(self, minimum, maximum):
            assert (minimum, maximum) == (5.0, 10.0)
            return 7.0

    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module.random, "SystemRandom", lambda: Random())
    events = []
    Portal(Page(), Config(), lambda kind, value: events.append((kind, value))).pause_before_transition()
    assert clock[0] >= 7.0
    assert events == [("transition_delay", 7.0)]


def test_storage_snapshots_are_separate_and_exclude_urls(tmp_path):
    with Store(tmp_path / "data") as store:
        task = Task("1.1.1", "Пример", "https://edu.mipt.ru/?token=secret")
        first = store.save_tasks("course", [task])
        second = store.save_tasks("course", [task])
        assert first != second
        rows = store.db.execute("SELECT tasks_json FROM snapshots").fetchall()
        assert len(rows) == 2 and all("secret" not in row[0] for row in rows)
        run_id = store.begin_run("course", task.task_id)
        store.transition(run_id, "unknown")
        assert store.recent_runs()[0]["state"] == "unknown"

