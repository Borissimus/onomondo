# Implementation plan

## Milestone 0: discovery

- Confirm that the repository baseline is committed on `main` and that all
  implementation work is performed on `feature/sgp32-prototype`.
- Do not implement directly on `main`; preserve the specification baseline so
  the implementation can be reviewed as a branch diff.
- Record Python/tool versions.
- Authenticate to Onomondo API docs without logging the key.
- Save a sanitized OpenAPI schema or document confirmed endpoints and payloads.
- Inspect A7670E firmware identity and supported AT commands.
- Decide PPP versus modem-native MQTT for the hardware transport test. Keep the
  abstract message-bus interface independent of this choice.

## Milestone 1: repository and quality baseline

- Python package layout for backend and device agent.
- Locked dependencies and reproducible setup.
- Ruff, type checking, pytest and pre-commit configuration.
- Configuration validation and secret-redaction tests.
- CI that uses only mocks/simulators and no production credentials.

## Milestone 2: read-only backend

- Database models and migrations.
- Authenticated management API.
- Typed Onomondo client for eUICC list and profile-info order.
- Durable poller with explicit state machine.
- Audit log and structured logging.
- Fake-Onomondo integration tests.

## Milestone 3: MQTT and device simulator

- Mosquitto TLS/ACL development deployment.
- Backend MQTT publisher/subscriber.
- Device agent running initially against a simulated modem.
- Presence, diagnostic command and result flows.
- Replay, expiry and ACL tests.

## Milestone 4: A7670E hardware

- Serial AT engine.
- A7670E diagnostic adapter.
- Real MQTTS transport through the modem, preferring PPP for the first complete
  Python proof and preserving an adapter for modem-native MQTT later.
- Hardware runbook and captured sanitized evidence.

## Milestone 5: controlled profile changes

- Implement download and enable only after confirming beta API schemas.
- Policy checks, confirmation and rollback by default.
- Pre/post device diagnostics and recovery state.
- Hardware tests on a designated eUICC.

## Definition of done per milestone

- Code and docs updated.
- Automated tests pass.
- No secrets committed or logged.
- Known limitations recorded.
- Implementation report maps completed work to acceptance criteria.
- Changes are committed in focused, reviewable commits on the implementation
  branch, with no secrets or generated runtime data tracked by Git.
