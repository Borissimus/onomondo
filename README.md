# Onomondo SGP.32 IoT management prototype

This repository is a specification-first prototype for securely managing Onomondo
SGP.32 eUICCs from a VPS while communicating with an IoT device over MQTT over
TLS. It intentionally contains no Onomondo API key and no production credentials.

## Components

- `backend/`: VPS management API, Onomondo client, order poller, policy engine,
  audit log, database integration, and MQTT integration.
- `device-agent/`: Python reference implementation for a PC connected to a
  SIMCom A7670E over UART. It is designed to be portable to an MCU later.
- `deploy/`: VPS deployment templates for systemd or Docker Compose, Mosquitto,
  and a TLS reverse proxy.
- `docs/`: normative project specifications and test plans.

## Authoritative documents

Read these in order before implementation:

1. [`docs/SPEC.md`](docs/SPEC.md)
2. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
3. [`docs/SECURITY.md`](docs/SECURITY.md)
4. [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md)
5. [`docs/MQTT_CONTRACT.md`](docs/MQTT_CONTRACT.md)
6. [`docs/TEST_PLAN.md`](docs/TEST_PLAN.md)
7. [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md)

`HANDOFF_PROMPT.md` contains a ready-to-use prompt for the implementation agent.

## Confirmed hardware and service facts

- Physical Onomondo SGP.32 test eUICCs contain an IPAe preconfigured for the
  Onomondo eIM.
- `listProfileInfo` has completed successfully through a SIMCom A7670E.
- The modem exposed proactive SIM Toolkit/BIP activity through `+MSTK` and PDP
  activation/deactivation URCs.
- The IPAe contacted `esipa.esim-iot.onomondo.com` over port 443.
- Onomondo orders are asynchronous and must be polled; `done` does not imply
  success, so `outcome` must always be inspected.

## Non-goals for the first release

- Implementing SGP.32/ES10b or raw APDU profile management in the device agent.
- Storing an organization-wide Onomondo API key on an IoT device.
- Profile deletion, eIM replacement, or default SM-DP+ modification.
- A production-ready graphical UI.

