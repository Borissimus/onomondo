import json
import os
import pty
import select
import threading
import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sgp32_agent.commands import CommandStore, Dispatcher
from sgp32_agent.modem import ModemDiagnostics, SimulatedModem
from sgp32_agent.serial_transport import ModemError, SerialTransport
from sgp32_common.messages import envelope


class CountingModem(ModemDiagnostics):
    def __init__(self):
        super().__init__(SimulatedModem())
        self.calls = 0

    def collect(self, include_sensitive=False):
        self.calls += 1
        return super().collect(include_sensitive)


def command(now=None):
    now = time.time() if now is None else now
    return envelope(
        "modem.diagnostics.request",
        {
            "commandId": str(uuid4()),
            "createdAt": datetime.fromtimestamp(now, UTC).isoformat(),
            "expiresAt": datetime.fromtimestamp(now + 300, UTC).isoformat(),
            "includeSensitiveIdentifiers": False,
        },
        str(uuid4()),
    )


def test_persistent_replay_and_expiry(tmp_path):
    modem = CountingModem()
    path = tmp_path / "ids.db"
    dispatch = Dispatcher("device-a", modem, CommandStore(path))
    value = command()
    raw = json.dumps(value).encode()
    result = dispatch.dispatch("devices/device-a/commands", raw)
    assert result["payload"]["status"] == "succeeded"
    restarted = Dispatcher("device-a", modem, CommandStore(path))
    assert restarted.dispatch("devices/device-a/commands", raw)["payload"]["reason"] == "duplicate"
    expired = command(time.time() - 301)
    assert (
        dispatch.dispatch("devices/device-a/commands", json.dumps(expired).encode())["payload"][
            "reason"
        ]
        == "expired"
    )
    assert modem.calls == 1
    assert "8945000000000000001" not in json.dumps(result)
    assert "001010123456789" not in json.dumps(result)


def test_unauthorized_malformed_retained_and_raw_at(tmp_path):
    modem = CountingModem()
    dispatch = Dispatcher("device-a", modem, CommandStore(tmp_path / "ids.db"))
    value = command()
    raw = json.dumps(value).encode()
    assert dispatch.dispatch("devices/device-b/commands", raw) is None
    for raw_bad in (b"bad", b" " * 32769, b"[]"):
        assert dispatch.dispatch("devices/device-a/commands", raw_bad) is None
    assert (
        dispatch.dispatch("devices/device-a/commands", raw, True)["payload"]["reason"]
        == "retained_command"
    )
    value["payload"]["rawAT"] = "AT+CSIM=4"
    assert dispatch.dispatch("devices/device-a/commands", json.dumps(value).encode()) is None
    del value["payload"]["rawAT"]
    value["payload"]["includeSensitiveIdentifiers"] = True
    assert (
        dispatch.dispatch("devices/device-a/commands", json.dumps(value).encode())["payload"][
            "reason"
        ]
        == "sensitive_identifiers_not_authorized"
    )
    assert modem.calls == 0


class PTYModem:
    def __init__(self, responder):
        self.master, self.slave = pty.openpty()
        self.path = os.ttyname(self.slave)
        self.commands = []
        self.responder = responder
        self.stopped = False
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self):
        buffer = b""
        try:
            while not self.stopped:
                if not select.select([self.master], [], [], 0.1)[0]:
                    continue
                buffer += os.read(self.master, 1024)
                while b"\r" in buffer:
                    raw, _, buffer = buffer.partition(b"\r")
                    value = raw.decode().strip()
                    if not value:
                        continue
                    self.commands.append(value)
                    self.responder(self, value)
        except OSError:
            pass

    def respond(self, value):
        os.write(self.master, value.encode())

    def close(self):
        self.stopped = True
        self.thread.join(0.5)
        os.close(self.master)
        os.close(self.slave)
        self.thread.join(0.3)


@pytest.fixture
def pty_modem():
    def respond(modem, value):
        if value == "AT+SLOW":
            time.sleep(0.3)
            modem.respond("\r\nLATE\r\nOK\r\n")
        elif value == "AT+FAIL":
            modem.respond("\r\nERROR\r\n")
        else:
            modem.respond(
                value + "\r\nline one\r\n+CEREG: 5\r\n+MSTK: "
                "001010123456789\r\n+CGEV: NW PDN ACT 1\r\nline two\r\nOK\r\n"
            )

    modem = PTYModem(respond)
    yield modem
    modem.close()


def test_pty_multiline_urc_error_timeout_and_reopen(pty_modem):
    urcs = []
    transport = SerialTransport(pty_modem.path, on_urc=urcs.append)
    try:
        assert transport.send_command("ATI") == ["line one", "line two"]
        assert any("+MSTK:" in value for value in urcs)
        assert "001010123456789" not in str(urcs)
        with pytest.raises(ModemError, match="at_error"):
            transport.send_command("AT+FAIL")
        with pytest.raises(ModemError, match="at_timeout"):
            transport.send_command("AT+SLOW", timeout=0.1)
        with pytest.raises(ModemError, match="modem_resynchronizing"):
            transport.send_command("AT")
        time.sleep(2.1)
        assert transport.send_command("ATI") == ["line one", "line two"]
        assert not any(value.startswith("AT+MSTK") for value in pty_modem.commands)
    finally:
        transport.close()


def test_pty_disconnect_reconnect(tmp_path):
    one = PTYModem(lambda modem, value: modem.respond("\r\nOK\r\n"))
    link = tmp_path / "modem"
    link.symlink_to(one.path)
    transport = SerialTransport(str(link))
    assert transport.send_command("AT") == []
    one.close()
    time.sleep(0.2)
    transport.close()
    two = PTYModem(lambda modem, value: modem.respond("\r\nRESTORED\r\nOK\r\n"))
    link.unlink()
    link.symlink_to(two.path)
    try:
        assert transport.send_command("ATI") == ["RESTORED"]
    finally:
        transport.close()
        two.close()


def test_pty_diagnostics():
    simulated = SimulatedModem()

    def respond(modem, value):
        modem.respond("\r\n" + "\r\n".join(simulated.send_command(value)) + "\r\nOK\r\n")

    pseudo = PTYModem(respond)
    transport = SerialTransport(pseudo.path)
    try:
        result = ModemDiagnostics(transport).collect()
        assert result["registration"] == "registered_roaming"
        assert result["packetAttached"] is True
        assert result["signal"]["csq"] == 24
        assert result["ipAddresses"] == ["192.0.2.2"]
        assert "AT+CIMI" not in pseudo.commands
    finally:
        transport.close()
        pseudo.close()
