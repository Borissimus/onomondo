import json
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

MAX_PAYLOAD = 32768


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Envelope(StrictModel):
    schemaVersion: Literal[1] = 1
    messageId: UUID
    timestamp: AwareDatetime
    correlationId: UUID
    type: str
    payload: dict[str, Any]


class DiagnosticsRequest(StrictModel):
    commandId: UUID
    createdAt: AwareDatetime
    expiresAt: AwareDatetime
    includeSensitiveIdentifiers: bool = False


class DiagnosticsResult(StrictModel):
    commandId: UUID
    status: Literal["succeeded", "failed", "rejected"]
    reason: str | None = Field(default=None, max_length=128)
    registration: str = Field(default="unknown", max_length=64)
    operator: dict[str, str] = Field(default_factory=dict)
    packetAttached: bool = False
    signal: dict[str, int | None] = Field(default_factory=dict)
    ipAddresses: list[str] = Field(default_factory=list, max_length=16)
    errors: list[str] = Field(default_factory=list, max_length=32)
    identity: str | None = Field(default=None, max_length=256)
    simReady: bool = False
    pdpContexts: list[str] = Field(default_factory=list, max_length=16)
    # Agent always masks identifiers on MQTT. Explicit IMSI collection is local only.
    identifiers: dict[str, str] = Field(default_factory=dict)


class Presence(StrictModel):
    schemaVersion: Literal[1] = 1
    state: Literal["online", "offline"]
    sessionId: UUID
    timestamp: AwareDatetime


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def envelope(kind: str, payload: dict[str, Any], correlation: str) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "messageId": str(uuid4()),
        "timestamp": now_iso(),
        "correlationId": correlation,
        "type": kind,
        "payload": payload,
    }


def decode(raw: bytes) -> Envelope:
    if len(raw) > MAX_PAYLOAD:
        raise ValueError("payload_too_large")
    return Envelope.model_validate_json(raw)


def encode(value: dict[str, Any]) -> bytes:
    raw = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > MAX_PAYLOAD:
        raise ValueError("payload_too_large")
    return raw


class MessageBus(Protocol):
    def publish(self, topic: str, payload: dict[str, Any], *, retain: bool = False) -> None: ...
    def subscribe(self, topic: str) -> None: ...
