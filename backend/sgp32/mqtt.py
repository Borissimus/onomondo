import queue
import re
import time

from prometheus_client import Gauge
from pydantic import ValidationError
from sgp32_common.messages import DiagnosticsResult, Presence, decode
from sgp32_common.mqtt import MqttBus, MqttSettings
from sgp32_common.security import scrub
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from sgp32.storage import (
    Database,
    Device,
    DeviceCommand,
    Heartbeat,
    Observation,
    Outbox,
    audit,
)

MQTT_CONNECTED = Gauge("sgp32_mqtt_connected", "Worker MQTT connection state")
LAST_SEEN = Gauge("sgp32_device_last_seen_timestamp", "Device last seen", ["device"])


class BackendMessaging:
    def __init__(self, db: Database, bus: MqttBus):
        self.db, self.bus = db, bus
        for topic in (
            "devices/+/status",
            "devices/+/telemetry/modem",
            "devices/+/events/modem",
            "devices/+/commands/+/result",
        ):
            self.bus.subscribe(topic)

    @classmethod
    def from_env(cls, db: Database) -> "BackendMessaging":
        return cls(db, MqttBus(MqttSettings(), "sgp32-backend"))

    def start(self) -> None:
        self.bus.start()

    def stop(self) -> None:
        self.bus.stop()

    def receive(self, topic: str, raw: bytes, retained: bool = False) -> bool:
        match = re.fullmatch(
            r"devices/([A-Za-z0-9_-]{1,64})/(status|telemetry/modem|events/modem|"
            r"commands/([a-f0-9-]{36})/result)",
            topic,
        )
        if not match or len(raw) > 32768:
            return False
        device_id, suffix, command_id = match.groups()
        now = time.time()
        try:
            with self.db.session() as s:
                device = s.scalar(
                    select(Device).where(Device.device_id == device_id).with_for_update()
                )
                if not device or not device.enabled:
                    return False
                if suffix == "status":
                    presence = Presence.model_validate_json(raw)
                    observed = presence.timestamp.timestamp()
                    if observed > now + 30:
                        return False
                    session = str(presence.sessionId)
                    # LWT is stamped at connection creation; same-session offline is still valid.
                    if (
                        device.session_id != session
                        and device.last_seen
                        and observed < device.last_seen
                    ):
                        return False
                    if presence.state == "online" and now - observed > 180:
                        return False
                    if presence.state == "offline":
                        device.state = "offline"
                    elif device.session_id != session or device.state in {"offline", "unknown"}:
                        device.state = "online_unregistered"
                    # A heartbeat confirms MQTT presence, not a change in modem registration.
                    device.session_id = session
                    device.last_seen = now
                    LAST_SEEN.labels(device_id).set(now)
                    return True
                if retained:
                    return False
                message = decode(raw)
                if abs(now - message.timestamp.timestamp()) > 300:
                    return False
                if suffix == "events/modem":
                    if message.type != "modem.event" or set(message.payload) != {"line"}:
                        return False
                    line = message.payload["line"]
                    if not isinstance(line, str) or len(line) > 1024:
                        return False
                    if not s.get(Observation, str(message.messageId)):
                        s.add(
                            Observation(
                                message_id=str(message.messageId),
                                device_id=device_id,
                                payload=scrub({"event": line}),
                                observed_at=now,
                            )
                        )
                    return True
                if command_id:
                    if message.type != "modem.diagnostics.result":
                        return False
                    result = DiagnosticsResult.model_validate(message.payload)
                    if str(result.commandId) != command_id:
                        return False
                    command = s.scalar(
                        select(DeviceCommand)
                        .where(DeviceCommand.command_id == command_id)
                        .with_for_update()
                    )
                    if (
                        not command
                        or command.device_id != device_id
                        or command.correlation_id != str(message.correlationId)
                        or command.expires_at < now
                        or command.state != "queued"
                    ):
                        return False
                    command.result = scrub(result.model_dump(mode="json"))
                    command.state = result.status
                    audit(
                        s,
                        device_id,
                        "diagnostics.result",
                        command_id,
                        result.status,
                        command.correlation_id,
                    )
                    payload = command.result
                else:
                    if message.type != "modem.telemetry":
                        return False
                    # Same normalized schema, without command correlation for periodic telemetry.
                    result = DiagnosticsResult.model_validate(message.payload)
                    payload = scrub(result.model_dump(mode="json"))
                if s.get(Observation, str(message.messageId)):
                    return False
                s.add(
                    Observation(
                        message_id=str(message.messageId),
                        device_id=device_id,
                        payload=payload,
                        observed_at=now,
                    )
                )
                device.last_seen = now
                registered = result.registration in {"registered_home", "registered_roaming"}
                device.state = (
                    "degraded"
                    if result.errors
                    else "data_connected"
                    if result.packetAttached and result.ipAddresses
                    else "online_registered"
                    if registered
                    else "online_unregistered"
                )
                LAST_SEEN.labels(device_id).set(now)
                return True
        except (ValueError, ValidationError, IntegrityError):
            return False

    def flush(self) -> None:
        connected = self.bus.connected.is_set()
        MQTT_CONNECTED.set(int(connected))
        now = time.time()
        with self.db.session() as s:
            s.merge(Heartbeat(name="worker", updated_at=now, ready=connected))
            s.execute(
                update(DeviceCommand)
                .where(
                    DeviceCommand.state == "queued",
                    DeviceCommand.expires_at < now,
                )
                .values(state="expired")
            )
        for _ in range(100):
            try:
                topic, raw, retained = self.bus.inbox.get_nowait()
            except queue.Empty:
                break
            self.receive(topic, raw, retained)
        if not connected:
            return
        with self.db.session() as s:
            rows = list(
                s.scalars(
                    select(Outbox).where(~Outbox.delivered).order_by(Outbox.created_at).limit(50)
                )
            )
        for row in rows:
            if row.payload["type"] == "modem.diagnostics.request":
                with self.db.session() as s:
                    command = s.get(DeviceCommand, row.payload["payload"]["commandId"])
                    if command is None or command.state != "queued":
                        s.execute(
                            update(Outbox)
                            .where(Outbox.message_id == row.message_id)
                            .values(delivered=True)
                        )
                        continue
            try:
                self.bus.publish(row.topic, row.payload)
            except (ConnectionError, RuntimeError):
                break
            with self.db.session() as s:
                s.execute(
                    update(Outbox).where(Outbox.message_id == row.message_id).values(delivered=True)
                )


def is_ready(db: Database) -> bool:
    with db.session() as s:
        heartbeat = s.get(Heartbeat, "worker")
        return bool(heartbeat and heartbeat.ready and time.time() - heartbeat.updated_at < 45)
