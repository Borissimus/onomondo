# A7670E read-only hardware runbook

## UART and diagnostics

Use the correct UART voltage, common ground and a stable power supply. The tested
FTDI UART is `/dev/cu.usbserial-A50285BI`, 115200 8N1, with SIMCom A7670E-FASE,
firmware A011B07A7670M7_F. Port names vary across hosts. Check that no terminal or
another process owns the port. The pyserial transport requests exclusive access.

Start with AT, ATI, AT+CGMR, AT+CPIN?, AT+CEREG?, AT+CGATT?, AT+CSQ.
The adapter uses **AT+CICCID** for this firmware; AT+ICCID returned ERROR in the
actual bench test. Its responses still use the +ICCID prefix. IMSI is not queried
by default. AT+COPS?, AT+CGDCONT? and AT+CGPADDR provide operator/PDP/IP diagnostics.
Unsupported optional queries are reported as errors rather than fabricated data.

The reader captures +CEREG/+CGEV/+MSTK asynchronously. It never sends a +MSTK
answer, SIM Toolkit response or APDU. Opaque +MSTK content is redacted. An AT
timeout prevents another command until its delayed final response arrives; a
serial disconnect is closed and reopened on a later collection. If a modem never
finishes an AT command, investigate/restart the agent or modem locally instead
of repeatedly transmitting commands into an unknown transaction.

Provision a distinct device MQTT credential and only its ACL subtree. Configure
`device-agent/.env.example` values in the process environment or systemd
EnvironmentFile; **do not** copy the backend environment to the device. Verify
TLS hostname/CA, then start `sgp32-agent`. The reference simulator is opt-in via
`AGENT_SIMULATE=true`; turn it off for hardware evidence.

An opt-in automated check is:

```sh
TEST_HARDWARE_PORT=/dev/cu.usbserial-A50285BI uv run pytest tests/test_hardware.py -q -s
```

It uses the real A7670E but a local generated-CA Mosquitto and fake inventory.
It proves REST-command correlation, UART collection, MQTTS result transport and
persistence. It does **not** prove MQTTS traversed the modem's cellular network.
See `docs/evidence/HARDWARE.md` for the recorded outcome.

## PPP transport proof on Linux

The initial production transport is host PPP plus the same Python TLS MQTT bus.
`deploy/ppp/onomondo.peer` and `onomondo.chat` are templates, not auto-run scripts.
Validate APN and the CID before changing a PDP context. Adjust the APN in the
chat template to the one approved for the test SIM. Dialing PPP changes data
connectivity but does not perform an SGP.32 profile operation.

1. Identify the modem's USB serial interfaces and supported PPP data port from its
   firmware manual. Use a separate diagnostic AT port. A single FTDI UART cannot
   run PPP and diagnostics simultaneously; do not let both processes open it.
   If only one UART exists, use separate pre/post diagnostics with the agent
   stopped during PPP, or attach the module's supported USB data interface.
2. Install pppd and chat. Copy reviewed templates to root-owned files under
   `/etc/ppp`, adjusting the data port/APN. Run `pppd file /etc/ppp/onomondo.peer`.
3. Confirm `ppp0` has an IP address. Resolve the public MQTT broker hostname via
   your trusted DNS. Add a **specific broker host route** to ppp0; the supplied
   template deliberately does not replace the host default route or DNS.
4. Verify `ip route get <broker-ip>` selects ppp0. Record a sanitized route output
   and interface byte counters before and after a correlated diagnostics command.
5. Keep certificate verification enabled. Establish MQTTS to the public VPS and
   confirm its online status, commandId result, TLS session and post-command
   counter change. Only this route/counter evidence supports a modem-carried MQTT
   claim. Remove the specific test route and stop PPP when finished.

Do not use a local-only broker for the cellular proof; its address must be
reachable from the modem's data network. Do not silently fall back to Wi-Fi.
## Modem-native MQTT transport

Set `AGENT_NETWORK_TRANSPORT=modem` to use the implemented A76XX native MQTT/TLS
adapter. It uploads the deployment CA with `CCERTDOWN`, enforces TLS 1.2, server
authentication and SNI, and executes `CMQTT*` operations over the same serialized
UART used for diagnostics. No PPP interface or host route is involved. The
adapter uses bounded JSON payloads and never exposes a remote raw-AT interface.

Validate a new firmware revision with the `=?` forms of `CMQTTACCQ`,
`CMQTTCONNECT`, `CMQTTSSLCFG`, `CMQTTSUBTOPIC`, `CMQTTSUB`, `CMQTTTOPIC`,
`CMQTTPAYLOAD`, `CMQTTPUB`, `CSSLCFG` and `CCERTDOWN` before enabling it. The
tested firmware is recorded in `docs/evidence/HARDWARE.md`.

## Live read-only Onomondo proof

After authenticated beta schema verification and VPS secret provisioning:

1. Synchronize the five registered test EIDs and bind the correct hardware EID.
   Never derive EID from ICCID.
2. Capture pre-operation diagnostics through MQTT.
3. Request profile refresh and record sanitized local operation/resource IDs.
4. Observe new/work/done and validate listProfileInfoResult.finalResult explicitly.
5. Confirm the enabled Onomondo operational profile in the protected DB/API.
6. Capture post-operation diagnostics. IPAe delivery and application connectivity
   are separate evidence: a successful AT/MQTT check alone proves neither.

No profile download/enable/disable/delete, eIM configuration, SM-DP+ modification
or raw APDU is part of this runbook. Milestone 5 needs separate user permission.
