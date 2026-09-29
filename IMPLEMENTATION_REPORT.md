# Implementation report — 2026-09-29

## Follow-up: local Docker deployment and authorized live inventory

The findings below supersede the initial report's Docker/PostgreSQL/credential
availability limitations. The original milestone report is preserved afterwards
as the record of that earlier verification stage.

* Docker Desktop was started; Docker Engine 29.7.2 and Compose 5.5.0 are available.
  The local stack is running under project `sgp32-local`, using a Python 3.12.14
  image and PostgreSQL 17. No VPS service or other Docker project was changed.
* Added `deploy/local/compose.yaml`, durable HTTPS fake Onomondo, a credential/TLS
  preparation helper, startup migration dependencies, simulator, and a TCP gateway.
  Published ports are loopback-only: API TLS 8443, MQTT TLS 8883, development DB 15432.
  Application containers have no Internet egress. API/worker/agent run as 10001:10001.
* The Dockerfile now prepares an owned writable state directory for named volumes;
  the application root filesystem remains read-only. Local secret mounts grant
  individual generated files only to the services that need them.
* Authenticated HTTPS API → TLS MQTT → simulated device → PostgreSQL passed,
  followed by the fake upstream new/work/done → persisted profile cycle.
  The same diagnostic flow passed with the **real A7670E on the host** through
  Docker MQTT/PostgreSQL. The temporary host hardware agent was stopped afterwards;
  the Docker simulator remains running. This still does not prove PPP/LTE routing.
* Fixed a deployment-discovered status bug: same-session MQTT heartbeats now
  preserve the modem's diagnostic connectivity state; reconnects reset it pending
  new diagnostics. A regression test covers heartbeat, reconnect and offline states.
* Final suite with real UART and a separately created disposable PostgreSQL DB:
  **45 passed, no skips**, 13.07 seconds. The disposable test DB was removed; the
  demo database/volumes and hardware observations were preserved. Ruff, formatting
  and mypy pass; type checking now covers 20 files including local deployment tools.
* The user subsequently explicitly authorized reading `api-keys.md` for a test,
  and prohibited undisclosed copying. A host Python process read the device-labelled
  credential **into memory only**. It was not copied into a different secret file,
  process environment, image, container, fixture, commit, log or terminal output.
  The original file was not modified. Generated `mock_api_key` and TLS `api.key`
  are unrelated local development credentials.
* Live `GET /api/euicc` returned HTTP 200 with **five actual eUICCs**. The same
  inventory was synchronized through the authenticated management backend into
  protected `.local/sgp32/live-inventory.db`, separate from the synthetic Docker DB.
  The key is absent from that database. This supplies live evidence for inventory
  acceptance criterion 3; the exact secret was never included in the evidence.
* Updated the adapter for the observed `{data: [{eid_value: ...}], pagination: ...}`
  response. Association tokens/signing fields are omitted from stored inventory
  metadata. Multi-page results fail explicitly until the pagination request
  contract is confirmed. Tests cover this shape and metadata omission.
* The application's own API-docs link returns 401 with the working API Bearer key.
  The live PSMO submission/outcome contract remains unconfirmed, so **no live PSMO
  POST was sent**. The running Docker stack uses fake credentials and synthetic
  profile operations exclusively. The default production schema gate remains false.

Access and repeatable commands are in [LOCAL_DOCKER.md](docs/LOCAL_DOCKER.md).
API docs: https://localhost:8443/api/v1/docs; user `admin`; generated password in
ignored `.local/sgp32/operator_password`; local CA `.local/sgp32/ca.crt`.
No system/browser CA trust was installed automatically.

Additional checks: local Compose config, repeated startup preserving credentials,
actual PostgreSQL Alembic/worker concurrency test, local Docker smoke, real-device
Docker smoke, runtime non-root identity, and safe log/secret-boundary checks.
The previous Starlette/httpx test-client deprecation warning remains non-failing.

## Original milestone implementation report

