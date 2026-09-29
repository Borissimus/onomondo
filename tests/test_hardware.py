"""Opt-in read-only hardware test. Ordinary CI never opens a real serial port."""

import json
import os
import time

import pytest
from sgp32.mqtt import BackendMessaging
from sgp32_agent.commands import CommandStore, Dispatcher
from sgp32_agent.modem import ModemDiagnostics
from sgp32_agent.serial_transport import SerialTransport

from tests.conftest import EID
from tests.test_mqtt import broker, receive, wait_for  # noqa: F401


def test_live_uart_through_local_mqtt(broker, backend, db, tmp_path):  # noqa: F811
    port = os.environ.get("TEST_HARDWARE_PORT")
    if not port:
        pytest.skip("Set TEST_HARDWARE_PORT explicitly for read-only UART/MQTTS test")
    factory, _, _ = broker
    api, _, _ = backend
    api.post("/api/v1/euiccs/sync")
    api.post("/api/v1/devices", json={"device_id": "device-a", "name": "Hardware test", "eid": EID})
    backend_bus, agent_bus = factory("backend"), factory("device-a")
    messaging = BackendMessaging(db, backend_bus)
    agent_bus.subscribe("devices/device-a/commands")
    time.sleep(0.2)
    transport = SerialTransport(port)
    try:
        dispatcher = Dispatcher(
            "device-a", ModemDiagnostics(transport), CommandStore(tmp_path / "hardware-ids.db")
        )
        command_id = api.post("/api/v1/devices/device-a/commands/diagnostics").json()["commandId"]
        messaging.flush()
        message, retained = receive(agent_bus, "devices/device-a/commands")
        result = dispatcher.dispatch(
            "devices/device-a/commands", json.dumps(message).encode(), retained
        )
        assert result["payload"]["simReady"], "SIM not ready"
        assert result["payload"]["status"] == "succeeded", "Hardware diagnostics failed"
        agent_bus.publish(f"devices/device-a/commands/{command_id}/result", result)
        wait_for(lambda: not backend_bus.inbox.empty())
        messaging.flush()
        stored = api.get("/api/v1/device-commands/" + command_id).json()
        assert stored["state"] == "succeeded"
        # Only normalized sanitized evidence is printed under pytest -s.
        print(json.dumps(stored["result"], indent=2))
    finally:
        transport.close()
