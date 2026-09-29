import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    String,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def uid() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Euicc(Base):
    __tablename__ = "euiccs"
    eid: Mapped[str] = mapped_column(String(32), primary_key=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)
    profiles_observed_at: Mapped[float | None] = mapped_column(Float)


class Device(Base):
    __tablename__ = "devices"
    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    eid: Mapped[str] = mapped_column(ForeignKey("euiccs.eid"), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    state: Mapped[str] = mapped_column(String(32), default="unknown")
    session_id: Mapped[str | None] = mapped_column(String(36))
    last_seen: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, onupdate=time.time)


class Profile(Base):
    __tablename__ = "profiles"
    eid: Mapped[str] = mapped_column(ForeignKey("euiccs.eid"), primary_key=True)
    iccid: Mapped[str] = mapped_column(String(22), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(128))
    provider: Mapped[str | None] = mapped_column(String(128))
    profile_class: Mapped[str | None] = mapped_column(String(32))
    state: Mapped[str | None] = mapped_column(String(32))
    fallback: Mapped[bool | None] = mapped_column(Boolean)
    observed_at: Mapped[float] = mapped_column(Float, default=time.time)


class Operation(Base):
    __tablename__ = "operations"
    operation_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    eid: Mapped[str] = mapped_column(ForeignKey("euiccs.eid"), index=True)
    # Unique while active, NULL after a known terminal result. Also serializes refreshes.
    active_eid: Mapped[str | None] = mapped_column(String(32), unique=True)
    operation_type: Mapped[str] = mapped_column(String(32), default="list_profile_info")
    requested_by: Mapped[str] = mapped_column(String(128))
    correlation_id: Mapped[str] = mapped_column(String(36))
    state: Mapped[str] = mapped_column(String(32), default="created", index=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    request: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    raw_outcome: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)
    next_poll: Mapped[float] = mapped_column(Float, default=time.time)
    deadline: Mapped[float] = mapped_column(Float)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    attempts: Mapped[int] = mapped_column(default=0)


class DeviceCommand(Base):
    __tablename__ = "device_commands"
    command_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.device_id"), index=True)
    command_type: Mapped[str] = mapped_column(String(64), default="modem.diagnostics.request")
    correlation_id: Mapped[str] = mapped_column(String(36))
    state: Mapped[str] = mapped_column(String(32), default="queued")
    request: Mapped[dict[str, Any]] = mapped_column(JSON)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    expires_at: Mapped[float] = mapped_column(Float)


class Observation(Base):
    __tablename__ = "device_observations"
    message_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.device_id"), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    observed_at: Mapped[float] = mapped_column(Float, default=time.time)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(128))
    decision: Mapped[str] = mapped_column(String(64))
    correlation_id: Mapped[str] = mapped_column(String(36))
    timestamp: Mapped[float] = mapped_column(Float, default=time.time)


class Idempotency(Base):
    __tablename__ = "idempotency"
    __table_args__ = (UniqueConstraint("actor", "key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    actor: Mapped[str] = mapped_column(String(128))
    key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status_code: Mapped[int] = mapped_column(default=200)


class Outbox(Base):
    __tablename__ = "outbox"
    message_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    topic: Mapped[str] = mapped_column(String(256))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


def make_engine(url: str) -> Engine:
    engine = create_engine(url, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def sqlite_setup(connection: Any, _: Any) -> None:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA journal_mode=WAL")

    return engine


class Database:
    def __init__(self, url: str):
        self.engine = make_engine(url)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self.sessions.begin() as session:
            yield session


def audit(
    session: Session, actor: str, action: str, target: str, decision: str, correlation_id: str
) -> None:
    session.add(
        AuditEvent(
            actor=actor,
            action=action,
            target=target,
            decision=decision,
            correlation_id=correlation_id,
        )
    )


class Heartbeat(Base):
    __tablename__ = "service_heartbeats"
    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    updated_at: Mapped[float] = mapped_column(Float)
    ready: Mapped[bool] = mapped_column(Boolean)
