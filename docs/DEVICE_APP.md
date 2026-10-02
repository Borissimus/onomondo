# Device application and MCU porting contract

`sgp32-agent` is the reference device application. It owns the modem UART,
collects bounded read-only diagnostics and exposes the device to the hosted API
through the versioned MQTT contract. It never receives the Onomondo API key and
never accepts arbitrary AT commands from the network.

## Runtime commands

```text
sgp32-agent check        validate configuration, UART access and secret paths
sgp32-agent diagnostics collect one local sanitized observation without MQTT
sgp32-agent run          run the persistent MQTTS command/telemetry loop
```

With no command, `run` remains the default for compatibility. Configuration is
provided only through `AGENT_*` and `MQTT_*` environment variables. Secrets are
read from `MQTT_PASSWORD_FILE`; they are not accepted as command-line arguments.

## Linux reference deployment

Install the project under `/opt/sgp32`, create a non-login `sgp32-agent` user and
put only non-secret settings in `/etc/sgp32-agent/agent.env`. Install the public
CA at `/etc/sgp32-agent/ca.crt` and the device-unique credential at
`/etc/sgp32-agent/secrets/mqtt_password`. Recommended modes are `0444` for the CA,
`0400` for the credential and `0700` for `/var/lib/sgp32-agent`. The supplied
systemd unit retains the agent's primary group, adds `dialout` only for UART
access, validates configuration before start and hardens the process without
blocking its serial device.

An example production environment is:

```text
AGENT_DEVICE_ID=device-b
AGENT_SERIAL_PORT=/dev/ttyUSB2
AGENT_BAUD=115200
AGENT_COMMAND_DB=/var/lib/sgp32-agent/commands.db
AGENT_SIMULATE=false
AGENT_ALLOW_IMSI=false
AGENT_TELEMETRY_INTERVAL=60
MQTT_HOST=sgp32-mqtt.borissimus.top
MQTT_PORT=8883
MQTT_CA=/etc/sgp32-agent/ca.crt
MQTT_USERNAME=device-b
MQTT_PASSWORD_FILE=/etc/sgp32-agent/secrets/mqtt_password
```

Run `sgp32-agent check` as the service user before enabling the unit. A successful
check prints only non-secret booleans and connection metadata. Then enable the
service and follow its structured logs with `journalctl -u sgp32-agent`.

The operator continues to use the hosted REST API. A diagnostics POST is
translated by the worker into a bounded MQTT command; the application executes
the fixed read-only AT sequence and returns the normalized result. SGP.32 profile
orders remain entirely on the VPS/Onomondo path; the device app only receives an
informational operation-status message.

## Portable boundary

The application is deliberately split into these responsibilities:

| Responsibility | Stable core/contract | Linux reference adapter | MCU replacement |
| --- | --- | --- | --- |
| AT request/response | `ModemTransport` | `SerialTransport`/pyserial | MCU UART driver and bounded parser |
| Normalization | `ModemDiagnostics` | shared directly | port the deterministic AT-to-result mapping |
| Commands and replay | `Dispatcher`, MQTT contract | SQLite `CommandStore` | flash/NVS command-id journal |
| Messaging | topics and JSON envelopes | Paho `MqttBus` | embedded TLS/MQTT client |
| Credentials | unique device identity | protected files | secure storage or secure element |
| Hosting | lifecycle contract | systemd | RTOS task/watchdog |

The backend API, MQTT topics and schema version are platform-independent. An MCU
port must implement the adapters; it must not reproduce the VPS REST API or store
the Onomondo key.

## MCU invariants

- One unique credential per device; never a fleet-wide password.
- TLS 1.2 or newer, certificate hostname verification and the pinned deployment CA.
- Bounded 32 KiB MQTT payloads and bounded UART lines/buffers.
- QoS 1 for commands/results, non-retained commands and retained presence only.
- Reject expired, future, duplicate, malformed and retained commands.
- No remote raw-AT/APDU interface and no automatic SIM Toolkit responses.
- ICCID/IMSI remain redacted from telemetry; IMSI collection is disabled by default.
- Persist processed command IDs across reboot and use a watchdog around UART/network tasks.
- Store the private credential in protected storage; enable secure boot and signed updates.

An MCU port is conformant when the existing MQTT integration and command-contract
test vectors pass against it, followed by the same hosted `device-b` end-to-end
test. Cellular routing is a separate transport proof: the Linux reference may use
host networking, PPP or a future modem-native MQTT adapter without changing the
application contract.
