import logging
import queue
import re
import threading
import time
from collections.abc import Callable
from typing import Any

from sgp32_common.messages import MAX_PAYLOAD, encode
from sgp32_common.mqtt import MqttSettings

from sgp32_agent.serial_transport import ModemError, SerialTransport


class ModemMqttBus:
    """SIMCom A76XX MQTT/TLS adapter using the modem UART as the network path."""

    def __init__(
        self,
        transport: SerialTransport,
        settings: MqttSettings,
        client_id: str,
        will: tuple[str, dict[str, Any]] | None = None,
        on_urc: Callable[[str], None] | None = None,
    ):
        self.transport = transport
        self.settings = settings
        self.client_id = client_id
        self.will = will
        self.on_urc = on_urc or (lambda _: None)
        self.inbox: queue.Queue[tuple[str, bytes, bool]] = queue.Queue(maxsize=256)
        self.connected = threading.Event()
        self.subscriptions: set[str] = set()
        self._results: queue.Queue[str] = queue.Queue(maxsize=256)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._rx_topic: str | None = None
        self._rx_payload: bytes | None = None
        self._rx_next: str | None = None
        self._lock = threading.Lock()

    def handle_urc(self, line: str) -> None:
        if line.startswith("+CMQTT"):
            try:
                self._results.put_nowait(line)
            except queue.Full:
                pass
            if line.startswith("+CMQTTRXTOPIC:"):
                self._rx_next = "topic"
            elif line.startswith("+CMQTTRXPAYLOAD:"):
                self._rx_next = "payload"
            elif line.startswith("+CMQTTRXEND:"):
                if self._rx_topic is not None and self._rx_payload is not None:
                    try:
                        self.inbox.put_nowait((self._rx_topic, self._rx_payload, False))
                    except queue.Full:
                        pass
                self._rx_topic = None
                self._rx_payload = None
                self._rx_next = None
            elif line.startswith(("+CMQTTDISC:", "+CMQTTCONNLOST:")):
                self.connected.clear()
            return
        if self._rx_next == "topic":
            self._rx_topic = line
            self._rx_next = None
            return
        if self._rx_next == "payload":
            self._rx_payload = line.encode()
            self._rx_next = None
            return
        self.on_urc(line)

    @staticmethod
    def _quoted(value: str) -> str:
        if not value or len(value) > 256 or re.search(r'["\r\n]', value):
            raise ValueError("invalid_modem_mqtt_value")
        return value

    def _discard_results(self) -> None:
        while not self._results.empty():
            self._results.get_nowait()

    def _wait_result(self, prefix: str, timeout: float = 30) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self._results.get(timeout=max(0.05, deadline - time.monotonic()))
            except queue.Empty:
                break
            if line.startswith(prefix):
                fields = [part.strip() for part in line.partition(":")[2].split(",")]
                if not fields or fields[-1] != "0":
                    raise ConnectionError("modem_mqtt_operation_failed")
                return line
        raise ConnectionError("modem_mqtt_operation_timeout")

    def _command_result(self, command: str, prefix: str, timeout: float = 30) -> None:
        self._discard_results()
        self.transport.send_command(command, timeout=timeout)
        self._wait_result(prefix, timeout)

    def _upload_ca(self) -> str:
        data = self.settings.ca.read_bytes()
        if not data.startswith(b"-----BEGIN CERTIFICATE-----") or len(data) > 10240:
            raise ValueError("invalid_mqtt_ca")
        name = "sgp32_ca.pem"
        try:
            self.transport.send_command(f'AT+CCERTDELE="{name}"')
        except ModemError:
            pass
        self.transport.send_data_command(f'AT+CCERTDOWN="{name}",{len(data)}', data)
        return name

    def _connect(self) -> None:
        assert self.settings.password
        host = self._quoted(self.settings.host)
        username = self._quoted(self.settings.username)
        password = self._quoted(self.settings.password.get_secret_value())
        client_id = self._quoted(self.client_id)
        try:
            self.transport.send_command("AT+CMQTTSTOP", timeout=15)
        except ModemError:
            pass
        self._command_result("AT+CMQTTSTART", "+CMQTTSTART:", 30)
        ca_name = self._upload_ca()
        for command in (
            'AT+CSSLCFG="sslversion",0,3',
            'AT+CSSLCFG="authmode",0,1',
            'AT+CSSLCFG="enableSNI",0,1',
            f'AT+CSSLCFG="cacert",0,"{ca_name}"',
            f'AT+CMQTTACCQ=0,"{client_id}",1',
            "AT+CMQTTSSLCFG=0,0",
        ):
            self.transport.send_command(command, timeout=15)
        if self.will:
            will_topic, will_payload = self.will[0].encode(), encode(self.will[1])
            self.transport.send_data_command(
                f"AT+CMQTTWILLTOPIC=0,{len(will_topic)}", will_topic
            )
            self.transport.send_data_command(
                f"AT+CMQTTWILLMSG=0,{len(will_payload)},1", will_payload
            )
        self._command_result(
            f'AT+CMQTTCONNECT=0,"tcp://{host}:{self.settings.port}",30,1,'
            f'"{username}","{password}"',
            "+CMQTTCONNECT:",
            60,
        )
        for topic in sorted(self.subscriptions):
            self._subscribe(topic)
        self.connected.set()
        logging.info("modem_mqtt_connected")

    def _run(self) -> None:
        delay = 1
        while not self._stop.is_set():
            if self.connected.is_set():
                self._stop.wait(1)
                continue
            try:
                with self._lock:
                    self._connect()
                delay = 1
            except (ConnectionError, ModemError, OSError, ValueError):
                self.connected.clear()
                logging.warning("modem_mqtt_connect_failed")
                self._stop.wait(delay)
                delay = min(delay * 2, 60)

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            if self.connected.is_set():
                try:
                    self.transport.send_command("AT+CMQTTDISC=0,60", timeout=15)
                    self.transport.send_command("AT+CMQTTREL=0", timeout=15)
                    self.transport.send_command("AT+CMQTTSTOP", timeout=15)
                except ModemError:
                    pass
        self.connected.clear()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def _subscribe(self, topic: str) -> None:
        raw = topic.encode()
        if not raw or len(raw) > 1024:
            raise ValueError("invalid_mqtt_topic")
        self.transport.send_data_command(f"AT+CMQTTSUBTOPIC=0,{len(raw)},1", raw)
        self._command_result("AT+CMQTTSUB=0", "+CMQTTSUB:", 30)

    def subscribe(self, topic: str) -> None:
        self.subscriptions.add(topic)
        if self.connected.is_set():
            with self._lock:
                self._subscribe(topic)

    def publish(self, topic: str, payload: dict[str, Any], *, retain: bool = False) -> None:
        if not self.connected.is_set():
            raise ConnectionError("mqtt_unavailable")
        topic_raw, payload_raw = topic.encode(), encode(payload)
        if len(topic_raw) > 1024 or len(payload_raw) > min(MAX_PAYLOAD, 10240):
            raise ValueError("payload_too_large")
        with self._lock:
            self.transport.send_data_command(f"AT+CMQTTTOPIC=0,{len(topic_raw)}", topic_raw)
            self.transport.send_data_command(
                f"AT+CMQTTPAYLOAD=0,{len(payload_raw)}", payload_raw
            )
            self._command_result(
                f"AT+CMQTTPUB=0,1,60,{int(retain)},0", "+CMQTTPUB:", 60
            )
