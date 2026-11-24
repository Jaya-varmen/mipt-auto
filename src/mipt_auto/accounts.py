import csv
from dataclasses import dataclass
import os
from pathlib import Path
import re
import tempfile

from .errors import AppError


def normalize_email(value: str) -> str:
    value = value.strip().casefold()
    if len(value) > 254 or not re.fullmatch(r"[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+", value):
        raise AppError("Некорректный email. Проверьте адрес без пробелов.")
    return value


@dataclass(repr=False)
class AccountInput:
    email: str
    password: str = ""


def read_accounts_csv(path: Path) -> list[AccountInput]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            sample = handle.read(4096)
            handle.seek(0)
            delimiter = ";" if ";" in sample.splitlines()[0] else ","
            reader = csv.DictReader(handle, delimiter=delimiter)
            if reader.fieldnames != ["email", "password"]:
                raise AppError("В CSV нужны две колонки в порядке email,password.")
            records, seen = [], set()
            for number, row in enumerate(reader, start=2):
                if not any(row.values()):
                    continue
                if None in row or row.get("email") is None or row.get("password") is None:
                    raise AppError(f"Неверное число колонок в строке {number}.")
                try:
                    email = normalize_email(row["email"])
                except AppError:
                    raise AppError(f"Некорректный email в строке {number}.") from None
                if email in seen:
                    raise AppError(f"Повтор email в строке {number}. Удалите дубликат перед импортом.")
                seen.add(email)
                records.append(AccountInput(email, row["password"]))
            return records
    except (OSError, UnicodeError, csv.Error, IndexError):
        raise AppError("Не удалось прочитать CSV в UTF-8. Используйте шаблон email,password.") from None


class CredentialVault:
    """Локальный CSV с колонками email,password для обеих платформ."""

    def __init__(self, path: Path):
        self.path = Path(path)

    @staticmethod
    def store_label():
        return "accounts.csv"

    def _records(self):
        return read_accounts_csv(self.path) if self.path.exists() else []

    def _write(self, records):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                             dir=self.path.parent, prefix=".accounts-",
                                             suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                writer = csv.writer(handle)
                writer.writerow(("email", "password"))
                writer.writerows((record.email, record.password) for record in records)
            os.replace(temporary, self.path)
        except OSError:
            raise AppError("Не удалось записать accounts.csv.") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def set(self, email, password):
        email = normalize_email(email)
        records = self._records()
        for record in records:
            if record.email == email:
                record.password = password
                break
        else:
            records.append(AccountInput(email, password))
        self._write(records)

    def get(self, email):
        email = normalize_email(email)
        return next((record.password for record in self._records() if record.email == email), None)

    def delete(self, email):
        email = normalize_email(email)
        self._write([record for record in self._records() if record.email != email])


def import_accounts(records, store, vault):
    """CSV сначала целиком проверяется; аккаунты и пароли сохраняются в accounts.csv."""
    imported = 0
    for record in records:
        try:
            vault.set(record.email, record.password)
            store.upsert_account(record.email, bool(record.password))
            imported += 1
        except AppError:
            raise AppError(f"Импортировано {imported} аккаунтов. Следующая запись не сохранена в accounts.csv.") from None
    return imported
