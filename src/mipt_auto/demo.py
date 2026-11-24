from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import threading

from .config import Config


class DemoHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] not in {"/", "/course", "/task/1.1.1", "/task/1.1.2", "/task/1.1.3"}:
            self.send_error(404)
            return
        body = files("mipt_auto").joinpath("demo_site/index.html").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


@contextmanager
def demo_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), DemoHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def demo_config(url: str, channel: str = "") -> Config:
    return Config(
        base_url=url, course_url=url + "/course", course_key="demo",
        course_title="Демонстрационный курс", module_title="Модуль 1. Проверка прототипа",
        browser_channel=channel,
        navigation_pause_min_seconds=0,
        navigation_pause_max_seconds=0,
        collection={"scope_css": "#tasks", "row_css": ".task", "title_css": "a", "link_css": "a",
                    "statuses": {"completed": "[data-status='completed']", "not_started": "[data-status='not_started']"}},
        execution={
            "task_heading": {"css": "h1"},
            "start": {"role": "button", "name": "Начать попытку"},
            "editor": {"css": "#code"}, "editor_kind": "textarea",
            "submit": {"role": "button", "name": "Проверить код"},
            "accepted": {"css": "#accepted"}, "rejected": {"css": "#rejected"},
            "next_task": {"role": "link", "name": "Следующая задача"},
            "timer": {"css": "#timer"}, "attempt_limit_seconds": 540,
        },
    )

