import queue
import ssl
import threading
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from sgp32_common.messages import MAX_PAYLOAD, encode
from sgp32_common.security import register_secret


class MqttSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MQTT_", env_file=None, hide_input_in_errors=True, extra="ignore"
    )
    host: str
    port: int = Field(default=8883, ge=1, le=65535)
    ca: Path
    username: str
    password: SecretStr | None = None
    password_file: Path | None = None

    @model_validator(mode="after")
    def validate_secret(self) -> "MqttSettings":
        if self.password_file:
            path = self.password_file.resolve()
            if path.name == "api-keys.md":
                raise ValueError("Forbidden secret source")
            try:
                self.password = SecretStr(path.read_text().strip())
            except OSError:
                raise ValueError("MQTT secret unavailable") from None
        if not self.password or not self.password.get_secret_value():
            raise ValueError("MQTT password required")
        if not self.ca.is_file() or not self.username or not self.host:
            raise ValueError("MQTT TLS and identity configuration required")
        register_secret(self.password.get_secret_value())
        return self


class MqttBus:
    def __init__(
        self, settings: MqttSettings, client_id: str, will: tuple[str, dict[str, Any]] | None = None
    ):
        self.settings = settings
        self.inbox: queue.Queue[tuple[str, bytes, bool]] = queue.Queue(maxsize=256)
        self.connected = threading.Event()
        self.subscriptions: set[str] = set()
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )
        assert settings.password
        self.client.username_pw_set(settings.username, settings.password.get_secret_value())
        context = ssl.create_default_context(cafile=str(settings.ca))
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        self.client.tls_set_context(context)
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client.max_queued_messages_set(256)
        if will:
            self.client.will_set(will[0], encode(will[1]), qos=1, retain=True)
        self.client.on_connect = self._connect
        self.client.on_disconnect = self._disconnect
        self.client.on_message = self._message

    def _connect(
        self, client: mqtt.Client, userdata: Any, flags: Any, reason: Any, properties: Any
    ) -> None:
        if reason.is_failure:
            self.connected.clear()
            return
        for topic in self.subscriptions:
            client.subscribe(topic, qos=1)
        self.connected.set()

    def _disconnect(
        self, client: mqtt.Client, userdata: Any, flags: Any, reason: Any, properties: Any
    ) -> None:
        self.connected.clear()

    def _message(self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        if len(message.payload) > MAX_PAYLOAD:
            return
        try:
            self.inbox.put_nowait((message.topic, message.payload, message.retain))
        except queue.Full:
            # Drop bounded telemetry; command QoS retry is protected by the durable command store.
            pass

    def start(self) -> None:
        self.client.connect_async(self.settings.host, self.settings.port, keepalive=30)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.disconnect()
        self.client.loop_stop()

    def subscribe(self, topic: str) -> None:
        self.subscriptions.add(topic)
        if self.connected.is_set():
            self.client.subscribe(topic, qos=1)

    def publish(self, topic: str, payload: dict[str, Any], *, retain: bool = False) -> None:
        if not self.connected.is_set():
            raise ConnectionError("mqtt_unavailable")
        result = self.client.publish(topic, encode(payload), qos=1, retain=retain)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise ConnectionError("mqtt_publish_failed")
        result.wait_for_publish(timeout=5)
        if not result.is_published():
            raise ConnectionError("mqtt_publish_unconfirmed")
