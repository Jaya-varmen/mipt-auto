import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from mipt_auto.control import TimingPolicy
from mipt_auto.gui import MainWindow, STYLE
from mipt_auto.storage import Store
from mipt_auto.worker import RunOptions, RunnerWorker
from mipt_auto.qt_runtime import prepare_qt_plugins


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    prepare_qt_plugins(tmp_path_factory.mktemp("qt-plugins"))
    instance = QApplication.instance() or QApplication([])
    instance.setStyleSheet(STYLE)
    yield instance


def test_gui_selects_only_checked_accounts(app, tmp_path):
    with Store(tmp_path / "data") as store:
        first = store.upsert_account("one@example.test")
        second = store.upsert_account("two@example.test")
    window = MainWindow(project_dir=tmp_path)
    try:
        assert window.mode.currentData() == "execute"
        assert window.selected_course() == "10-11"
        window.course_buttons["8-9"].click()
        assert window.config_path == tmp_path / "config" / "edu-8-9.json"
        assert not window.start_button.isEnabled()
        window.accounts_table.item(1, 0).setCheckState(Qt.CheckState.Checked)
        assert [a["id"] for a in window.selected_accounts()] == [second]
        assert window.start_button.isEnabled()
        assert window.delay_min.value() == 1 and window.delay_max.value() == 4
        window.delay_min.setValue(4)
        window.delay_max.setValue(2)
        assert window.delay_min.value() == 2
        window.mode.setCurrentIndex(window.mode.findData("single"))
        assert window.task_id_input.isVisible() is False  # Окно не показано в offscreen тесте.
        assert not window.task_id_input.isHidden()
    finally:
        window.close()


def test_demo_account_does_not_pollute_real_database(app, tmp_path):
    window = MainWindow(project_dir=tmp_path)
    try:
        window.mode.setCurrentIndex(window.mode.findData("demo"))
        assert window.selected_accounts()[0]["email"] == "demo@example.test"
        assert window.store.accounts() == []
        window.mode.setCurrentIndex(window.mode.findData("read"))
        assert window.selected_accounts() == []
        assert not window.start_button.isEnabled()
    finally:
        window.close()


def test_gui_reads_accounts_and_password_flags_from_csv(app, tmp_path):
    (tmp_path / "accounts.csv").write_text("email,password\nuser@example.test,secret\n", encoding="utf-8")
    window = MainWindow(project_dir=tmp_path)
    try:
        assert window.store.accounts()[0]["has_password"] == 1
        assert window.credential_vault().get("user@example.test") == "secret"
        (tmp_path / "accounts.csv").write_text("email,password\nuser@example.test,\n", encoding="utf-8")
        window._sync_accounts_csv()
        assert window.store.accounts()[0]["has_password"] == 0
    finally:
        window.close()


def test_gui_keeps_inspection_action_explicit(app, tmp_path):
    window = MainWindow(project_dir=tmp_path)
    try:
        window.request_user("Проверьте попытку", inspection=True)
        assert window.continue_button.text() == "Закрыть браузер"
        assert window.continue_button.isEnabled()
        assert window.status.text() == "Ожидаю проверки браузера"
        window.run_completed("Остановлено", False)
        assert window.continue_button.text() == "Продолжить после входа"
    finally:
        window.close()


@pytest.mark.browser
def test_worker_runs_two_isolated_accounts_and_keeps_gui_responsive(app, tmp_path):
    accounts = [{"id": "a", "email": "one@example.test"}, {"id": "b", "email": "two@example.test"}]
    options = RunOptions(accounts=accounts, mode="demo", data_dir=tmp_path / "data", headless=True, fast_demo=True,
                         timing=TimingPolicy(60, 240))
    worker = RunnerWorker(options)
    results, events = [], []
    worker.completed.connect(lambda message, ok: results.append((message, ok)))
    worker.event.connect(lambda name, value: events.append((name, value)))
    worker.start()
    deadline = time.monotonic() + 45
    iterations = 0
    while worker.isRunning() and time.monotonic() < deadline:
        app.processEvents()
        iterations += 1
        time.sleep(0.01)
    if worker.isRunning():
        worker.stop()
    assert worker.wait(5000)
    app.processEvents()
    assert results and results[-1][1], results
    assert iterations > 1
    assert [event[1]["email"] for event in events if event[0] == "account"] == ["one@example.test", "two@example.test"]
    with Store(tmp_path / "data") as store:
        runs = store.recent_runs()
        assert len(runs) == 4
        assert {run["account_id"] for run in runs} == {"a", "b"}
        assert all(run["state"] == "accepted" for run in runs)