Implementation and all commits are on `feature/sgp32-prototype`. `main` remains
at the original specification baseline
`2bbe87873e1b031a01adc4c202f0c1be954c6e96`. No merge, force-push or deployment was
performed. `api-keys.md` was not opened, copied, imported or committed. Milestone
5 and destructive SGP.32 operations were not implemented or executed.

## Outcome and limits

The read-only software is implemented: authenticated FastAPI backend,
SQLAlchemy/Alembic persistence, isolated Onomondo adapter, durable poller,
idempotency, audit records, MQTT outbox/consumer, TLS/ACL deployment, persistent
command replay protection, serial/URC engine, A7670E adapter and runbooks.

**40 tests passed, 1 skipped** in the final full run with the physical modem
explicitly enabled. Ruff, formatting, mypy, migrations, packaging and Compose
configuration validation passed. The only skipped test needs a disposable
PostgreSQL server; none is running locally and the Docker daemon is unavailable.
The CI workflow provisions PostgreSQL and runs that test, but CI itself was not
executed from this session.

Real A7670E diagnostics completed through an actual local TLS Mosquitto and back
into the database. This does not establish that MQTT used the cellular channel.
Authenticated beta schema capture and live Onomondo requests were not possible:
no permitted secret source/schema was supplied. Live requests fail closed behind
`ONOMONDO_SCHEMA_CONFIRMED=false`. Consequently milestones 0 and 4 retain the
live-validation blockers below; claiming all live MVP acceptance criteria met
would be incorrect.

## Milestone mapping

| Milestone | Delivered evidence | Remaining boundary |
| --- | --- | --- |
| 0 — discovery | All eight normative documents/HANDOFF read; clean branch/baseline verified; versions recorded; real firmware and diagnostic command support observed; host PPP selected, MessageBus abstraction preserved. See `docs/DISCOVERY.md` and hardware evidence. | Authenticated hosted beta JSON schema still required. Public connectivity/upstream eIM docs were not treated as equivalent. |
| 1 — baseline | `pyproject.toml`, `uv.lock`, three packages, configuration validation/redaction, Ruff/mypy/pytest/pre-commit, mock-only CI on Python 3.12/3.14. Wheel and source distribution built. | Local execution was Python 3.14.7; Python 3.12 is configured in CI, not claimed as run here. |
| 2 — backend | All MVP management routes; Basic/Argon2id authentication including docs; common errors/correlation; models and two migrations; worker leases; unknown-submission reconciliation; explicit success normalization; fake HTTPS server and MockTransport tests. | Hosted JSON contract is deliberately provisional and gated. Real five-card inventory and profile outcome not verified. PostgreSQL execution deferred to available server/CI. |
| 3 — MQTT/simulator | Real Mosquitto TLS tests, two isolated identities, CA/hostname/auth failures, Last Will retention, diagnostic command/result and operation-status flow, persistent replay store, malformed/oversized/expired rejection. | Local deployment tested. Public VPS TLS deployment remains an operator action. |
| 4 — A7670E | Real UART discovery, serialized AT engine, independent redacted URCs, PTY timeout/disconnect/reconnect tests, normalized diagnostics, actual REST→MQTTS→UART→MQTTS→DB test. PPP and service templates/runbook supplied. | Public broker/CA/credential and appropriate PPP data interface needed for modem-carried MQTTS proof. PPP was not started and host routes were not changed. |

## SPEC.md acceptance criteria

