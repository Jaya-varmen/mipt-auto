from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLayout, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .accounts import AccountInput, CredentialVault, import_accounts, normalize_email, read_accounts_csv
from .control import TimingPolicy
from .course_profiles import COURSES, course_config_path
from .errors import AppError
from .models import SolutionLibrary
from .storage import Store, configure_logging
from .worker import RunOptions, RunnerWorker
from .qt_runtime import prepare_qt_plugins
from . import __version__
from .updater import Release, check_latest_release, download_release


STYLE = """
QMainWindow { background: #F3F5FA; }
QWidget { color: #202942; font-size: 13px; }
QGroupBox { background: white; border: 1px solid #DBE1ED; border-radius: 12px;
  margin-top: 14px; padding: 20px 16px 16px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 16px; padding: 0 5px; }
QLabel#subtitle, QLabel#muted { color: #66728A; }
QLabel#title { font-size: 26px; font-weight: 700; }
QLabel#countdown { font-size: 32px; font-weight: 600; color: #5146CD; }
QLabel#task { font-size: 17px; font-weight: 600; }
QLabel#status { color: #5146CD; background: #E8E6FF; border-radius: 10px; padding: 7px 14px; }
QPushButton { border: 1px solid #D7DDE9; border-radius: 7px; padding: 8px 13px; background: #FFFFFF; }
QPushButton:hover { background: #F0F1FC; border-color: #AAA6E8; }
QPushButton:disabled { color: #9CA5B7; border-color: #E3E7EF; background: #F7F8FB; }
QPushButton#primary { background: #5D51D8; color: white; border: 0; padding: 12px 23px; font-weight: 600; }
QPushButton#primary:hover { background: #4B40C1; }
QPushButton#primary:disabled { background: #D8D4EF; color: #9290A4; }
QPushButton#stop { color: #AE3748; }
QPushButton#stop:disabled { color: #9CA5B7; }
QPushButton#course:checked { background: #E8E6FF; border-color: #7266E3; color: #5146CD; }
QLineEdit, QComboBox, QSpinBox { background: white; border: 1px solid #D7DDE9; border-radius: 6px; padding: 7px; min-height: 20px; }
QTableWidget, QPlainTextEdit { border: 1px solid #E3E7EF; border-radius: 7px; background: white; alternate-background-color: #F8F9FC; }
QHeaderView::section { background: #F4F6FA; border: 0; border-bottom: 1px solid #E3E7EF; padding: 9px; color: #626D84; font-weight: 500; }
QTableWidget::item { padding: 5px; }
QProgressBar { border: 0; border-radius: 5px; background: #E9ECF4; height: 9px; text-align: center; }
QProgressBar::chunk { border-radius: 5px; background: #7266E3; }
QCheckBox { spacing: 7px; }
"""


class UpdateWorker(QThread):
    completed = Signal(object, str)

    def __init__(self, project_dir: Path, data_dir: Path, release: Release | None = None):
        super().__init__()
        self.project_dir, self.data_dir, self.release = project_dir, data_dir, release

    def run(self):
        try:
            result = (download_release(self.release, self.data_dir) if self.release
                      else check_latest_release(self.project_dir))
            self.completed.emit(result, "")
        except AppError as exc:
            self.completed.emit(None, str(exc))


