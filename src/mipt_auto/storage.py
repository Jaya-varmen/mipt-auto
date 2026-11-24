from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
import uuid

from .models import Task


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def prepare_data_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def private_json(path: Path, value) -> None:
    prepare_data_dir(path.parent)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def configure_logging(data_dir: Path) -> None:
    prepare_data_dir(data_dir)
    path = data_dir / "app.log"
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger("mipt_auto")
    for old in logger.handlers[:]:
        old.close()
        logger.removeHandler(old)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


class Store:
    def __init__(self, directory: Path, account_id: str | None = None):
        self.account_id = account_id
        prepare_data_dir(directory)
        path = directory / "progress.sqlite3"
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL,
                has_password INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                id TEXT PRIMARY KEY, course_key TEXT NOT NULL, created_at TEXT NOT NULL,
                tasks_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, course_key TEXT NOT NULL, task_id TEXT NOT NULL,
                state TEXT NOT NULL, started_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
        """)
        for table in ("runs", "snapshots"):
            if "account_id" not in {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN account_id TEXT")
        self.db.commit()

    def accounts(self) -> list[dict]:
        return [dict(row) for row in self.db.execute("SELECT * FROM accounts ORDER BY email")]

    def upsert_account(self, email: str, has_password: bool = False) -> str:
        existing = self.db.execute("SELECT id FROM accounts WHERE email = ?", (email,)).fetchone()
        account_id = existing[0] if existing else str(uuid.uuid4())
        with self.db:
            self.db.execute("""INSERT INTO accounts VALUES (?, ?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET has_password = excluded.has_password""",
                (account_id, email, int(has_password), now()))
        return account_id

    def remove_account(self, account_id: str):
        with self.db:
            self.db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))

    def save_tasks(self, course_key: str, tasks: list[Task]) -> str:
        snapshot_id = str(uuid.uuid4())
        # Не смешиваем снимки разных аккаунтов. URL и профиль пользователя не сохраняются.
        records = [{"task_id": t.task_id, "title": t.title, "status": t.status} for t in tasks]
        with self.db:
            self.db.execute("INSERT INTO snapshots (id, course_key, created_at, tasks_json, account_id) VALUES (?, ?, ?, ?, ?)",
                            (snapshot_id, course_key, now(), json.dumps(records, ensure_ascii=False), self.account_id))
        return snapshot_id

    def begin_run(self, course_key: str, task_id: str) -> str:
        run_id = str(uuid.uuid4())
        stamp = now()
        with self.db:
            self.db.execute("INSERT INTO runs (id, course_key, task_id, state, started_at, updated_at, account_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (run_id, course_key, task_id, "prepared", stamp, stamp, self.account_id))
        return run_id

    def transition(self, run_id: str, state: str) -> None:
        with self.db:
            self.db.execute("UPDATE runs SET state = ?, updated_at = ? WHERE id = ?",
                            (state, now(), run_id))
        logging.getLogger("mipt_auto").info("run=%s state=%s", run_id, state)

    def was_accepted(self, course_key: str, task_id: str) -> bool:
        row = self.db.execute(
            """SELECT 1 FROM runs WHERE course_key = ? AND task_id = ?
               AND state = 'accepted' AND account_id IS ? LIMIT 1""",
            (course_key, task_id, self.account_id),
        ).fetchone()
        return row is not None

    def recent_runs(self) -> list[dict]:
        return [dict(row) for row in self.db.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 20")]

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

