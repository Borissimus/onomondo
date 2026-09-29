import os
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError
from sgp32_common.messages import DiagnosticsRequest, decode, envelope

from sgp32_agent.modem import ModemDiagnostics


class Clock(Protocol):
    def now(self) -> float: ...


class SystemClock:
    def now(self) -> float:
        return time.time()


class CommandStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, expires REAL NOT NULL)"
            )

    def claim(self, command_id: str, expires: float, now: float) -> bool:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("DELETE FROM commands WHERE expires < ?", (now - 86400,))
            try:
                db.execute("INSERT INTO commands VALUES (?, ?)", (command_id, expires))
                return True
            except sqlite3.IntegrityError:
                return False


class Dispatcher:
    def __init__(
        self,
        device_id: str,
        modem: ModemDiagnostics,
        store: CommandStore,
        clock: Clock | None = None,
    ):
        self.device_id, self.modem, self.store = device_id, modem, store
        self.clock = clock or SystemClock()

    def dispatch(self, topic: str, raw: bytes, retained: bool = False) -> dict[str, Any] | None:
        if topic != f"devices/{self.device_id}/commands":
            return None
        try:
            message = decode(raw)
            if message.type != "modem.diagnostics.request":
                return None
            command = DiagnosticsRequest.model_validate(message.payload)
        except (ValueError, ValidationError):
            return None
        now = self.clock.now()
        created, expires = command.createdAt.timestamp(), command.expiresAt.timestamp()
        reason = None
        if retained:
            reason = "retained_command"
        elif expires <= now:
            reason = "expired"
        elif (
            created > now + 30
            or expires <= created
            or expires - created > 300
            or abs(message.timestamp.timestamp() - created) > 30
        ):
            reason = "invalid_command_time"
        elif command.includeSensitiveIdentifiers and not self.modem.allow_imsi:
            reason = "sensitive_identifiers_not_authorized"
        elif not self.store.claim(str(command.commandId), expires, now):
            reason = "duplicate"
        if reason:
            payload: dict[str, Any] = {
                "commandId": str(command.commandId),
                "status": "rejected",
                "reason": reason,
            }
        else:
            try:
                payload = {
                    "commandId": str(command.commandId),
                    **self.modem.collect(command.includeSensitiveIdentifiers),
                }
            except Exception:
                payload = {
                    "commandId": str(command.commandId),
                    "status": "failed",
                    "errors": ["diagnostics_failed"],
                }
        result = envelope("modem.diagnostics.result", payload, str(message.correlationId))
        result["timestamp"] = datetime.fromtimestamp(now, UTC).isoformat()
        return result
