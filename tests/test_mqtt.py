import json
import os
import queue
import shutil
import socket
import ssl
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest
from sgp32.mqtt import BackendMessaging
from sgp32.storage import DeviceCommand, Observation
from sgp32.worker import Worker
from sgp32_agent.commands import CommandStore, Dispatcher
from sgp32_agent.modem import ModemDiagnostics, SimulatedModem
from sgp32_agent.serial_transport import SerialTransport
from sgp32_common.messages import now_iso
from sgp32_common.mqtt import MqttBus, MqttSettings
from sqlalchemy import select

from tests.conftest import EID, SUCCESS
from tests.test_agent import PTYModem


def wait_for(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition timeout")


def receive(bus, topic, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            actual, raw, retained = bus.inbox.get(timeout=0.1)
            if actual == topic:
                return json.loads(raw), retained
        except queue.Empty:
            pass
    raise AssertionError("message timeout for " + topic)


@pytest.fixture(scope="module")
def broker(tmp_path_factory):
    mosquitto = shutil.which("mosquitto")
    passwd = shutil.which("mosquitto_passwd")
    if not mosquitto or not passwd or not shutil.which("openssl"):
        pytest.skip("Local Mosquitto, mosquitto_passwd and openssl required")
    root = tmp_path_factory.mktemp("mqtt-tls")

    def run(*args):
        subprocess.run(args, cwd=root, check=True, capture_output=True)

    run(
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "1",
        "-keyout",
        "ca.key",
        "-out",
        "ca.crt",
        "-subj",
        "/CN=SGP32 test CA",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
        "-addext",
        "basicConstraints=critical,CA:TRUE",
    )
    run(
        "openssl",
        "req",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        "server.key",
        "-out",
        "server.csr",
        "-subj",
        "/CN=localhost",
    )
    (root / "extensions").write_text("subjectAltName=DNS:localhost\nextendedKeyUsage=serverAuth\n")
    run(
        "openssl",
        "x509",
        "-req",
        "-in",
        "server.csr",
        "-CA",
        "ca.crt",
        "-CAkey",
        "ca.key",
        "-CAcreateserial",
        "-days",
        "1",
        "-extfile",
        "extensions",
        "-out",
        "server.crt",
    )
    passwords = {name: str(uuid4()) for name in ("backend", "device-a", "device-b")}
    password_file = root / "passwords"
    password_file.write_text("".join(f"{user}:{value}\n" for user, value in passwords.items()))
    run(passwd, "-U", str(password_file))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = Path("deploy/mosquitto/mosquitto.conf").read_text()
    config = config.replace("listener 8883", f"listener {port} 127.0.0.1")
    config = config.replace("/mosquitto/secrets/", str(root) + "/")
    config = config.replace("/mosquitto/config/acl", str(Path("deploy/mosquitto/acl").resolve()))
    config = config.replace("persistence true", "persistence false")
    config = config.replace("persistence_location /mosquitto/data/", "")
    if os.getuid() == 0:
        config += "\nuser root\n"
    (root / "mosquitto.conf").write_text(config)
    process = subprocess.Popen(
        [mosquitto, "-c", str(root / "mosquitto.conf")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    def listening():
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            assert process.poll() is None, "Mosquitto startup failed"
            return False

    wait_for(listening)
    clients = []

    def factory(user, *, host="localhost", password=None, will=None, start=True):
        settings = MqttSettings(
            host=host,
            port=port,
            ca=root / "ca.crt",
            username=user,
            password=password or passwords[user],
        )
        bus = MqttBus(settings, "test-" + str(uuid4()), will)
        clients.append(bus)
        if start:
            bus.start()
            wait_for(bus.connected.is_set)
        return bus

    yield factory, root, port
    for bus in clients:
        bus.stop()
    process.terminate()
    process.wait(timeout=5)


@pytest.mark.broker
def test_tls_ca_hostname_and_authentication(broker):
    factory, root, port = broker
    context = ssl.create_default_context(cafile=str(root / "ca.crt"))
    with socket.create_connection(("127.0.0.1", port)) as raw:
        with pytest.raises(ssl.SSLCertVerificationError):
            context.wrap_socket(raw, server_hostname="127.0.0.1")
    context = ssl.create_default_context()
    with socket.create_connection(("127.0.0.1", port)) as raw:
        with pytest.raises(ssl.SSLCertVerificationError):
            context.wrap_socket(raw, server_hostname="localhost")
    bad = factory("device-a", password="wrong-test-password", start=False)
    rejected = []
    bad.client.on_connect = lambda client, userdata, flags, reason, properties: rejected.append(
        reason
    )
    bad.client.connect("localhost", port, 30)
    bad.client.loop_start()
    wait_for(lambda: bool(rejected))
    assert rejected[0].is_failure
    assert not bad.connected.is_set()
    bad.stop()
    anonymous = factory("device-a", start=False)
    anonymous.client.username_pw_set(None)
    rejected.clear()
    anonymous.client.on_connect = lambda client, userdata, flags, reason, properties: (
        rejected.append(reason)
    )
    anonymous.start()
    wait_for(lambda: bool(rejected))
    assert rejected[0].is_failure
    anonymous.stop()


@pytest.mark.broker
def test_acl_isolation_and_lwt(broker):
    factory, _, _ = broker
    backend, second = factory("backend"), factory("device-b")
    offline = {
        "schemaVersion": 1,
        "state": "offline",
        "sessionId": str(uuid4()),
        "timestamp": now_iso(),
    }
    first = factory("device-a", will=("devices/device-a/status", offline))
    backend.subscribe("devices/+/status")
    first.subscribe("devices/device-b/commands")
    second.subscribe("devices/device-b/commands")
    time.sleep(0.2)
    first.publish("devices/device-a/status", {**offline, "state": "online"}, retain=True)
    assert receive(backend, "devices/device-a/status")[0]["state"] == "online"
    first.publish("devices/device-b/status", {**offline, "state": "online"})
    with pytest.raises(AssertionError, match="message timeout"):
        receive(backend, "devices/device-b/status", 0.3)
    backend.publish("devices/device-b/commands", {"probe": True})
    assert receive(second, "devices/device-b/commands")[0] == {"probe": True}
    with pytest.raises(AssertionError, match="message timeout"):
        receive(first, "devices/device-b/commands", 0.3)
    first.client._reconnect_on_failure = False
    sock = first.client.socket()
    sock.shutdown(socket.SHUT_RDWR)
    sock.close()
    assert receive(backend, "devices/device-a/status")[0]["state"] == "offline"
    observer = factory("backend")
    observer.subscribe("devices/device-a/status")
    value, retained = receive(observer, "devices/device-a/status")
    assert value["state"] == "offline" and retained


@pytest.mark.broker
def test_rest_mqtt_pty_result_and_operation_loop(broker, backend, db, settings, tmp_path):
    factory, _, _ = broker
    client, fake, upstream = backend
    client.post("/api/v1/euiccs/sync")
    client.post("/api/v1/devices", json={"device_id": "device-a", "name": "A", "eid": EID})
    backend_bus, agent_bus = factory("backend"), factory("device-a")
    messaging = BackendMessaging(db, backend_bus)
    agent_bus.subscribe("devices/device-a/commands")
    agent_bus.subscribe("devices/device-a/esim/operations/+")
    time.sleep(0.2)
    simulated = SimulatedModem()

    def respond(modem, value):
        modem.respond("\r\n" + "\r\n".join(simulated.send_command(value)) + "\r\nOK\r\n")

    pty_modem = PTYModem(respond)
    transport = SerialTransport(pty_modem.path)
    dispatcher = Dispatcher(
        "device-a", ModemDiagnostics(transport), CommandStore(tmp_path / "dedupe.db")
    )
    try:
        response = client.post("/api/v1/devices/device-a/commands/diagnostics")
        command_id = response.json()["commandId"]
        messaging.flush()
        message, retained = receive(agent_bus, "devices/device-a/commands")
        result = dispatcher.dispatch(
            "devices/device-a/commands", json.dumps(message).encode(), retained
        )
        topic = f"devices/device-a/commands/{command_id}/result"
        agent_bus.publish(topic, result)
        wait_for(lambda: not backend_bus.inbox.empty())
        messaging.flush()
        stored = client.get("/api/v1/device-commands/" + command_id).json()
        assert stored["state"] == "succeeded"
        assert stored["result"]["registration"] == "registered_roaming"
        duplicate = dispatcher.dispatch("devices/device-a/commands", json.dumps(message).encode())
        assert duplicate["payload"]["reason"] == "duplicate"
        agent_bus.publish(topic, duplicate)
        wait_for(lambda: not backend_bus.inbox.empty())
        messaging.flush()
        with db.session() as s:
            assert s.get(DeviceCommand, command_id).state == "succeeded"
            assert len(list(s.scalars(select(Observation)))) == 1
        op = client.post(f"/api/v1/euiccs/{EID}/profiles/refresh").json()["operationId"]
        fake.states = [SUCCESS]
        worker, now = Worker(db, upstream, settings), time.time()
        worker.tick(now)
        worker.tick(now + 15)
        messaging.flush()
        topic = f"devices/device-a/esim/operations/{op}"
        assert receive(agent_bus, topic)[0]["payload"]["state"] == "queued"
        assert receive(agent_bus, topic)[0]["payload"]["state"] == "succeeded"
        assert not messaging.receive(
            "devices/device-b/commands/" + command_id + "/result", json.dumps(result).encode()
        )
        assert not messaging.receive("devices/device-a/telemetry/modem", b"x" * 32769)
        assert not messaging.receive("devices/device-a/status", b"{bad")
    finally:
        transport.close()
        pty_modem.close()


@pytest.mark.broker
def test_payload_size_is_bounded(broker):
    factory, _, _ = broker
    agent = factory("device-a")
    with pytest.raises(ValueError, match="payload_too_large"):
        agent.publish("devices/device-a/telemetry/modem", {"data": "x" * 32769})
