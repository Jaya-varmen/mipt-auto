import time
import logging
import re
import random
from urllib.parse import urljoin

from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout

from .config import Config, LocatorSpec, origin
from .errors import AppError
from .control import Cancelled, RunControl, TimingPolicy
from .models import Result, Solution, Task, normalize_title, parse_task_title


def one_visible(locator, description: str, timeout_ms=None):
    locator = locator.filter(visible=True)
    try:
        locator.first.wait_for(state="visible", timeout=timeout_ms)
    except PlaywrightTimeout:
        raise AppError(f"Не найден элемент «{description}». Проверьте открытый экран задачи.") from None
    if locator.count() != 1 or not locator.is_visible():
        raise AppError(f"Элемент «{description}» не единственный. Уточните профиль сайта.")
    return locator


def is_visible(locator) -> bool:
    locator = locator.filter(visible=True)
    count = locator.count()
    if count > 1:
        raise AppError("Маркер состояния неоднозначен. Уточните профиль сайта.")
    return count == 1 and locator.is_visible()


class Portal:
    def __init__(self, page, config: Config, notify=lambda *_: None, control: RunControl | None = None):
        self.page = page
        self.config = config
        self.notify = notify
        self.control = control
        self.last_submission_trace = None

    def pause_before_transition(self):
        minimum = self.config.navigation_pause_min_seconds
        maximum = self.config.navigation_pause_max_seconds
        seconds = random.SystemRandom().uniform(minimum, maximum)
        if seconds <= 0:
            return
        self.notify("transition_delay", round(seconds, 1))
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.control:
                self.control.check()
            self.page.wait_for_timeout(min(200, max(1, (deadline - time.monotonic()) * 1000)))
        if self.control:
            self.control.check()

    def go(self, url: str):
        if self.page.url.rstrip("/") != url.rstrip("/"):
            self.pause_before_transition()
        started = time.monotonic()
        self.page.goto(url, wait_until="commit")
        self.ensure_origin()
        elapsed = time.monotonic() - started
        logging.getLogger("mipt_auto").info("navigation_seconds=%.3f", elapsed)
        self.notify("navigation", round(elapsed, 2))

    def ensure_origin(self):
        if origin(self.page.url) != self.config.portal_origin:
            raise AppError("Действие остановлено: открыта страница другого сайта.")

    def click_known_text(self, text: str) -> bool:
        """Названия взяты из PDF, но существование каждого элемента проверяется."""
        self.ensure_origin()
        expression = re.compile(r"^" + r"\s+".join(re.escape(word) for word in text.split()) + r"$", re.I)
        locator = self.page.get_by_text(expression).filter(visible=True)
        try:
            self.pause_before_transition()
            one_visible(locator, text).click()
        except (AppError, PlaywrightError):
            return False
        return True

    def navigate(self, manual_step):
        self.ensure_origin()
        if self.config.course_url:
            self.go(self.config.course_url)
        else:
            for label in ("МОЁ ОБУЧЕНИЕ", self.config.course_title, "ПЕРЕЙТИ К КУРСУ"):
                if not self.click_known_text(label):
                    manual_step(f"Откройте шаг «{label}» вручную в браузере, затем нажмите Enter: ")
                    self.ensure_origin()
        module = self.page.get_by_text(self.config.module_title, exact=True)
        try:
            one_visible(module, "модуль")
            # Повторное нажатие не должно закрывать уже раскрытый модуль.
            expanded = module.get_attribute("aria-expanded")
            scope_css = self.config.collection.get("scope_css")
            scope_open = bool(scope_css and self.page.locator(scope_css).count() == 1
                              and self.page.locator(scope_css).is_visible())
            if expanded != "true" and not scope_open:
                module.click()
        except (AppError, PlaywrightError):
            manual_step("Раскройте нужный модуль вручную, затем нажмите Enter: ")
        if not self.config.collection.get("scope_css"):
            manual_step("Проверьте, что открыт нужный курс и модуль. Enter — прочитать список задач: ")
        self.ensure_origin()

    def collect_tasks(self) -> list[Task]:
        started = time.monotonic()
        self.ensure_origin()
        settings = self.config.collection
        scope_css = settings.get("scope_css")
        scope = one_visible(self.page.locator(scope_css), "контейнер модуля") if scope_css else self.page.locator("body")
        rows = scope.locator(settings.get("row_css") or "a")
        rows.filter(visible=True).first.wait_for(state="visible")
        # Один запрос вместо нескольких RPC на каждую из сотен строк.
        records = rows.evaluate_all("""(rows, cfg) => {
          const visible = e => !!(e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
          return rows.filter(visible).map(row => {
            const titles = cfg.title_css ? [...row.querySelectorAll(cfg.title_css)] : [row];
            const links = cfg.link_css ? [...row.querySelectorAll(cfg.link_css)] : [row];
            if (titles.length !== 1 || links.length !== 1) return {invalid:true};
            const statuses = Object.entries(cfg.statuses || {}).filter(([s, css]) =>
              [...row.querySelectorAll(css)].some(visible)).map(([s]) => s);
            return {text:titles[0].innerText, href:links[0].getAttribute('href'),
              status:statuses.length === 1 ? statuses[0] : 'unknown'};
          });
        }""", settings)
        tasks = {}
        for record in records:
            if record.get("invalid"):
                raise AppError("У строки задачи должно быть одно название и одна ссылка.")
            parsed = parse_task_title(record["text"])
            if not parsed:
                continue
            href = record["href"]
            if not href or href.startswith("#"):
                continue
            url = urljoin(self.page.url, href)
            try:
                if origin(url) != self.config.portal_origin:
                    continue
            except AppError:
                continue
            state = record["status"]
            task_id, title = parsed
            task = Task(task_id, title, url, state)
            if task_id in tasks and tasks[task_id] != task:
                raise AppError("Один ID найден у разных задач. Уточните контейнер модуля.")
            tasks[task_id] = task
        if not tasks:
            raise AppError("Задачи не найдены. Раскройте модуль или настройте collection по диагностике.")
        elapsed = time.monotonic() - started
        logging.getLogger("mipt_auto").info("tasks_read=%d collection_seconds=%.3f", len(tasks), elapsed)
        self.notify("collected", {"count": len(tasks), "seconds": round(elapsed, 3)})
        return list(tasks.values())

    def open_task(self, task: Task):
        self.ensure_origin()
        if origin(task.url) != self.config.portal_origin:
            raise AppError("Задача относится к другому сайту.")
        self.go(task.url)
        self.ensure_origin()

    def next_task(self, task: Task):
        """Кнопка следующей задачи используется только с проверенным адресом."""
        spec = self.config.execution.get("next_task")
        if spec:
            link = LocatorSpec.parse(spec).locate(self.page).filter(visible=True)
            if link.count() == 1:
                href = link.get_attribute("href")
                if href and urljoin(self.page.url, href).rstrip("/") == task.url.rstrip("/"):
                    self.pause_before_transition()
                    link.click()
                    self.verify_task(task)
                    return
        self.open_task(task)

    def fill_editor(self, code: str):
        kind = self.config.execution["editor_kind"]
        if kind == "monaco":
            # Поле ввода Monaco может жить вне корня редактора. Не ждём его:
            # кликаем по видимой строке кода и проверяем содержимое после ввода.
            editor = one_visible(self.execution_locator("editor"), "редактор Monaco", timeout_ms=30000)
            activation_deadline = time.monotonic() + 15
            self.notify("phase", "Ввожу код в видимую область Monaco")
            while True:
                if self.control:
                    self.control.check()
                try:
                    lines = editor.locator(".view-lines").filter(visible=True)
                    if lines.count() == 1:
                        lines.click(position={"x": 40, "y": 18}, timeout=1000)
                    else:
                        editor.click(position={"x": 120, "y": 24}, timeout=1000)
                    self.page.keyboard.press("ControlOrMeta+A")
                    self.page.keyboard.insert_text(code)
                    # Сверяем модель Monaco и строки на экране. Сам код в лог не пишем.
                    for _ in range(10):
                        matches = editor.evaluate("""(root, expected) => {
                            const normalize = s => s.replace(/\\r\\n/g, '\\n').replace(/\\u00a0/g, ' ').replace(/\\n+$/, '');
                            const values = [...root.querySelectorAll('[role="textbox"]')]
                                .map(e => e.editContext?.text).filter(v => typeof v === 'string');
                            const models = globalThis.monaco?.editor?.getModels?.() || [];
                            if (models.length === 1) values.push(models[0].getValue());
                            const lines = [...root.querySelectorAll('.view-lines .view-line')];
                            if (lines.length) values.push(lines.map(e => e.textContent).join('\\n'));
                            return values.some(v => normalize(v) === normalize(expected));
                        }""", code, timeout=1000)
                        if matches:
                            return
                        self.page.wait_for_timeout(200)
                except PlaywrightError:
                    # Перерисовка во время клика или проверки допускает повтор.
                    pass
                if time.monotonic() >= activation_deadline:
                    raise AppError("Не удалось подтвердить код в Monaco. Код не отправлен.")
                self.page.wait_for_timeout(250)
        else:
            editor = one_visible(self.execution_locator("editor"), "редактор")
            actual = editor.evaluate("e => ({tag:e.tagName.toLowerCase(), editable:e.isContentEditable})")
            if (kind == "textarea" and actual["tag"] != "textarea") or (kind == "contenteditable" and not actual["editable"]):
                raise AppError("Редактор не соответствует профилю. Отправка остановлена.")
            editor.fill(code)
            inserted = editor.input_value() if kind == "textarea" else editor.inner_text()
            matches = inserted.replace("\r\n", "\n").rstrip("\n") == code.replace("\r\n", "\n").rstrip("\n")
        if kind != "monaco" and not matches:
            raise AppError("Не удалось подтвердить полный текст в редакторе. Код не отправлен.")

    def execution_scope(self):
        frame_css = self.config.execution.get("frame_css")
        if frame_css:
            one_visible(self.page.locator(frame_css), "iframe редактора")
            return self.page.frame_locator(frame_css)
        return self.page

    def execution_locator(self, key: str):
        if key == "start" and self.config.portal_origin == "https://edu.mipt.ru":
            return self.execution_scope().get_by_role(
                "button", name=re.compile(r"начать\s+(?:новую\s+)?попытку", re.I)
            )
        if key == "rejected" and not self.config.execution.get(key):
            # Неизвестный маркер отказа не угадываем: итог останется неподтверждённым.
            return self.execution_scope().locator(":not(*)")
        return LocatorSpec.parse(self.config.execution[key]).locate(self.execution_scope())

    def submit_button(self):
        """На платформе видимый текст кнопки может отличаться от её aria-name."""
        scope = self.execution_scope()
        host = scope.locator("web-component-trenajer")
        targets = []
        if host.count() == 1:
            targets.extend((
                host.locator('button:has-text("Проверить код"), [role="button"]:has-text("Проверить код")'),
                host.get_by_role("button", name=re.compile(r"проверить\s+код", re.I)),
                host.get_by_text("Проверить код", exact=True),
            ))
        targets.append(self.execution_locator("submit"))
        targets.append(scope.locator('button:has-text("Проверить код"), [role="button"]:has-text("Проверить код")'))
        deadline = time.monotonic() + 10
        while True:
            if self.control:
                self.control.check()
            for target in targets:
                visible = target.filter(visible=True)
                count = visible.count()
                if count == 1:
                    return visible
                if count > 1:
                    raise AppError("Найдено несколько кнопок «Проверить код». Отправка остановлена.")
            if time.monotonic() >= deadline:
                raise AppError("Не найдена видимая кнопка «Проверить код». Код не отправлен.")
            self.page.wait_for_timeout(200)

    def accepted_marker(self):
        configured = self.execution_locator("accepted").filter(visible=True)
        if is_visible(configured):
            return configured
        host = self.execution_scope().locator("web-component-trenajer")
        scope = host if host.count() == 1 else self.execution_scope()
        fallback = scope.get_by_text("Решение принято", exact=True).filter(visible=True)
        return fallback if is_visible(fallback) else None

    def verify_task(self, task: Task):
        self.ensure_origin()
        heading = one_visible(self.execution_locator("task_heading"), "название открытой задачи", timeout_ms=30000)
        parsed = parse_task_title(heading.inner_text())
        if not parsed or parsed[0] != task.task_id or normalize_title(parsed[1]) != normalize_title(task.title):
            raise AppError("Открытая задача не совпадает с выбранным решением.")

    def attempt_state(self, timeout_seconds=15):
        """Ждём устойчивого состояния после гидратации страницы задачи."""
        deadline = time.monotonic() + timeout_seconds
        candidate = None
        candidate_since = None
        while True:
            if self.control:
                self.control.check()
            start = self.execution_locator("start")
            editor = self.execution_locator("editor")
            start_visible = is_visible(start)
            editor_visible = is_visible(editor)
            current = "ready" if start_visible else "active" if editor_visible else None
            now = time.monotonic()
            if current != candidate:
                candidate = current
                candidate_since = now
            stable_for = 0.5 if candidate == "ready" else 2.0
            if candidate and now - candidate_since >= stable_for:
                return candidate
            if time.monotonic() >= deadline:
                raise AppError("Не удалось определить устойчивое состояние попытки. Проверьте экран задачи.")
            self.page.wait_for_timeout(150)

    def execute_one(self, task: Task, solution: Solution, store, confirm,
                    *, timing: TimingPolicy | None = None, control: RunControl | None = None) -> Result:
        control = control or RunControl()
        control.check()
        self.config.validate()
        if not self.config.execution:
            raise AppError("Сначала настройте execution по реальной тестовой странице. Селекторы ещё неизвестны.")
        if (solution.course_key != self.config.course_key or solution.task_id != task.task_id
                or normalize_title(solution.title) != normalize_title(task.title) or not solution.code.strip()):
            raise AppError("Решение не соответствует курсу и задаче.")
        self.notify("phase", "Проверяю заголовок задачи")
        self.verify_task(task)
        if self.accepted_marker() or is_visible(self.execution_locator("rejected")):
            raise AppError("На странице уже есть результат. Автоматическая повторная попытка не начнётся.")
        self.notify("phase", "Определяю состояние попытки")
        initial_state = self.attempt_state()
        attempt_limit = self.config.execution.get("attempt_limit_seconds", 540)
        run_id = store.begin_run(self.config.course_key, task.task_id)
        submitted = False
        try:
            if not confirm(task, solution):
                store.transition(run_id, Result.CANCELLED.value)
                return Result.CANCELLED
            # Проверяем страницу ещё раз после ожидания решения человека.
            self.verify_task(task)
            control.check()
            state = self.attempt_state(timeout_seconds=3)
            if initial_state == "active" and state != "active":
                raise AppError("Активная попытка завершилась до ввода кода. Новая попытка не начата.")
            started = time.monotonic()
            started_wall = time.time()
            def remaining():
                elapsed = max(time.monotonic() - started, time.time() - started_wall)
                fallback = attempt_limit - elapsed
                if not self.config.execution.get("timer"):
                    return fallback
                timer = one_visible(self.execution_locator("timer"), "таймер попытки").inner_text().strip()
                match = re.fullmatch(r"(\d{1,2}):(\d{2})", timer)
                if not match or int(match[2]) > 59:
                    raise AppError("Не удалось прочитать оставшееся время попытки. Код не отправлен.")
                return min(fallback, int(match[1]) * 60 + int(match[2]))

            if state == "ready":
                delay = timing.choose(attempt_limit) if timing else 0
                store.transition(run_id, "starting")
                self.notify("phase", "Нажимаю начало попытки")
                one_visible(self.execution_locator("start"), "Начать попытку").click()
                if timing:
                    self.notify("delay", round(delay, 1))
                    store.transition(run_id, "delay")
                    delay_deadline = time.monotonic() + delay
                    delay_wall_deadline = time.time() + delay
                    control.wait_until(delay_deadline, self.page, remaining=remaining,
                                       reserve_seconds=timing.reserve_seconds + timing.post_input_max_seconds + 45,
                                       wall_deadline=delay_wall_deadline)
            else:
                store.transition(run_id, "resuming")
                self.notify("phase", "Продолжаю уже открытую попытку")
            control.check()
            self.notify("phase", "Ввожу решение в редактор")
            self.fill_editor(solution.code)
            post_delay = timing.choose_post_input() if timing else 0
            if post_delay > 0:
                self.notify("post_delay", round(post_delay, 1))
                store.transition(run_id, "post_delay")
                post_deadline = time.monotonic() + post_delay
                post_wall_deadline = time.time() + post_delay
                known_remaining = remaining if state == "ready" or self.config.execution.get("timer") else None
                control.wait_until(post_deadline, self.page, remaining=known_remaining,
                                   reserve_seconds=timing.reserve_seconds, wall_deadline=post_wall_deadline)
            self.verify_task(task)
            if self.accepted_marker() or is_visible(self.execution_locator("rejected")):
                raise AppError("Обнаружен старый результат. Отправка остановлена.")
            submit_button = self.submit_button()
            if not submit_button.is_enabled():
                raise AppError("Проверка кода недоступна: попытка могла истечь. Код не отправлен.")
            trace = {"click_event_seen": None, "same_origin_requests": {}, "same_origin_responses": {}}
            self.last_submission_trace = trace

            def record_request(request):
                try:
                    if origin(request.url) == self.config.portal_origin:
                        trace["same_origin_requests"][request.method] = trace["same_origin_requests"].get(request.method, 0) + 1
                except AppError:
                    pass

            def record_response(response):
                try:
                    if origin(response.url) == self.config.portal_origin:
                        key = str(response.status)
                        trace["same_origin_responses"][key] = trace["same_origin_responses"].get(key, 0) + 1
                except AppError:
                    pass

            submit_button.evaluate("""el => {
                window.__miptSubmitClickSeen = false;
                el.addEventListener('click', () => {window.__miptSubmitClickSeen = true;}, {once: true});
            }""")
            self.page.on("request", record_request)
            self.page.on("response", record_response)
            store.transition(run_id, "submitting")
            control.check()
            submitted = True  # Даже ошибка click не доказывает, что отправки не было.
            try:
                submit_button.click()
                try:
                    trace["click_event_seen"] = self.page.evaluate("window.__miptSubmitClickSeen === true")
                except PlaywrightError:
                    pass
                store.transition(run_id, "waiting")
                result = self.wait_result(task, control)
            finally:
                if trace["click_event_seen"] is None:
                    try:
                        trace["click_event_seen"] = self.page.evaluate("window.__miptSubmitClickSeen === true")
                    except PlaywrightError:
                        pass
                self.page.remove_listener("request", record_request)
                self.page.remove_listener("response", record_response)
            store.transition(run_id, result.value)
            return result
        except Cancelled:
            store.transition(run_id, Result.UNKNOWN.value if submitted else Result.CANCELLED.value)
            raise
        except BaseException:
            store.transition(run_id, Result.UNKNOWN.value if submitted else Result.INTERRUPTED.value)
            raise

    def wait_result(self, task: Task, control: RunControl | None = None) -> Result:
        deadline = time.monotonic() + self.config.result_timeout_ms / 1000
        while time.monotonic() < deadline:
            if control:
                control.check()
            self.verify_task(task)
            accepted = self.accepted_marker()
            rejected = self.execution_locator("rejected").filter(visible=True)
            good, bad = accepted is not None, is_visible(rejected)
            if good and bad:
                return Result.UNKNOWN
            if good:
                # Цвет сам по себе не является доказательством принятия.
                return Result.ACCEPTED if " ".join(accepted.inner_text().split()) == "Решение принято" else Result.UNKNOWN
            if bad:
                return Result.REJECTED
            self.page.wait_for_timeout(100)
        return Result.TIMEOUT

