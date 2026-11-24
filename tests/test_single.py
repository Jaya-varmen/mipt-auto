import pytest

from mipt_auto.errors import AppError
from mipt_auto.models import Solution, SolutionLibrary, Task
from mipt_auto.worker import select_single_task


def test_single_mode_limits_queue_to_one_exact_task():
    tasks = [Task("1.1.3", "Эхо тестирующей системы", "/one", "completed"),
             Task("1.1.5", "Псевдоним участника", "/two", "incomplete")]
    library = SolutionLibrary([Solution("python-10-11", "1.1.5", "Псевдоним участника", "print(1)")])
    assert select_single_task(tasks, "1.1.5", library, "python-10-11") == [tasks[1]]
    with pytest.raises(AppError, match="уже отмечена выполненной"):
        select_single_task(tasks, "1.1.3", library, "python-10-11")
    with pytest.raises(AppError, match="не найдена"):
        select_single_task(tasks, "1.1.7", library, "python-10-11")
