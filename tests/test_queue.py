import random
import time

import pytest

from mipt_auto.control import Cancelled, RunControl, TimingPolicy
from mipt_auto.errors import AppError
from mipt_auto.models import Result, Solution, SolutionLibrary
from mipt_auto.portal import Portal
from mipt_auto.runner import run_sequence
from mipt_auto.storage import Store


def test_random_delay_range_and_budget():
    policy = TimingPolicy()
    rng = random.Random(31)
    values = [policy.choose(540, rng) for _ in range(20)]
    assert all(60 <= value <= 240 for value in values)
    assert len(set(values)) == 20
    post_values = [policy.choose_post_input(rng) for _ in range(20)]
    assert all(3 <= value <= 6 for value in post_values)
    assert len(set(post_values)) == 20
    with pytest.raises(AppError):
        policy.choose(260)


def test_insufficient_remaining_time_stops_wait():
    with pytest.raises(AppError, match="Недостаточно времени"):
        RunControl().wait_until(time.monotonic() + 60, None, remaining=lambda: 20)


def test_wall_deadline_accounts_for_system_sleep():
    events = []
    control = RunControl(lambda kind, value: events.append((kind, value)))
    control.wait_until(time.monotonic() + 60, None, wall_deadline=time.time() - 1, remaining=lambda: 500)
    assert events == [("countdown", 0)]


def library():
    return SolutionLibrary([
        Solution("demo", "1.1.1", "Приветствие стенда", "print('Привет, стенд!')"),
        Solution("demo", "1.1.3", "Эхо тестирующей системы", "text = input()\nprint(text)\n"),
    ])


def fast_timing():
    return TimingPolicy(0, 0, post_input_min_seconds=0, post_input_max_seconds=0)


@pytest.mark.browser
def test_waits_before_input_then_after_input_before_submit(page, config, tmp_path):
    observations = []
    def record(kind, _):
        if kind in {"delay", "post_delay"}:
            observations.append((kind, page.locator("#code").input_value()))
    portal = Portal(page, config, record)
    page.goto(config.base_url)
    portal.navigate(lambda _: None)
    task = portal.collect_tasks()[0]
    portal.open_task(task)
    code = "print('Привет, стенд!')"
    timing = TimingPolicy(0.1, 0.1, post_input_min_seconds=0.1, post_input_max_seconds=0.1)
    with Store(tmp_path / "data") as store:
        result = portal.execute_one(task, library().find("demo", task), store, lambda *_: True, timing=timing)
    assert observations == [("delay", ""), ("post_delay", code)]
    assert result == Result.ACCEPTED


@pytest.mark.browser
def test_sequence_skips_completed_and_moves_next(page, config, tmp_path):
    portal = Portal(page, config)
    page.goto(config.base_url)
    portal.navigate(lambda _: None)
    tasks = portal.collect_tasks()
    opened = []
    page.on("framenavigated", lambda frame: opened.append(frame.url) if frame == page.main_frame else None)
    with Store(tmp_path / "data", account_id="test-account") as store:
        result = run_sequence(portal, tasks, library(), store, RunControl(), fast_timing())
        assert (result.accepted, result.completed, result.missing, result.stopped) == (2, 1, 0, "")
        assert len(store.recent_runs()) == 2
        assert all(row["account_id"] == "test-account" and row["state"] == "accepted" for row in store.recent_runs())
        assert not any("/task/1.1.2" in url for url in opened)
        assert page.url.endswith("/task/1.1.3")
        repeated = run_sequence(portal, tasks, library(), store, RunControl(), fast_timing())
        assert (repeated.accepted, repeated.completed, repeated.missing, repeated.stopped) == (0, 3, 0, "")
        assert len(store.recent_runs()) == 2


def test_accepted_history_is_scoped_to_account(tmp_path):
    with Store(tmp_path / "data", account_id="first") as store:
        run_id = store.begin_run("course", "1.1.1")
        store.transition(run_id, "accepted")
        assert store.was_accepted("course", "1.1.1")
    with Store(tmp_path / "data", account_id="second") as store:
        assert not store.was_accepted("course", "1.1.1")


