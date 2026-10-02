import argparse
import json
import logging
import os
import queue
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import Field
from pydantic import ValidationError as PydanticValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sgp32_common.messages import envelope, now_iso
from sgp32_common.mqtt import MqttBus, MqttSettings
from sgp32_common.security import configure_logging

from sgp32_agent.commands import CommandStore, Dispatcher
from sgp32_agent.modem import ModemDiagnostics, SimulatedModem
from sgp32_agent.serial_transport import SerialTransport


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENT_", env_file=None, extra="ignore", hide_input_in_errors=True
    )
    device_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    serial_port: str = "/dev/ttyUSB2"
    baud: int = 115200
    command_db: Path = Path("data/agent-commands.db")
    simulate: bool = False
    allow_imsi: bool = False
    telemetry_interval: float = Field(default=60, ge=15)


def _settings() -> tuple[AgentSettings, MqttSettings]:
    agent = AgentSettings()
    mqtt = MqttSettings()
    if mqtt.username != agent.device_id:
        raise ValueError("MQTT identity must match device ID")
    return agent, mqtt


def check(settings: AgentSettings, mqtt_settings: MqttSettings) -> dict[str, Any]:
    """Validate deployment inputs without connecting or printing secrets."""
    serial_ready = settings.simulate or (
        Path(settings.serial_port).exists()
        and os.access(settings.serial_port, os.R_OK | os.W_OK)
    )
    command_parent = settings.command_db.expanduser().resolve().parent
    state_ready = command_parent.is_dir() and os.access(command_parent, os.W_OK)
    status = "ready" if serial_ready and state_ready else "not_ready"
    return {
        "status": status,
        "deviceId": settings.device_id,
        "simulation": settings.simulate,
        "serialReady": serial_ready,
        "stateDirectoryReady": state_ready,
        "mqtt": {
            "host": mqtt_settings.host,
            "port": mqtt_settings.port,
            "identityMatchesDevice": mqtt_settings.username == settings.device_id,
            "caReadable": mqtt_settings.ca.is_file() and os.access(mqtt_settings.ca, os.R_OK),
            "credentialLoaded": bool(mqtt_settings.password),
        },
    }


def diagnostics(settings: AgentSettings) -> dict[str, Any]:
    """Collect one sanitized local modem observation without MQTT."""
    transport = SerialTransport(settings.serial_port, settings.baud)
    modem = ModemDiagnostics(
        SimulatedModem() if settings.simulate else transport,
        allow_imsi=settings.allow_imsi,
    )
    try:
        return modem.collect(False)
    finally:
        transport.close()


def run(settings: AgentSettings, mqtt_settings: MqttSettings) -> None:
    if mqtt_settings.username != settings.device_id:
        raise ValueError("MQTT identity must match device ID")
    session = str(uuid4())
    presence: dict[str, Any] = {
        "schemaVersion": 1,
        "state": "offline",
        "sessionId": session,
        "timestamp": now_iso(),
    }
    root = f"devices/{settings.device_id}"
    bus = MqttBus(mqtt_settings, "agent-" + settings.device_id, (root + "/status", presence))
    urcs: queue.Queue[str] = queue.Queue(maxsize=128)

    def urc(line: str) -> None:
        try:
            urcs.put_nowait(line)
        except queue.Full:
            pass

    transport = SerialTransport(settings.serial_port, settings.baud, urc)
    modem = ModemDiagnostics(
        SimulatedModem() if settings.simulate else transport, allow_imsi=settings.allow_imsi
    )
    dispatcher = Dispatcher(settings.device_id, modem, CommandStore(settings.command_db))
    bus.subscribe(root + "/commands")
    bus.start()
    next_telemetry = 0.0
    online = False
    try:
        while True:
            if not bus.connected.wait(timeout=1):
                online = False
                continue
            if not online:
                try:
                    bus.publish(
                        root + "/status",
                        {**presence, "state": "online", "timestamp": now_iso()},
                        retain=True,
                    )
                    online = True
                except (ConnectionError, RuntimeError):
                    time.sleep(1)
                    continue
            try:
                topic, raw, retained = bus.inbox.get(timeout=0.1)
                result = dispatcher.dispatch(topic, raw, retained)
                if result:
                    bus.publish(
                        root + "/commands/" + result["payload"]["commandId"] + "/result", result
                    )
            except queue.Empty:
                pass
            except (ConnectionError, RuntimeError):
                online = False
            if time.monotonic() >= next_telemetry:
                observation = modem.collect()
                try:
                    bus.publish(
                        root + "/telemetry/modem",
                        envelope(
                            "modem.telemetry",
                            {"commandId": str(uuid4()), **observation},
                            str(uuid4()),
                        ),
                    )
                    bus.publish(
                        root + "/status",
                        {**presence, "state": "online", "timestamp": now_iso()},
                        retain=True,
                    )
                except (ConnectionError, RuntimeError):
                    online = False
                next_telemetry = time.monotonic() + settings.telemetry_interval
            for _ in range(32):
                try:
                    line = urcs.get_nowait()
                    bus.publish(
                        root + "/events/modem",
                        envelope("modem.event", {"line": line}, str(uuid4())),
                    )
                except (queue.Empty, ConnectionError, RuntimeError):
                    break
    finally:
        try:
            if bus.connected.is_set():
                bus.publish(root + "/status", {**presence, "timestamp": now_iso()}, retain=True)
        finally:
            bus.stop()
            transport.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Secure A7670E MQTT device agent")
    parser.add_argument(
        "command",
        choices=("run", "check", "diagnostics"),
        nargs="?",
        default="run",
        help="run the agent, validate deployment, or collect local sanitized diagnostics",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    configure_logging()
    try:
        command = _parser().parse_args(argv).command
        if command == "diagnostics":
            result = diagnostics(AgentSettings())
            print(json.dumps(result, separators=(",", ":"), sort_keys=True))
            if result["status"] != "succeeded":
                raise SystemExit(1)
            return
        agent, mqtt = _settings()
        if command == "check":
            result = check(agent, mqtt)
            print(json.dumps(result, separators=(",", ":"), sort_keys=True))
            if result["status"] != "ready":
                raise SystemExit(1)
            return
        run(agent, mqtt)
    except KeyboardInterrupt:
        return
    except (OSError, PydanticValidationError, ValueError):
        logging.error("agent_startup_failed")
        raise SystemExit(2) from None
    except Exception:
        logging.error("agent_stopped_configuration_or_dependency_error")
        raise SystemExit(1) from None
