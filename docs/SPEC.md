# Product specification

## 1. Purpose

Build a testable reference system that demonstrates secure remote management of
Onomondo SGP.32 eUICCs. The privileged Onomondo API integration runs on a VPS.
An IoT-side agent communicates with the VPS using MQTT over TLS and obtains
modem diagnostics through a SIMCom A7670E UART interface.

The prototype must prove the complete control and observation loop without
placing the Onomondo API key on the device:

1. An authenticated operator requests an eUICC operation from the VPS API.
2. The VPS validates policy and creates an asynchronous Onomondo order.
3. A worker polls the order until a terminal state.
4. The eUICC IPAe independently retrieves and executes its signed eIM package.
5. The VPS persists and publishes the result.
6. The device agent reports modem/network state before and after the operation.

## 2. Terminology

- **eUICC**: the secure element commonly called an eSIM.
- **EID**: globally unique identifier of the eUICC.
- **Profile**: operator subscription installed inside the eUICC.
- **ICCID**: identifier of an installed profile/subscription.
- **IPAe**: IoT Profile Assistant implemented inside the eUICC.
- **eIM**: eSIM IoT Remote Manager that prepares signed SGP.32 operations.
- **PSMO**: Profile State Management Operation, such as list or enable.
- **Order**: Onomondo's asynchronous REST representation of requested work.
- **Device agent**: application-side software observing and controlling the modem;
  it does not implement profile management itself.

## 3. Actors

- **Operator**: authenticated human using the management API or CLI.
- **Backend**: trusted VPS service holding the Onomondo API key.
- **Order worker**: background process polling Onomondo orders.
- **MQTT broker**: TLS-secured message transport between backend and devices.
- **Device agent**: Python process initially running on a PC connected over UART.
- **Onomondo platform**: REST API, eIM, ESipa endpoint and connectivity platform.
- **eUICC/IPAe**: physical Onomondo card executing signed packages.

## 4. MVP functional requirements

### 4.1 Device inventory

- Fetch organization eUICCs from `GET /api/euicc`.
- Persist EID and non-secret metadata.
- Allow an administrator to assign an internal `device_id` and human-readable
  name to an EID.
- Never infer EID from ICCID; they identify different objects.

### 4.2 Profile inventory

- Create a `listProfileInfo` PSMO order for one EID.
- Persist Onomondo `resourceId`, request, timestamps, status, complete outcome and
  debug information.
- Poll at 15-second intervals by default.
- Respect HTTP `Retry-After` on `429`.
- Treat `new` and `work` as nonterminal; `done` and `absent` as terminal.
- Treat `done` as successful only when its outcome explicitly contains a success
  result. A `procedureError` is a completed failure.
- Publish normalized operation status to MQTT.

### 4.3 Modem diagnostics

- Open a configured serial port using `pyserial`.
- Execute one AT command at a time with a timeout and transcript sanitization.
- Collect at least: modem identity, SIM readiness, IMSI when explicitly enabled,
  ICCID, operator, registration state, packet attachment, PDP contexts, signal
  quality and IP address where supported.
- Capture relevant unsolicited result codes including `+CEREG`, `+CGEV` and
  `+MSTK` without attempting to decode or answer SGP.32 SIM Toolkit commands.
- Publish normalized diagnostics via MQTT.
- Redact IMSI, ICCID and EID in ordinary logs; full values may be stored only in
  protected database fields required for operation.

### 4.4 Device messaging

- Connect to MQTT through TLS.
- Use unique credentials per device.
- Publish online/offline status using retained state and Last Will.
- Receive diagnostic commands with `commandId`, creation time and expiry time.
- Reject expired, duplicated, unauthorized or malformed commands.
- Return a result correlated by `commandId`.

### 4.5 Operator interface

- Provide a JSON REST API with generated OpenAPI documentation.
- Provide authentication from the first version; a single administrator account
  is acceptable for MVP, but no unauthenticated management endpoint is allowed.
- Provide endpoints described in `API_CONTRACT.md`.
- Return local operation identifiers instead of exposing internal database IDs.