| # | Criterion | Evidence / status |
| --- | --- | --- |
| 1 | Clean deployment without secret output | Locked build, secret-safe startup entry points, config/redaction tests, `.dockerignore`, secret-only mounts, Compose parse, migrations and generated-artifact filename audit. Actual clean VPS/container deployment was **not run** because the Docker daemon is unavailable. |
| 2 | Operator authentication enforced | `test_auth_idempotency_conflict_and_no_destructive_routes`; all management/docs/metrics endpoints use Basic authentication and Argon2id verification; only health is public. Production TLS proxy required. |
| 3 | Five registered test eUICCs | `test_success_flow` synchronizes **five synthetic fixtures** and returns them through the API. Actual organization inventory **blocked pending live schema/secret source**. |
| 4 | Local operation plus resourceId | Fake HTTPS server integration and success flow verify 202/local UUID, one list-only POST and persisted upstream resource ID. No live service claim. |
| 5 | new/work/done and outcome semantics | Success, procedureError, absent, missing/invalid outcomes, malformed JSON, schema drift, 429/Retry-After, GET 502/backoff/retry limit, ambiguous POST, interrupted submission, deadline and reconciliation tests. |
| 6 | Installed Onomondo profile persisted/returned | Fixture profile is stored and read with unchanged conventional ICCID and observed timestamp. Real installed profile **not yet queried**. Unknown ICCID encodings fail safely. |
| 7 | A7670E reports diagnostics over MQTTS | `test_live_uart_through_local_mqtt`: real A7670E, real local TLS broker, authenticated API, correlated result and database observation. `docs/evidence/HARDWARE.md` records sanitized firmware/network evidence. **Cellular MQTT path unverified**. |
| 8 | Expired/repeated commands rejected | Agent command-store tests cover expiry, restart-persistent deduplication, retained/future/unauthorized command handling; real QoS 1 duplicate delivery does not re-execute diagnostics. |
| 9 | Device topic isolation | `test_acl_isolation_and_lwt` proves device-a cannot publish device-b status or receive device-b commands while device-b can. Broker anonymous/wrong-password and TLS CA/hostname failures are also exercised. |
| 10 | Test plan | Final available suite: **40 passed, 1 PostgreSQL skip**; static checks and migration drift check pass. PTY/fake-HTTP/real-broker/manual-hardware layers are distinguished, and no destructive test was run. |

## Implementation decisions

* Only `GET /api/euicc`, `POST /api/orders/psmo` and
  `GET /api/orders/psmo/{resourceId}` exist in the upstream adapter. The only
  submitted order is listProfileInfo. No raw upstream-payload API or remote AT
  console exists.
* Mock JSON assumptions are documented in `docs/DISCOVERY.md`; the exact hosted
  request/response shape must be confirmed before enabling real HTTP calls.
* Worker state is committed as `submitting` **before** POST. A crash, transport
  failure, malformed successful response or upstream 5xx cannot cause an automatic
  repeat POST. Unknown/time-out operations retain the EID lock. Reconciliation is
  GET-only and checks the resource binding.
* DB uniqueness permits one active operation per EID, including refreshes. This
  is intentionally stricter than just serializing profile-changing operations.
  Atomic conditional leases prevent duplicate polling/submission by workers.
* Polling is minimum 10/default 15 seconds. Retry-After is persisted into the next
  schedule; operation deadline still takes precedence without another HTTP call.
  GET retries are bounded and jittered. Inventory GET uses a separate bounded
  retry budget. Redirects are disabled and HTTPS/CA validation remain enabled.
* Results/profiles/outbox/audit are committed together. MQTT outbox publication
  waits for PUBACK; a crash can cause safe QoS 1 redelivery. Command deduplication
  is persistent and claims an ID before execution.
* The MQTT command includes explicit payload `createdAt` in addition to envelope
  timestamp, resolving the SECURITY.md requirement missing from the sample JSON.
  Identifiers remain masked over MQTT even when local IMSI collection is enabled.
* UART timeout blocks subsequent commands until the delayed final response is
  drained. URCs are independently routed. +MSTK content is masked and never
  answered or decoded as SGP.32. Real firmware required `AT+CICCID`, not AT+ICCID.
* Systemd and Compose keep backend secrets separate from device settings.
  Database URL files, operator Argon2id hash files and MQTT password files are
  supported. Runtime protected DB fields retain necessary EID/ICCID data;
  ordinary logs and MQTT are sanitized.
* Worker metrics are on loopback 9100; API metrics have a separate process
  registry. Readiness checks migration access, recent worker/MQTT heartbeat and
  the beta-schema gate. It does not issue paid/live upstream requests.

## Commands and results

Commands below use `uv`; in this workspace the installed executable is
`.venv/bin/uv`.

