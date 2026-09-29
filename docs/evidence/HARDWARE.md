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