## 5. Phase 2 requirements

Implement only after the read-only path is stable:

- Submit a profile download using an activation code.
- Enable an installed profile using ICCID and `rollback: true` by default.
- Request device diagnostics before the order.
- Verify device connectivity after completion.
- Mark an operation `completed_unverified` when Onomondo succeeds but the device
  does not return post-change connectivity evidence.
- Require an explicit privileged override to disable rollback.

## 6. Operations excluded until separately approved

- Disable profile.
- Delete profile.
- Change associated eIM configuration.
- Change default SM-DP+ address.
- Direct ES10b/APDU operations.
- Automatic profile switching based solely on one failed ping.
- Accept arbitrary ICCID, EID, activation code or raw Onomondo payload from MQTT.

## 7. Onomondo integration rules

- Base URL is configuration, defaulting to
  `https://app.esim-iot.onomondo.com/api`.
- Send API key only as `Authorization: Bearer ...` to the configured HTTPS origin.
- Do not follow cross-origin redirects while carrying authorization.
- Set connect/read/total timeouts and bounded retries.
- Retry safe GET requests on transient errors with exponential backoff and jitter.
- Do not blindly repeat POST requests after an ambiguous network failure. Record
  the state as `submission_unknown` for reconciliation.
- Store raw outcomes for troubleshooting and a normalized result for application
  logic.
- The beta API schema must be captured from authenticated API docs during
  implementation. Unsupported standard SGP.32 operations must not be invented.

## 8. State models

### 8.1 Local operation status

`created -> awaiting_device_precheck -> submitting -> queued -> working ->`
`succeeded | failed | absent | timed_out | submission_unknown`

Phase 2 may add `awaiting_device_postcheck` and `completed_unverified`.

### 8.2 Device status

`unknown | offline | online_unregistered | online_registered | data_connected |`
`degraded`

## 9. Persistence

Minimum entities:

- `devices`: device ID, name, EID, enabled flag, timestamps.
- `euiccs`: EID and Onomondo metadata.
- `profiles`: EID, ICCID, name, provider, class, state, fallback flag, observed time.
- `operations`: local ID, EID, type, requested by, state, resource ID, request,
  raw outcome, normalized error, timestamps.
- `device_commands`: command ID, device ID, type, state, request/result, expiry.
- `device_observations`: registration, operator, signal, data state, timestamp.
- `audit_events`: actor, action, target, decision, correlation ID, timestamp.

SQLite is acceptable for local development. PostgreSQL is preferred on the VPS.
Database migrations are mandatory.

## 10. Configuration

Configuration must come from environment variables or secret files, including:

- `ONOMONDO_API_URL`
- `ONOMONDO_API_KEY` (secret)
- database URL (secret if it embeds credentials)
- MQTT URL, CA path and backend credentials
- operator authentication secret or password hash
- serial device and baud rate for device agent
- log level and polling interval

The program must fail closed when required secrets are absent. It must never print
secret values during startup validation.

## 11. Observability

- Structured JSON logs on VPS.
- Correlation IDs across REST request, local operation, Onomondo resource and MQTT
  command.
- Health endpoints separated into liveness and readiness.
- Metrics for operation counts/duration/failures, Onomondo status codes, MQTT
  connection state and device last-seen time.
- No API key, activation code, bearer token or MQTT password in logs.

## 12. Acceptance criteria

MVP is accepted when:

1. A clean deployment starts without secrets appearing in output.
2. Operator authentication is enforced.
3. Inventory returns the five registered test eUICCs.
4. A profile refresh produces a local operation and Onomondo resource ID.
5. The worker observes `new/work/done` and recognizes success versus
   `procedureError`.
6. The installed Onomondo profile is persisted and returned by the management API.
7. The A7670E device agent reports current network diagnostics over MQTTS.
8. An expired or repeated MQTT command is rejected deterministically.
9. A device credential cannot read or write another device's topics.
10. Unit and integration tests described in `TEST_PLAN.md` pass.