| Command / check | Result |
| --- | --- |
| `git branch --show-current`, `git status --short`, `git show-ref --heads` | Existing feature branch, initially clean, both heads initially at baseline. |
| `uv lock`, `uv sync --locked` | Reproducible lock generated and installed. |
| `uv run ruff check .` | Pass. |
| `uv run ruff format --check .` / pre-commit format hook | Pass. |
| `uv run mypy` | Pass, 18 source files. |
| `TEST_HARDWARE_PORT=/dev/cu.usbserial-A50285BI uv run pytest -q` | **40 passed, 1 skipped**, approximately 13 seconds. Skip: disposable PostgreSQL URL unavailable. |
| `uv run pytest tests/test_mqtt.py -x -q` | 4 real-broker tests passed; included again in full suite. |
| `TEST_HARDWARE_PORT=... uv run pytest tests/test_hardware.py -q -s` | 1 passed; sanitized evidence committed. |
| `uv run alembic upgrade head`, `uv run alembic check` | Pass, no model/migration drift. |
| Migration round-trip test | SQLite upgrade → downgrade → upgrade → check passed. |
| `DATABASE_URL=postgresql+psycopg://localhost/sgp32 uv run alembic upgrade head --sql` | PostgreSQL SQL generation passed; this is **not** a live DB test. |
| `uv build` | Source distribution and wheel built successfully. |
| Artifact filename audit | Both archives exclude forbidden secret/runtime filenames. Archive contents of any forbidden file were not read. |
| `docker compose -f deploy/compose.yaml config --no-env-resolution --no-interpolate -q` | Pass without resolving/printing secret environments. |
| `docker info` | No daemon socket; container build/deployment/live PostgreSQL test unavailable. |
| `uv run pre-commit run --all-files` | Ruff check, Ruff format and mypy hooks passed. |
| `git diff --check` | Pass. |

Tools: Python 3.14.7, uv 0.12.20, pytest 9.1.1, Ruff 0.16.9, mypy 1.20.2,
Mosquitto 2.1.2, OpenSSL 3.6.3. Exact Python dependency versions are in `uv.lock`.

One non-failing dependency warning remains: the installed Starlette test client
warns that its httpx integration is deprecated in favor of httpx2. The requested
httpx stack currently works; no warning suppression was added.

Intermediate issues were corrected before final verification: an editable build
made before package files existed, a forbidden-file test's validation order,
PTY close behavior on macOS, strict TLS test-CA keyUsage requirements, fake HTTPS
server reverse-DNS lookup, and the observed AT+CICCID firmware command. TLS
verification was never weakened to make tests pass.

## Remaining work, permissions and limitations

1. Supply a **separate approved secret-file path** and authenticated sanitized
   beta guide/schema; do not provide the key in chat or use `api-keys.md`.
   Validate/update the adapter shape before setting the live gate. Then run the
   read-only five-card inventory/profile acceptance sequence.
2. Supply a public broker endpoint, matching CA and device MQTT secret file.
   Confirm a supported PPP data interface distinct from the diagnostic UART, or
   schedule exclusive use of the single UART. Execute the runbook's explicit
   broker route/counter check before claiming cellular MQTTS.
3. Run the PostgreSQL integration test on a disposable server and the deployment
   smoke checks on the target Linux VPS. The CI job is supplied, not claimed run.
4. After an agent crash following command-ID claim, diagnostics are not repeated;
   the operator may need to request a new command to obtain a result. MQTT uses
   clean sessions, so commands to an offline subscriber can expire without
   execution. Outbox PUBACK confirms broker acceptance, not modem execution.
5. The prototype is not a hardened production service: OIDC, credential issuance,
   backup/retention automation, HA MQTT/worker deployment and a graphical UI are
   outside this scope. Secrets/DB backups require deployment access controls.
6. Milestone 5, profile download/enable/disable/delete, eIM changes, default SM-DP+
   changes and raw APDU remain absent. Separate explicit authorization is required.

Focused commits preceding this report:

* `7d9ed7e` discovery boundaries;
* `ef7955a` package/lock/security baseline;
* `ed44bf6` authenticated backend, schema and durable orders;
* `9f4eb32` MQTT isolation and serial agent;
* `28a5056` deployment, resilience refinements, runbooks and hardware evidence.
