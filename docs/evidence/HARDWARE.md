# Sanitized A7670E evidence — 2026-09-29

Direct read-only UART check on `/dev/cu.usbserial-A50285BI`, 115200 8N1.
No competing process owned the port. No profile-changing or SIM Toolkit reply
commands were sent. Full IMEI was masked before output.

```text
AT -> OK
ATI -> Manufacturer: INCORPORATED; Model: A7670E-FASE
       Revision: A7670M7_V1.11.1; IMEI: [REDACTED]
AT+CGMR -> +CGMR: A011B07A7670M7_F
AT+CPIN? -> +CPIN: READY
AT+CEREG? -> +CEREG: 0,5
AT+CGATT? -> +CGATT: 1
AT+CSQ -> +CSQ: 24,99
```

This proves UART, SIM readiness, roaming registration and packet attachment.
It does not prove PPP, modem-carried MQTT, IPAe delivery or a live profile refresh.

## Implemented adapter and local-MQTTS round trip

The first adapter run found `AT+ICCID -> ERROR`. Read-only `AT+CICCID` succeeded
and returned `+ICCID: [REDACTED]`. The adapter was corrected to this observed
firmware command. It queries neither IMSI nor EID by default.

Executed:

```sh
TEST_HARDWARE_PORT=/dev/cu.usbserial-A50285BI uv run pytest tests/test_hardware.py -q -s
```

Result: **1 passed**. The test generated temporary TLS credentials, launched a
local Mosquitto with the repository ACL, authenticated two MQTT clients, created
a diagnostics command through the management API, collected from the real UART,
returned the correlated MQTT result and verified persistence through the API.
Sanitized observed result:

```json
{
  "status": "succeeded",
  "registration": "registered_roaming",
  "operator": {"mcc": "255", "mnc": "06"},
  "packetAttached": true,
  "signal": {"csq": 24},
  "simReady": true,
  "errors": [],
  "identity": "A7670E-FASE / A7670M7_V1.11.1 / IMEI [REDACTED]",
  "identifiers": {"iccid": "[REDACTED]", "imsi": "[REDACTED]"}
}
```

An IP address and PDP context were returned by the modem; the address is omitted
from this committed evidence as unnecessary operational data. No raw credentials,
ICCID, IMSI, EID, private certificate or runtime database are included.

Only the FTDI UART is available for this check. PPP was not started and host
routes were not changed. The broker was local, so this proves real hardware
**diagnostics over host MQTTS**, not modem-carried MQTTS. Live beta Onomondo
inventory/order processing remains untested pending an approved secret source
and authenticated JSON schema verification.

## Hosted read-only SGP.32 validation — 2026-09-30

An operator-provisioned, ignored runtime secret was mounted only into the local
API and worker containers. The separate `sgp32-live` project used its own
PostgreSQL/MQTT volumes; mock Onomondo and the simulated device were disabled.
No credential value was printed, copied into an image or exposed to the device
agent.

Observed sanitized sequence:

```text
GET hosted inventory -> 5 registered eUICCs
device-b UART precheck -> SIM ready, registered roaming, packet attached, CSQ 28
local operation -> created -> queued -> succeeded
hosted resource -> 01a0f224-83a3-730a-bcdc-13c5ca481c1d
profile inventory -> 1 operational Onomondo profile, enabled, ICCID ***623769
```

The operation targeted EID `***876379` and was limited to `listProfileInfo`.
The hosted result matched
`listProfileInfoResult.finalResult=successResult/profileInfoList` and was stored
in the live PostgreSQL database. No download, enable, disable, delete, eIM,
SM-DP+, raw APDU or SIM Toolkit reply operation was performed.

This confirms the full hosted read-only control path through Onomondo eIM and the
IPAe/eUICC. Device-agent MQTT still used the Mac network path; PPP/modem-carried
MQTTS remains a separate unproven transport milestone.

## Modem-native MQTTS validation — 2026-10-05

The production broker CA was regenerated with strict CA extensions after Python
correctly rejected the original certificate for missing `keyCertSign`. The
A7670E firmware reported support for the required `CMQTT*`, `CSSLCFG` and
`CCERTDOWN` commands. The agent was then started with
`AGENT_NETWORK_TRANSPORT=modem`; host PPP and host MQTT were not used.

Observed sanitized result:

```text
A7670E firmware -> A011B07A7670M7_F
modem TLS/MQTT -> connected to sgp32-mqtt.borissimus.top:8883
VPS presence -> device-b data_connected
periodic UART telemetry -> succeeded, registered_roaming, packetAttached=true, CSQ 28
VPS diagnostics command -> de569cb2-5117-47c1-93a0-fd826a564105
modem-carried command/result -> succeeded, registered_roaming, packetAttached=true
```

The command was created in the protected backend for transport validation,
published by the worker, received through the modem-native subscribed topic,
executed over the same serialized UART and returned through modem-native MQTTS.
The backend persisted the correlated result. No identifier or credential is
included in this evidence. This proves bidirectional application traffic through
the Onomondo SIM data path without relying on the Mac network transport.
