import json

import pytest

from mipt_auto.accounts import CredentialVault, import_accounts, read_accounts_csv
from mipt_auto.errors import AppError
from mipt_auto.storage import Store


class MemoryVault:
    def __init__(self):
        self.secrets = {}

    def set(self, email, password):
        self.secrets[email] = password

    def get(self, email):
        return self.secrets.get(email)

    def delete(self, email):
        self.secrets.pop(email, None)


def test_csv_passwords_are_copied_to_main_csv_without_sqlite(tmp_path):
    path = tmp_path / "input.csv"
    path.write_text('\ufeffemail,password\nTester@example.test,"s,ecret"\nmanual@example.test,\n', encoding="utf-8")
    records = read_accounts_csv(path)
    vault = CredentialVault(tmp_path / "accounts.csv")
    with Store(tmp_path / "data") as store:
        assert import_accounts(records, store, vault) == 2
        rows = store.accounts()
        assert len(rows) == 2
        assert vault.get("tester@example.test") == "s,ecret"
        assert "s,ecret" not in json.dumps(rows)
        assert b"s,ecret" not in (tmp_path / "data/progress.sqlite3").read_bytes()
        assert import_accounts(records, store, vault) == 2
        assert len(store.accounts()) == 2
        assert read_accounts_csv(tmp_path / "accounts.csv")[0].password == "s,ecret"


def test_csv_vault_updates_and_deletes_only_selected_email(tmp_path):
    vault = CredentialVault(tmp_path / "accounts.csv")
    vault.set("One@example.test", 'a,"b"')
    vault.set("two@example.test", "second")
    vault.set("one@example.test", "changed")
    assert vault.get("one@example.test") == "changed"
    assert vault.get("two@example.test") == "second"
    assert len(read_accounts_csv(tmp_path / "accounts.csv")) == 2
    vault.delete("one@example.test")
    assert vault.get("one@example.test") is None
    assert vault.get("two@example.test") == "second"


@pytest.mark.parametrize("body", ["email,password\nx@example.test,a\nx@example.test,b\n",
                                 "email,password\nwrong,secret\n", "email,password\nx@example.test,a,b\n"])
def test_invalid_csv_is_rejected_before_import(tmp_path, body):
    path = tmp_path / "input.csv"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(AppError):
        read_accounts_csv(path)


def test_empty_template(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("email,password\n", encoding="utf-8")
    assert read_accounts_csv(path) == []


def test_csv_write_failure_does_not_create_account(tmp_path):
    class DeniedVault:
        def set(self, *_):
            raise AppError("Нет доступа")
    from mipt_auto.accounts import AccountInput
    with Store(tmp_path / "data") as store:
        with pytest.raises(AppError, match="Импортировано 0"):
            import_accounts([AccountInput("a@example.test", "not-logged")], store, DeniedVault())
        assert store.accounts() == []
