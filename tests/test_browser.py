import json

import pytest

from mipt_auto.diagnostics import capture_structure
from mipt_auto.errors import AppError
from mipt_auto.models import Result, Solution, Task
from mipt_auto.portal import Portal
from mipt_auto.storage import Store

pytestmark = pytest.mark.browser


def prepare(page, config):
    page.goto(config.base_url)
    portal = Portal(page, config)
    portal.navigate(lambda _: None)
    tasks = portal.collect_tasks()
    return portal, tasks


def test_navigation_and_statuses(page, config):
    portal, tasks = prepare(page, config)
    assert [(t.task_id, t.status) for t in tasks] == [("1.1.1", "not_started"), ("1.1.2", "completed"), ("1.1.3", "not_started")]
    portal.navigate(lambda _: None)
    assert len(portal.collect_tasks()) == 3
    config.collection["statuses"] = {}
    assert all(t.status == "unknown" for t in portal.collect_tasks())


@pytest.mark.parametrize("mode, code, expected", [
    ("", "print('Привет, стенд!')\n", Result.ACCEPTED),
    ("", "print('Неверно')", Result.REJECTED),
    ("timeout", "print('Привет, стенд!')", Result.TIMEOUT),
    ("ambiguous", "print('Привет, стенд!')", Result.UNKNOWN),
    ("unexpected", "print('Привет, стенд!')", Result.UNKNOWN),
])
def test_execution_only_once(page, config, tmp_path, mode, code, expected):
    portal, tasks = prepare(page, config)
    task = tasks[0]
    page.goto(task.url + (f"?result={mode}" if mode else ""))
    with Store(tmp_path / "data") as store:
        result = portal.execute_one(task, Solution("demo", task.task_id, task.title, code), store, lambda *_: True)
        assert result == expected
        assert page.evaluate("window.demoSubmissions") == 1
        assert portal.last_submission_trace["click_event_seen"] is True
        assert store.recent_runs()[0]["state"] == expected.value


def test_accepted_result_in_shadow_component(page, config):
    page.goto(config.base_url)
    page.set_content('''<h2 class="title_course">Задача 1.1.5. Псевдоним участника</h2>
        <web-component-trenajer></web-component-trenajer>
        <script>
          document.querySelector('web-component-trenajer').attachShadow({mode:'open'}).innerHTML =
            '<div role="status">Решение принято</div>';
        </script>''')
    config.execution["task_heading"] = {"css": "h2.title_course"}
    task = Task("1.1.5", "Псевдоним участника", page.url, "not_started")
    assert Portal(page, config).wait_result(task) == Result.ACCEPTED


def test_cancel_does_not_start_attempt(page, config, tmp_path):
    portal, tasks = prepare(page, config)
    task = tasks[0]
    portal.open_task(task)
    with Store(tmp_path / "data") as store:
        result = portal.execute_one(task, Solution("demo", task.task_id, task.title, "print(1)"), store, lambda *_: False)
        assert result == Result.CANCELLED
        assert page.get_by_role("button", name="Начать попытку").is_visible()
        assert page.evaluate("window.demoSubmissions") == 0


def test_resumes_active_attempt_without_starting_new_one(page, config, tmp_path):
    portal, tasks = prepare(page, config)
    task = tasks[0]
    portal.open_task(task)
    page.get_by_role("button", name="Начать попытку").click()
    assert page.get_by_role("button", name="Начать попытку").is_hidden()
    with Store(tmp_path / "data") as store:
        result = portal.execute_one(
            task, Solution("demo", task.task_id, task.title, "print('Привет, стенд!')"),
            store, lambda *_: True,
        )
        assert result == Result.ACCEPTED
        assert page.evaluate("window.demoSubmissions") == 1
        assert store.recent_runs()[0]["state"] == "accepted"


def test_attempt_state_ignores_transient_editor_before_start_button(page, config):
    page.goto(config.base_url)
    page.set_content('''<textarea id="code"></textarea>
        <button id="start" style="display:none">Начать попытку</button>
        <script>
          setTimeout(() => {
            document.querySelector('#code').style.display = 'none';
            document.querySelector('#start').style.display = 'block';
          }, 400);
        </script>''')
    assert Portal(page, config).attempt_state(timeout_seconds=4) == "ready"


def test_wrong_task_and_stale_result_stop_submission(page, config, tmp_path):
    portal, tasks = prepare(page, config)
    portal.open_task(tasks[1])
    with Store(tmp_path / "data") as store:
        with pytest.raises(AppError, match="не совпадает"):
            portal.execute_one(tasks[0], Solution("demo", tasks[0].task_id, tasks[0].title, "print(1)"), store, lambda *_: True)
        with pytest.raises(AppError, match="уже есть результат"):
            portal.execute_one(tasks[1], Solution("demo", tasks[1].task_id, tasks[1].title, "print(1)"), store, lambda *_: True)
        assert page.evaluate("window.demoSubmissions") == 0


def test_duplicate_task_id_is_not_silently_selected(page, config):
    portal, _ = prepare(page, config)
    page.locator("#tasks").evaluate("e => e.insertAdjacentHTML('beforeend', '<div class=task><a href=/task/other>Задача 1.1.1. Другая задача</a></div>')")
    with pytest.raises(AppError, match="разных задач"):
        portal.collect_tasks()


def test_diagnostics_excludes_secrets(page, config, tmp_path):
    page.goto(config.base_url)
    page.evaluate("""() => {
      document.body.insertAdjacentHTML('beforeend', '<input value="secret-input"><input type=password value="secret-password"><a href="/?token=secret-token">secret-name@example.test</a>');
      localStorage.setItem('token', 'secret-storage');
      document.cookie = 'token=secret-cookie';
    }""")
    report = capture_structure(page, config, tmp_path / "data" / "structure.json")
    text = json.dumps(report)
    assert "secret-" not in text
    assert "password" not in text
    assert report["elements"]


def test_contexts_do_not_share_auth(browser, demo_url):
    first, second = browser.new_context(), browser.new_context()
    try:
        one, two = first.new_page(), second.new_page()
        one.goto(demo_url)
        one.evaluate("() => { localStorage.setItem('auth', 'example'); document.cookie='auth=example'; }")
        two.goto(demo_url)
        assert two.evaluate("localStorage.getItem('auth')") is None
        assert two.evaluate("document.cookie") == ""
    finally:
        first.close()
        second.close()