@pytest.mark.browser
def test_next_link_after_acceptance(page, config, tmp_path):
    portal = Portal(page, config)
    page.goto(config.base_url)
    portal.navigate(lambda _: None)
    tasks = [t for t in portal.collect_tasks() if t.status != "completed"]
    with Store(tmp_path / "data") as store:
        result = run_sequence(portal, tasks, library(), store, RunControl(), fast_timing())
        assert result.accepted == 2
        assert page.url.endswith("/task/1.1.3")


@pytest.mark.browser
def test_stop_during_delay_never_submits(page, config, tmp_path):
    portal = Portal(page, config)
    page.goto(config.base_url)
    portal.navigate(lambda _: None)
    task = portal.collect_tasks()[0]
    portal.open_task(task)
    control = RunControl()
    control.notify = lambda kind, _: control.stop() if kind == "countdown" else None
    with Store(tmp_path / "data") as store:
        with pytest.raises(Cancelled):
            portal.execute_one(task, library().find("demo", task), store, lambda *_: True,
                               timing=TimingPolicy(60, 240), control=control)
        assert page.evaluate("window.demoSubmissions") == 0
        assert store.recent_runs()[0]["state"] == "cancelled"


@pytest.mark.browser
def test_queue_skips_rejected_result_and_moves_to_next_task(page, config, tmp_path):
    portal = Portal(page, config)
    page.goto(config.base_url)
    portal.navigate(lambda _: None)
    tasks = portal.collect_tasks()
    bad_library = SolutionLibrary([Solution("demo", "1.1.1", "Приветствие стенда", "print('wrong')")])
    with Store(tmp_path / "data") as store:
        result = run_sequence(portal, tasks, bad_library, store, RunControl(), fast_timing(),
                              continue_on_rejected=True)
        assert result.stopped == ""
        assert (result.rejected, result.completed, result.missing) == (1, 1, 1)
        assert len(store.recent_runs()) == 1
        assert store.recent_runs()[0]["state"] == "rejected"


def test_queue_waits_for_operator_after_task_error_and_keeps_next_task():
    from mipt_auto.models import Task

    tasks = [Task("1.1.1", "Первое", "https://example.test/1"),
             Task("1.1.2", "Второе", "https://example.test/2")]
    solutions = SolutionLibrary([Solution("demo", t.task_id, t.title, "print(1)") for t in tasks])

    class PortalStub:
        config = type("Config", (), {"course_key": "demo"})()

        def __init__(self):
            self.opened = []

        def open_task(self, task):
            self.opened.append(task.task_id)

        def execute_one(self, task, *_args, **_kwargs):
            if task.task_id == "1.1.1":
                raise AppError("Редактор не готов")
            return Result.ACCEPTED

    class StoreStub:
        def was_accepted(self, *_args):
            return False

    portal = PortalStub()
    decisions = []

    def recover(task, exc):
        decisions.append((task.task_id, str(exc)))
        return True

    result = run_sequence(portal, tasks, solutions, StoreStub(), RunControl(), fast_timing(), on_error=recover)
    assert (result.accepted, result.skipped_errors, result.stopped) == (1, 1, "")
    assert portal.opened == ["1.1.1", "1.1.2"]
    assert decisions == [("1.1.1", "Редактор не готов")]


def test_unknown_result_requires_operator_before_skipping():
    from mipt_auto.models import Task

    tasks = [Task("1.1.1", "Первое", "https://example.test/1"),
             Task("1.1.2", "Второе", "https://example.test/2")]
    solutions = SolutionLibrary([Solution("demo", t.task_id, t.title, "print(1)") for t in tasks])

    class PortalStub:
        config = type("Config", (), {"course_key": "demo"})()

        def __init__(self):
            self.opened = []

        def open_task(self, task):
            self.opened.append(task.task_id)

        def execute_one(self, task, *_args, **_kwargs):
            return Result.UNKNOWN if task.task_id == "1.1.1" else Result.ACCEPTED

    class StoreStub:
        def was_accepted(self, *_args):
            return False

    portal = PortalStub()
    decisions = []
    result = run_sequence(
        portal, tasks, solutions, StoreStub(), RunControl(), fast_timing(),
        on_error=lambda task, state: decisions.append((task.task_id, state)) or True,
    )
    assert result.accepted == 1
    assert result.skipped_errors == 1
    assert result.stopped == ""
    assert decisions == [("1.1.1", Result.UNKNOWN)]
    assert portal.opened == ["1.1.1", "1.1.2"]
