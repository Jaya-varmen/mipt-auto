from dataclasses import dataclass

from .control import RunControl, TimingPolicy
from .models import Result


@dataclass
class QueueResult:
    accepted: int = 0
    completed: int = 0
    missing: int = 0
    stopped: str = ""


def run_sequence(portal, tasks, library, store, control: RunControl,
                 timing: TimingPolicy, notify=lambda *_: None) -> QueueResult:
    """Порядок соответствует курсу. Повторных отправок после неопределённого итога нет."""
    result = QueueResult()
    previous_accepted = False
    for index, task in enumerate(tasks):
        control.check()
        notify("task", {"index": index + 1, "total": len(tasks), "id": task.task_id, "title": task.title})
        if task.status == "completed" or store.was_accepted(portal.config.course_key, task.task_id):
            result.completed += 1
            notify("task_state", "Уже выполнена — пропуск")
            previous_accepted = False
            continue
        solution = library.find(portal.config.course_key, task)
        if solution is None:
            result.missing += 1
            notify("task_state", "Нет решения в базе — пропуск")
            previous_accepted = False
            continue
        notify("task_state", "Открываю задачу")
        if previous_accepted:
            portal.next_task(task)
        else:
            portal.open_task(task)
        control.check()
        state = portal.execute_one(task, solution, store, lambda *_: True, timing=timing, control=control)
        notify("task_state", state.value)
        if state != Result.ACCEPTED:
            result.stopped = state.value
            return result
        result.accepted += 1
        previous_accepted = True
    return result
