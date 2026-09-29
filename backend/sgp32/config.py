from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sgp32_common.security import register_secret


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", hide_input_in_errors=True)
    database_url_file: Path | None = None
    database_url: SecretStr = SecretStr("sqlite:///./data/sgp32.db")
    operator_username: str = "admin"
    operator_password_hash: SecretStr | None = None
    operator_password_hash_file: Path | None = None
    onomondo_api_url: str = "https://app.esim-iot.onomondo.com/api"
    onomondo_api_key: SecretStr | None = None
    onomondo_api_key_file: Path | None = None
    onomondo_schema_confirmed: bool = False
    poll_interval: float = Field(default=15, ge=10)
    operation_timeout: float = Field(default=1800, ge=60)

    @model_validator(mode="after")
    def validate_secrets(self) -> "Settings":
        for field in ("operator_password_hash", "onomondo_api_key", "database_url"):
            path = getattr(self, field + "_file")
            if path:
                if path.name == "api-keys.md":
                    raise ValueError("Forbidden secret source")
                resolved = path.resolve()
                if resolved.name == "api-keys.md":
                    raise ValueError("Forbidden secret source")
                try:
                    value = resolved.read_text().strip()
                except OSError:
                    raise ValueError("Required secret file unavailable") from None
                setattr(self, field, SecretStr(value))
            secret = getattr(self, field)
            if (
                not secret
                or not secret.get_secret_value()
                or "REPLACE_ME" in secret.get_secret_value()
            ):
                raise ValueError("Required secret missing")
            register_secret(secret.get_secret_value())
        assert self.operator_password_hash
        if not self.operator_password_hash.get_secret_value().startswith("$argon2id$"):
            raise ValueError("Operator password must be an Argon2id hash")
        url = urlsplit(self.onomondo_api_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("Onomondo URL must be a credential-free HTTPS URL")
        return self