class MainWindow(QMainWindow):
    def __init__(self, project_dir=None, data_dir=None, vault=None):
        super().__init__()
        self.project_dir = Path(project_dir or Path.cwd())
        self.data_dir = Path(data_dir or self.project_dir / "data")
        self.config_path = course_config_path(self.project_dir, "10-11", sys.platform)
        self.solutions_path = self.project_dir / "solutions.local.json"
        self.settings_path = self.project_dir / ("settings.windows.json" if sys.platform == "win32" else "settings.local.json")
        self.store = Store(self.data_dir)
        self.vault = vault
        self.csv_error = None
        try:
            self._sync_accounts_csv()
        except AppError as exc:
            self.csv_error = str(exc)
        self.worker = None
        self.update_worker = None
        self.available_release = None
        self.pending_close = False
        self.selected_ids = set()
        self.current_task = None
        self.account_rows = []
        self.setWindowTitle("MIPT Auto")
        self.resize(1180, 880)
        self.setMinimumSize(1000, 760)
        self._build()
        if self.csv_error:
            self.append_log(f"Ошибка accounts.csv: {self.csv_error}")
        update_error = self.data_dir / "update-error.log"
        try:
            message = update_error.read_text(encoding="utf-8").strip()
            update_error.unlink()
            if message:
                self.append_log(f"Обновление: {message}")
        except OSError:
            pass
        self._restore_settings()
        self.refresh_accounts()
        self.update_solution_label()
        self.mode_changed()

    def _build(self):
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        outer.setContentsMargins(26, 22, 26, 22)
        outer.setSpacing(15)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel(f"MIPT Auto · {__version__}")
        title.setObjectName("title")
        subtitle = QLabel("Аккаунты, задачи и контроль выполнения")
        subtitle.setObjectName("subtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch()
        self.status = QLabel("Готово к запуску")
        self.status.setObjectName("status")
        header.addWidget(self.status, 0, Qt.AlignmentFlag.AlignVCenter)
        self.update_button = QPushButton("Проверить обновления")
        header.addWidget(self.update_button)
        outer.addLayout(header)

        top = QHBoxLayout()
        top.setSpacing(18)
        self.accounts_box = QGroupBox("1. Выберите аккаунты")
        accounts_layout = QVBoxLayout(self.accounts_box)
        toolbar = QHBoxLayout()
        self.add_button = QPushButton("Добавить")
        self.import_button = QPushButton("Импорт CSV")
        self.template_button = QPushButton("Шаблон CSV")
        for button in (self.add_button, self.import_button, self.template_button):
            toolbar.addWidget(button)
        toolbar.addStretch()
        accounts_layout.addLayout(toolbar)
        self.accounts_table = QTableWidget(0, 3)
        self.accounts_table.setHorizontalHeaderLabels(["", "Email", "Пароль"])
        self.accounts_table.setColumnWidth(0, 38)
        self.accounts_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.accounts_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.accounts_table.verticalHeader().setVisible(False)
        self.accounts_table.setAlternatingRowColors(True)
        self.accounts_table.setMinimumHeight(145)
        accounts_layout.addWidget(self.accounts_table)
        self.empty_label = QLabel("База пуста. Заполните accounts.csv и нажмите «Импорт CSV».")
        self.empty_label.setWordWrap(True)
        self.empty_label.setObjectName("muted")
        accounts_layout.addWidget(self.empty_label)
        account_footer = QHBoxLayout()
        self.all_button = QPushButton("Выбрать все")
        self.remove_button = QPushButton("Удалить отмеченные")
        self.selection_label = QLabel("Выбрано: 0")
        self.selection_label.setObjectName("muted")
        account_footer.addWidget(self.all_button)
        account_footer.addWidget(self.remove_button)
        account_footer.addStretch()
        account_footer.addWidget(self.selection_label)
        accounts_layout.addLayout(account_footer)
        note = QLabel("CSV: email,password. Пароли сохраняются в accounts.csv.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        accounts_layout.addWidget(note)
        top.addWidget(self.accounts_box, 3)

        self.settings_box = QGroupBox("2. Настройте запуск")
        form = QVBoxLayout(self.settings_box)
        form.setSpacing(10)
        form.addWidget(QLabel("Курс"))
        courses = QHBoxLayout()
        self.course_group = QButtonGroup(self)
        self.course_group.setExclusive(True)
        self.course_buttons = {}
        for key, label in COURSES.items():
            button = QPushButton(label)
            button.setObjectName("course")
            button.setCheckable(True)
            button.setMinimumHeight(38)
            self.course_group.addButton(button)
            self.course_buttons[key] = button
            courses.addWidget(button)
        self.course_buttons["10-11"].setChecked(True)
        form.addLayout(courses)
        form.addWidget(QLabel("Режим"))
        self.mode = QComboBox()
        self.mode.addItem("edu.mipt.ru · выполнение очереди", "execute")
        self.mode.addItem("edu.mipt.ru · просмотр задач", "read")
        self.mode.addItem("edu.mipt.ru · тест одной задачи", "single")
        self.mode.addItem("Локальная демонстрация", "demo")
        self.mode.setMinimumHeight(38)
        form.addWidget(self.mode)
        self.task_id_label = QLabel("ID задачи для теста")
        self.task_id_input = QLineEdit()
        self.task_id_input.setPlaceholderText("Например, 1.1.5")
        form.addWidget(self.task_id_label)
        form.addWidget(self.task_id_input)
        form.addWidget(QLabel("Пауза перед вводом кода после начала попытки"))
        delays = QHBoxLayout()
        self.delay_min, self.delay_max = QSpinBox(), QSpinBox()
        for spin, initial in ((self.delay_min, 1), (self.delay_max, 4)):
            spin.setRange(1, 4)
            spin.setValue(initial)
            spin.setSuffix(" мин")
            spin.setMinimumHeight(38)
        delays.addWidget(QLabel("От"))
        delays.addWidget(self.delay_min)
        delays.addWidget(QLabel("до"))
        delays.addWidget(self.delay_max)
        form.addLayout(delays)
        form.addWidget(QLabel("После ввода кода: случайная пауза 3–6 с до отправки"))
        self.fast_demo = QCheckBox("Демо без ожидания")
        form.addWidget(self.fast_demo)
        self.solution_label = QLabel()
        self.solution_label.setObjectName("muted")
        self.solution_label.setWordWrap(True)
        form.addWidget(self.solution_label)
        self.solutions_button = QPushButton("Выбрать базу решений…")
        self.solutions_button.setMinimumHeight(38)
        form.addWidget(self.solutions_button)
        form.addStretch()
        explanation = QLabel(
            "Выполненные задачи пропускаются. Отклонённое решение отмечается в журнале, "
            "затем открывается следующая задача. При ошибке или неизвестном результате "
            "проверьте браузер и нажмите «Продолжить после ошибки» для пропуска задачи."
        )
        explanation.setWordWrap(True)
        explanation.setObjectName("muted")
        form.addWidget(explanation)
        top.addWidget(self.settings_box, 2)
        outer.addLayout(top)

        progress_box = QGroupBox("Выполнение")
        progress_layout = QGridLayout(progress_box)
        self.account_label = QLabel("Ожидание запуска")
        self.account_label.setObjectName("muted")
        self.task_label = QLabel("Выберите аккаунты и нажмите «Запустить»")
        self.task_label.setObjectName("task")
        self.task_label.setWordWrap(True)
        self.countdown = QLabel("—")
        self.countdown.setObjectName("countdown")
        self.countdown.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.countdown_note = QLabel("до отправки")
        self.countdown_note.setObjectName("muted")
        self.countdown_note.setAlignment(Qt.AlignmentFlag.AlignRight)
        progress_layout.addWidget(self.account_label, 0, 0)
        progress_layout.addWidget(self.task_label, 1, 0)
        progress_layout.addWidget(self.countdown, 0, 1, 2, 1)
        progress_layout.addWidget(self.countdown_note, 2, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        progress_layout.addWidget(self.progress, 2, 0)
        self.prompt_label = QLabel()
        self.prompt_label.setWordWrap(True)
        self.prompt_label.hide()
        progress_layout.addWidget(self.prompt_label, 3, 0, 1, 2)
        outer.addWidget(progress_box)

        self.tasks_table = QTableWidget(0, 3)
        self.tasks_table.setHorizontalHeaderLabels(["Задача", "Название", "Состояние"])
        self.tasks_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tasks_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tasks_table.verticalHeader().setVisible(False)
        self.tasks_table.setMaximumHeight(180)
        self.tasks_table.setMinimumHeight(105)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(400)
        self.log.setPlaceholderText("Здесь появятся этапы работы и результаты.")
        self.log.setMinimumHeight(90)
        self.log.setMaximumHeight(160)
        details = QSplitter(Qt.Orientation.Horizontal)
        details.addWidget(self.tasks_table)
        details.addWidget(self.log)
        details.setSizes([650, 430])
        outer.addWidget(details, 2)

        actions = QHBoxLayout()
        self.continue_button = QPushButton("Продолжить после входа")
        self.continue_button.setEnabled(False)
        self.stop_button = QPushButton("Остановить")
        self.stop_button.setObjectName("stop")
        self.stop_button.setEnabled(False)
        self.start_button = QPushButton("Запустить")
        self.start_button.setObjectName("primary")
        actions.addWidget(self.continue_button)
        actions.addStretch()
        actions.addWidget(self.stop_button)
        actions.addWidget(self.start_button)
        outer.addLayout(actions)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(root)
        self.setCentralWidget(scroll)

        self.add_button.clicked.connect(self.add_account)
        self.import_button.clicked.connect(self.import_csv)
        self.template_button.clicked.connect(self.open_template)
        self.remove_button.clicked.connect(self.remove_accounts)
        self.all_button.clicked.connect(self.select_all)
        self.accounts_table.itemChanged.connect(self.selection_changed)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        self.course_group.buttonClicked.connect(lambda *_: self.course_changed())
        self.solutions_button.clicked.connect(self.choose_solutions)
        self.delay_min.valueChanged.connect(lambda v: self.delay_max.setValue(max(v, self.delay_max.value())))
        self.delay_max.valueChanged.connect(lambda v: self.delay_min.setValue(min(v, self.delay_min.value())))
        self.start_button.clicked.connect(self.start_run)
        self.stop_button.clicked.connect(self.stop_run)
        self.continue_button.clicked.connect(self.continue_run)
        self.update_button.clicked.connect(self.check_or_install_update)

    def credential_vault(self):
        if self.vault is None:
            self.vault = CredentialVault(self.project_dir / "accounts.csv")
        return self.vault

    def _sync_accounts_csv(self):
        """Подхватывает ручные правки CSV и сбрасывает старые признаки Keychain."""
        path = self.project_dir / "accounts.csv"
        records = read_accounts_csv(path) if path.exists() else []
        passwords = {record.email: bool(record.password) for record in records}
        for row in self.store.accounts():
            self.store.upsert_account(row["email"], passwords.get(row["email"], False))
        for record in records:
            self.store.upsert_account(record.email, bool(record.password))

    def show_error(self, text):
        dialog = QMessageBox(QMessageBox.Icon.Warning, "MIPT Auto", text,
                             QMessageBox.StandardButton.Ok, self)
        dialog.setStyleSheet("QMessageBox { background: #F3F5FA; } QMessageBox QLabel { color: #202942; }")
        dialog.exec()

    def _restore_settings(self):
        try:
            settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
            self.selected_ids = set(settings.get("selected_ids", []))
            saved_course = settings.get("course", "10-11")
            if saved_course in self.course_buttons:
                self.course_buttons[saved_course].setChecked(True)
                self.course_changed()
            self.delay_min.setValue(int(settings.get("min_minutes", 1)))
            self.delay_max.setValue(int(settings.get("max_minutes", 4)))
            candidate = settings.get("solutions_path")
            if candidate:
                self.solutions_path = Path(candidate)
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def save_settings(self):
        value = {"selected_ids": list(self.selected_ids), "course": self.selected_course(),
                 "min_minutes": self.delay_min.value(),
                 "max_minutes": self.delay_max.value(), "solutions_path": str(self.solutions_path)}
        try:
            self.settings_path.touch(mode=0o600, exist_ok=True)
            self.settings_path.chmod(0o600)
            self.settings_path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            self.append_log("Не удалось сохранить настройки окна.")

    def refresh_accounts(self):
        rows = self.store.accounts()
        if self.mode.currentData() == "demo" and not rows:
            rows = [{"id": "demo-local", "email": "demo@example.test", "has_password": False}]
            self.selected_ids.add("demo-local")
        self.account_rows = rows
        self.accounts_table.blockSignals(True)
        self.accounts_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Checked if row["id"] in self.selected_ids else Qt.CheckState.Unchecked)
            self.accounts_table.setItem(index, 0, check)
            for column, value in ((1, row["email"]), (2, f"В {CredentialVault.store_label()}" if row["has_password"] else "Ручной ввод")):
                item = QTableWidgetItem(value)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.accounts_table.setItem(index, column, item)
        self.accounts_table.blockSignals(False)
        self.empty_label.setVisible(not rows or rows[0]["id"] == "demo-local")
        self.empty_label.setText("Демонстрационный аккаунт: вход на сайт не выполняется." if rows else "База пуста. Заполните accounts.csv и нажмите «Импорт CSV».")
        self.selection_changed()
        self.fast_demo.setVisible(self.mode.currentData() == "demo")

    def selected_accounts(self):
        return [row for i, row in enumerate(self.account_rows)
                if self.accounts_table.item(i, 0).checkState() == Qt.CheckState.Checked]

    def selection_changed(self, *_):
        selected = self.selected_accounts()
        self.selected_ids = {row["id"] for row in selected}
        self.selection_label.setText(f"Выбрано: {len(selected)} из {len(self.account_rows)}")
        self.start_button.setEnabled(bool(selected) and not (self.worker and self.worker.isRunning()))

    def select_all(self):
        check = Qt.CheckState.Unchecked if len(self.selected_accounts()) == len(self.account_rows) else Qt.CheckState.Checked
        for i in range(self.accounts_table.rowCount()):
            self.accounts_table.item(i, 0).setCheckState(check)

    def mode_changed(self):
        self.refresh_accounts()
        self.update_solution_label()
        single = self.mode.currentData() == "single"
        self.task_id_label.setVisible(single)
        self.task_id_input.setVisible(single)

    def course_changed(self):
        self.config_path = course_config_path(self.project_dir, self.selected_course(), sys.platform)
        self.update_solution_label()

    def selected_course(self):
        return next(key for key, button in self.course_buttons.items() if button.isChecked())

    def add_account(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Добавить аккаунт")
        dialog.setMinimumWidth(420)
        layout = QFormLayout(dialog)
        email, password = QLineEdit(), QLineEdit()
        password.setEchoMode(QLineEdit.EchoMode.Password)
        password.setPlaceholderText("Можно оставить пустым и войти вручную")
        layout.addRow("Email", email)
        layout.addRow("Пароль", password)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            address = normalize_email(email.text())
            secret = password.text()
            import_accounts([AccountInput(address, secret)], self.store, self.credential_vault())
            self.selected_ids.update(row["id"] for row in self.store.accounts() if row["email"] == address)
            self.refresh_accounts()
        except AppError as exc:
            self.show_error(str(exc))
        finally:
            password.clear()

    def import_csv(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Импорт аккаунтов", str(self.project_dir / "accounts.csv"), "CSV (*.csv)")
        if not filename:
            return
        try:
            records = read_accounts_csv(Path(filename))
            if not records:
                self.show_error("CSV пока пуст. Добавьте строки под заголовком email,password.")
                return
            count = import_accounts(records, self.store, self.credential_vault())
            self.refresh_accounts()
            self.append_log(f"Импортировано аккаунтов: {count}. Пароли сохранены в accounts.csv.")
        except AppError as exc:
            self.refresh_accounts()
            self.show_error(str(exc))

    def open_template(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.project_dir / "accounts.csv")))

    def remove_accounts(self):
        selected = [a for a in self.selected_accounts() if a["id"] != "demo-local"]
        if not selected:
            return
        if QMessageBox.question(self, "Удалить аккаунты", f"Удалить {len(selected)} аккаунтов из базы и их пароли из {CredentialVault.store_label()}?") != QMessageBox.StandardButton.Yes:
            return
        try:
            for account in selected:
                self.credential_vault().delete(account["email"])
                self.store.remove_account(account["id"])
            self.refresh_accounts()
        except AppError as exc:
            self.show_error(str(exc))

    def choose_solutions(self):
        filename, _ = QFileDialog.getOpenFileName(self, "База решений", str(self.solutions_path), "JSON (*.json)")
        if not filename:
            return
        try:
            SolutionLibrary.from_file(Path(filename))
            self.solutions_path = Path(filename)
            self.update_solution_label()
        except AppError as exc:
            self.show_error(str(exc))

    def update_solution_label(self):
        if self.mode.currentData() == "demo":
            self.solution_label.setText("Встроенная база: две задачи и один выполненный пример.")
            return
        try:
            library = SolutionLibrary.from_file(self.solutions_path)
            course_key = "python-8-9" if self.selected_course() == "8-9" else "python-10-11"
            available = sum(key[0] == course_key for key in library.entries)
            self.solution_label.setText(f"Для выбранного курса: {available} решений. Совпадение проверяется по курсу, ID и названию.")
        except AppError:
            self.solution_label.setText("Выберите JSON с решениями для выполнения очереди.")

    def start_run(self):
        if self.worker and self.worker.isRunning():
            return
        if self.update_worker and self.update_worker.isRunning():
            self.show_error("Дождитесь завершения проверки обновления.")
            return
        try:
            self._sync_accounts_csv()
        except AppError as exc:
            self.show_error(f"Ошибка accounts.csv: {exc}")
            return
        self.refresh_accounts()
        selected = self.selected_accounts()
        mode = self.mode.currentData()
        self.config_path = course_config_path(self.project_dir, self.selected_course(), sys.platform)
        if mode != "demo" and not self.config_path.is_file():
            self.show_error("Профиль выбранного курса не найден. Проверьте установку программы.")
            return
        if mode == "single":
            if len(selected) != 1:
                self.show_error("Для теста одной задачи отметьте ровно один аккаунт.")
                return
            if not self.task_id_input.text().strip():
                self.show_error("Укажите ID задачи, например 1.1.5.")
                return
        self.save_settings()
        self.current_task = None
        self.tasks_table.setRowCount(0)
        self.countdown.setText("—")
        self.progress.setValue(0)
        self.prompt_label.hide()
        options = RunOptions(accounts=selected, mode=mode, task_id=self.task_id_input.text().strip(),
                             config_path=self.config_path, solutions_path=self.solutions_path, data_dir=self.data_dir,
                             timing=TimingPolicy(self.delay_min.value() * 60, self.delay_max.value() * 60),
                             fast_demo=self.fast_demo.isChecked())
        self.worker = RunnerWorker(options, self)
        self.worker.event.connect(self.handle_event)
        self.worker.needs_user.connect(self.request_user)
        self.worker.needs_recovery.connect(self.request_recovery)
        self.worker.completed.connect(self.run_completed)
        self.worker.finished.connect(self.worker_finished)
        self.accounts_box.setEnabled(False)
        self.settings_box.setEnabled(False)
        self.start_button.setEnabled(False)
        self.update_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status.setText("Запускаю браузер")
        self.append_log("Начат запуск выбранных аккаунтов.")
        self.worker.start()

    def append_log(self, message):
        self.log.appendPlainText(f"{datetime.now().strftime('%H:%M:%S')}  {message}")

    def handle_event(self, kind, value):
        if kind == "account":
            self.account_label.setText(f"Аккаунт {value['index']} из {value['total']} · {value['email']}")
            self.status.setText("Подключение")
        elif kind == "navigation":
            self.append_log(f"Переход: {value:.2f} с до начала документа.")
        elif kind == "transition_delay":
            self.status.setText("Пауза перед переходом")
            self.append_log(f"Пауза перед переходом: {value:.1f} с.")
        elif kind == "collected":
            self.append_log(f"Прочитано {value['count']} задач за {value['seconds']:.3f} с.")
        elif kind == "tasks":
            self.tasks_table.setRowCount(len(value))
            labels = {"completed": "Выполнена", "not_started": "Не начата", "incomplete": "Не выполнена", "unknown": "Неизвестно"}
            for row, task in enumerate(value):
                for col, text in enumerate((task["id"], task["title"], labels.get(task["status"], task["status"]))):
                    item = QTableWidgetItem(text)
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    self.tasks_table.setItem(row, col, item)
        elif kind == "task":
            self.current_task = value["id"]
            self.task_label.setText(f"{value['id']} · {value['title']}")
            self.progress.setRange(0, value["total"])
            self.progress.setValue(value["index"] - 1)
            self.countdown.setText("—")
            self.countdown_note.setText("до отправки")
        elif kind == "task_state":
            labels = {"accepted": "Решение принято", "rejected": "Решение отклонено", "timeout": "Результат не подтверждён", "unknown": "Неизвестный результат"}
            text = labels.get(value, value)
            self.status.setText(text)
            self.append_log(f"{self.current_task}: {text}")
            for row in range(self.tasks_table.rowCount()):
                if self.tasks_table.item(row, 0).text() == self.current_task:
                    self.tasks_table.item(row, 2).setText(text)
                    break
            if value in {"accepted", "rejected", "Уже выполнена — пропуск",
                         "Нет решения в базе — пропуск", "Пропущена после ошибки",
                         "Пропущена после проверки"}:
                self.progress.setValue(min(self.progress.maximum(), self.progress.value() + 1))
        elif kind == "delay":
            self.status.setText("Пауза перед вводом кода")
            self.countdown_note.setText("до ввода кода")
            self.append_log(f"Задержка перед вводом кода: {value:.1f} с.")
        elif kind == "post_delay":
            self.status.setText("Пауза перед отправкой")
            self.countdown_note.setText("до отправки")
            self.append_log(f"Задержка после ввода кода: {value:.1f} с.")
        elif kind == "phase":
            self.status.setText(value)
            self.append_log(value)
            if value == "Ввожу решение в редактор":
                self.countdown.setText("—")
        elif kind == "countdown":
            self.countdown.setText(f"{value // 60}:{value % 60:02d}")
        elif kind == "resumed":
            self.prompt_label.hide()
            self.continue_button.setEnabled(False)
            self.status.setText("Продолжаю")
        elif kind == "log":
            self.append_log(value)

    def request_user(self, message, inspection=False):
        self.prompt_label.setText(message)
        self.prompt_label.show()
        self.continue_button.setText("Закрыть браузер" if inspection else "Продолжить после входа")
        self.continue_button.setEnabled(True)
        self.status.setText("Ожидаю проверки браузера" if inspection else "Нужен ручной вход")

    def request_recovery(self, message):
        self.prompt_label.setText(message)
        self.prompt_label.show()
        self.continue_button.setText("Продолжить после ошибки")
        self.continue_button.setEnabled(True)
        self.status.setText("Ожидаю решения по ошибке")
        self.append_log("Пауза после ошибки задачи: браузер и вход сохранены.")

    def continue_run(self):
        if self.worker:
            self.continue_button.setEnabled(False)
            self.worker.resume()

    def stop_run(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.stop_button.setEnabled(False)
            self.continue_button.setEnabled(False)
            self.status.setText("Останавливаю…")

    def run_completed(self, message, success):
        self.append_log(message)
        self.status.setText("Завершено" if success else "Остановлено")
        self.task_label.setText(message)
        self.countdown.setText("—")
        self.prompt_label.hide()
        self.continue_button.setText("Продолжить после входа")

    def worker_finished(self):
        self.accounts_box.setEnabled(True)
        self.settings_box.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.continue_button.setEnabled(False)
        self.selection_changed()
        self.update_button.setEnabled(True)
        if self.pending_close:
            self.close()

    def check_or_install_update(self):
        if self.worker and self.worker.isRunning():
            self.show_error("Остановите выполнение задач перед обновлением.")
            return
        if self.update_worker and self.update_worker.isRunning():
            return
        release = self.available_release
        self.update_button.setEnabled(False)
        self.status.setText("Загружаю обновление" if release else "Проверяю обновления")
        self.update_worker = UpdateWorker(self.project_dir, self.data_dir, release)
        self.update_worker.completed.connect(self.update_completed)
        self.update_worker.start()

    def update_completed(self, result, error):
        if self.update_worker:
            self.update_worker.wait(5000)
        self.update_button.setEnabled(True)
        if error:
            self.status.setText("Обновление не выполнено")
            self.show_error(error)
            return
        if isinstance(result, Release):
            self.available_release = result
            self.update_button.setText(f"Установить {result.version}")
            self.status.setText(f"Доступна версия {result.version}")
            return
        if isinstance(result, Path):
            try:
                with (self.data_dir / "update-error.log").open("w") as errors:
                    subprocess.Popen([sys.executable, "-m", "mipt_auto.updater", "apply",
                                      "--project-dir", str(self.project_dir), "--archive", str(result),
                                      "--expected-version", self.available_release.version,
                                      "--parent-pid", str(os.getpid())], cwd=self.project_dir,
                                     stdout=subprocess.DEVNULL, stderr=errors)
            except OSError:
                self.show_error("Не удалось запустить установщик обновления.")
                return
            self.close()
            QApplication.instance().quit()
            return
        self.available_release = None
        self.status.setText("Установлена последняя версия")
        self.update_button.setText("Проверить обновления")

    def closeEvent(self, event):
        if self.update_worker and self.update_worker.isRunning():
            event.ignore()
            return
        if self.worker and self.worker.isRunning():
            self.pending_close = True
            self.stop_run()
            event.ignore()
            return
        self.save_settings()
        self.store.close()
        event.accept()


def main(argv=None):
    try:
        prepare_qt_plugins(Path.cwd() / "data" / "qt-plugins")
    except (AppError, OSError) as exc:
        print(str(exc) if isinstance(exc, AppError) else "Не удалось подготовить графический модуль.", file=sys.stderr)
        return 1
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("MIPT Auto")
    app.setOrganizationName("MIPT Auto Local")
    app.setStyleSheet(STYLE)
    configure_logging(Path.cwd() / "data")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
