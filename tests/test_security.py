import pytest
from pydantic import ValidationError
from sgp32.config import Settings
from sgp32_common.security import scrub


def test_fail_closed(monkeypatch):
    for name in (
        "ONOMONDO_API_KEY",
        "ONOMONDO_API_KEY_FILE",
        "OPERATOR_PASSWORD_HASH",
        "OPERATOR_PASSWORD_HASH_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValidationError):
        Settings()


def test_redaction():
    value = {
        "Authorization": "Bearer private",
        "activationCode": "1$host$secret",
        "nested": {
            "imsi": "001010123456789",
            "iccid": "8945000000000000001",
            "eid": "89000000000000000000000000000001",
        },
        "text": "Bearer private 001010123456789 password=hunter2 1$host$secret",
    }
    rendered = str(scrub(value))
    for secret in (
        "private",
        "hunter2",
        "001010123456789",
        "8945000000000000001",
        "89000000000000000000000000000001",
        "1$host$secret",
    ):
        assert secret not in rendered


def test_forbidden_file(tmp_path):
    with pytest.raises(ValidationError, match="Forbidden secret source"):
        Settings(operator_password_hash_file=tmp_path / "api-keys.md")
