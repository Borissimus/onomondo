import json
import logging
import re
from typing import Any

SECRET_KEYS = re.compile(r"authorization|password|secret|token|activation.?code|api.?key", re.I)
IDENTIFIER_KEYS = re.compile(r"imsi|iccid|eid", re.I)


def scrub(value: Any, *, identifiers: bool = True) -> Any:
    if isinstance(value, dict):
        return {
            str(k): "[REDACTED]"
            if SECRET_KEYS.search(str(k)) or (identifiers and IDENTIFIER_KEYS.search(str(k)))
            else scrub(v, identifiers=identifiers)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [scrub(v, identifiers=identifiers) for v in value]
    if isinstance(value, str):
        value = re.sub(r'(?i)Bearer\s+[^\s"\',;]+', "Bearer [REDACTED]", value)
        value = re.sub(r'1\$[^\s"\']+', "[REDACTED]", value)
        value = re.sub(
            r"(?i)(authorization|password|secret|token|activation.?code|api.?key)"
            r"([\s=:]+)[^\s,;]+",
            r"\1\2[REDACTED]",
            value,
        )
        if identifiers:
            value = re.sub(r"(?<!\d)\d{14,32}(?!\d)", "[REDACTED]", value)
        return value
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Deliberately omit exception reprs, arguments and arbitrary extra fields.
        return json.dumps({"level": record.levelname, "event": scrub(record.getMessage())})


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    for name in ("httpx", "httpcore", "sqlalchemy.engine", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
