# VPS and operator runbook

## Secrets and deployment

Use PostgreSQL on the VPS. Expose only reverse-proxy HTTPS 443 and Mosquitto TLS
8883; keep PostgreSQL and the API listener private. Restrict SSH using your VPN
or firewall. Rotate credentials by replacing secret files and restarting the
relevant services; no migration is required.

`backend/.env.example` and `device-agent/.env.example` are placeholders. No dotenv
file is loaded implicitly. Environment variables or explicitly named secret
files are the only configuration sources. Never use `api-keys.md` as an input.
Do not put credentials in shell command arguments, source, fixture files, logs
or MQTT. Run the services with restrictive umask and database access controls.

For systemd, install code under `/opt/sgp32` and create the `sgp32` service user.
Use root-owned `/etc/sgp32/backend.env`, mode 0600. systemd reads this file before
changing user; it can contain `ONOMONDO_API_KEY`, `OPERATOR_PASSWORD_HASH`,
`DATABASE_URL`, and `MQTT_PASSWORD` directly. Alternatively use the `_FILE`
settings with files readable only by the service user, mode 0400. Root-only secret
files cannot be read by an unprivileged process. Supply an Argon2id hash for the
single administrator, never a plaintext password. Generate it using an interactive
Python `getpass` script and write directly to the protected hash file, not stdout.
Create `/var/lib/sgp32` owned by sgp32 with mode 0700. Install the systemd units.

For containers, copy the placeholder environment file to
`/etc/sgp32/backend.env`; set `DATABASE_URL_FILE=/run/secrets/database_url`.
The database URL secret must use `postgres` as hostname and the password from
`/etc/sgp32/secrets/postgres_password`. Compose mounts all credentials read-only.
The API/worker run as UID/GID 10001:10001; secret files must be readable by that
UID (e.g. owner 10001 mode 0400, parent root-only). Compose local secret mounts
preserve host file permissions. PostgreSQL and Mosquitto have their own service
UIDs: assign permissions appropriate to those containers. Keep their private keys
out of the application container and the device.

Configure the broker certificate SAN for the real MQTT hostname and update both
`MQTT_HOST` and the Compose network alias. Install the matching CA in
`/etc/sgp32/ca.crt`. Replace `api.example.invalid` in the nginx template. Provision
Mosquitto password hashes interactively with `mosquitto_passwd`; give every device
a unique user/password. Each user needs an explicit ACL block for its device ID.
Use the provided backend ACL only for the backend identity. Do not add device
wildcard permissions. Broker secrets live in `/etc/sgp32/mosquitto-secrets/`.

```sh
docker compose -f deploy/compose.yaml build
docker compose -f deploy/compose.yaml run --rm migrate
docker compose -f deploy/compose.yaml up -d
```

For systemd, run `.venv/bin/python -m sgp32.migrate` in the protected service
environment before starting the units. Never paste an environment containing
secrets into a support ticket. Migrations fail with a generic error; inspect DB
access privately if needed. The generic Alembic CLI is for local tests and can
print database-driver diagnostics; use the wrapper for secret-bearing deployments.

No deployment was performed automatically. Docker images are version-tagged;
production operators should pin their reviewed image digests before rollout.
Back up the protected database, configure encrypted storage/backups, and bound
journald/container log retention. Runtime DB fields contain necessary EID/ICCID
values; ordinary logs and MQTT do not.

## Production network layout for borissimus.top

The supplied production Compose file is configured for:

```text
https://sgp32-api.borissimus.top        -> Caddy :443 -> api:8000
mqtts://sgp32-mqtt.borissimus.top:8883 -> Mosquitto :8883
```

Caddy also listens on port 80 only for ACME HTTP challenge and HTTPS redirects.
FastAPI, PostgreSQL, plaintext MQTT and worker metrics have no published host
ports. Caddy obtains and renews the public API certificate automatically. The
MQTT broker uses the private CA/server certificate provisioned under
`/etc/sgp32/mosquitto-secrets`; clients must validate the exact public MQTT
hostname against the certificate SAN.

The backend connects to Mosquitto through the Docker DNS alias
`sgp32-mqtt.borissimus.top`, so the same certificate hostname is validated both
internally and externally. Do not replace this with `mosquitto` while hostname
verification is enabled.

## Confirm the hosted API before live requests

Use the account's authenticated beta docs through the approved secret-loading
path. Do not use connectivity API documentation as a substitute. Compare the
isolated adapter in `backend/sgp32/onomondo.py` with the actual inventory,
listProfileInfo submission, resourceId and outcome formats; update tests if
needed and save a **sanitized** schema. Only then set
`ONOMONDO_SCHEMA_CONFIRMED=true`. This acknowledges schema verification, not a
permission to submit profile-changing operations; none exist in this release.

The key is sent as Bearer only to the configured HTTPS origin. No redirects are
followed. Response bodies are bounded at 1 MiB. Connect and I/O deadlines plus a
stream wall-clock budget bound each call below the 120-second worker lease.
GET polling retries at most six transient failures, with exponential backoff and
jitter, minimum 10/default 15 seconds, honoring Retry-After. POST is never retried
by the worker. Inventory sync is a safe GET with at most three attempts within a 45-second
retry-scheduling budget. A longer Retry-After is returned as an upstream failure
instead of holding an HTTP thread indefinitely; retry that operator sync later.
It does not resubmit a profile order.

## API workflow

Use an HTTPS client with HTTP Basic; do not put a password on the command line.
Interactive `curl --user admin` prompts for a password. All management routes,
Swagger and OpenAPI require authentication. The API echoes a valid UUID
`X-Correlation-ID` or generates one. Health paths are `/api/v1/health/live` and
`/api/v1/health/ready`. Readiness requires migrations, a recent connected worker
heartbeat, and the schema-confirmation flag; it is not a live Onomondo probe.

1. `POST /api/v1/euiccs/sync` and `GET /api/v1/euiccs?limit=50&offset=0`.
2. Bind a known EID with `POST /api/v1/devices` and body
   `{"device_id":"device-a","name":"Bench A7670E","eid":"<32 digits>"}`.
3. `POST /api/v1/devices/device-a/commands/diagnostics`; read
   `/api/v1/device-commands/{commandId}` for the correlated result.
4. `POST /api/v1/euiccs/{eid}/profiles/refresh`; retain the returned `operationId`.
5. Read `/api/v1/operations/{operationId}` and `/api/v1/euiccs/{eid}/profiles`.
   Five eUICCs in test fixtures are synthetic and do not prove live inventory.

Supply a unique `Idempotency-Key` on every mutating call. Same actor, key and
canonical route/body replay the original response. A different request with that
key receives 409. DB uniqueness also serializes active work per EID, including
read-only refreshes. This stricter policy is intentional for the prototype.

A `done` upstream response is successful only for the explicitly recognized
listProfileInfo success outcome. A procedureError, missing result, duplicate
ICCID or unsupported ICCID encoding is a failure. Conventional ICCIDs are kept
in their original order. Empty successful inventories replace old observations.

After a POST timeout/crash, `submission_unknown` retains the EID lock. The
operator must identify the resource through authenticated external docs/support.
`POST /api/v1/operations/{operationId}/reconcile` accepts `{"resourceId":"..."}`
and performs GET only; it verifies the returned resource matches the original
EID and list-only request. With no resource ID it refuses, never resubmits.
Timed-out polling also retains the lock and can be resumed via reconciliation.
Do not edit database locks to bypass an unknown submission without investigation.

## MQTT and monitoring

Commands carry both envelope timestamp and payload `createdAt`, `expiresAt`,
`commandId`; `createdAt` makes the SECURITY.md requirement explicit where the
original MQTT example only showed envelope timestamp. Maximum TTL is five
minutes. Devices reject retained, expired, repeated, wrong-topic, malformed and
non-allowlisted commands. Deduplication survives restart; IDs are removed only
after their expiry plus 24 hours. If an agent crashes after claiming an ID, it
will not repeat the command; request a new diagnostic command to get fresh data.

Only diagnostics are accepted. IMSI collection additionally requires local
`AGENT_ALLOW_IMSI=true`; identifiers remain masked on MQTT. +MSTK is an observed,
redacted URC, never answered. Commands/results/events/status notifications use
QoS 1, presence is retained and includes an offline Will. Outbox delivery can
repeat after a worker crash, which is safe under command-ID deduplication.

The worker exports Prometheus metrics on **loopback-only port 9100**, including
operation count/duration/failures, Onomondo status codes, MQTT connection and
per-device last-seen. API `/api/v1/metrics` exposes only its own process registry;
nginx blocks public metrics access. Scrape the worker locally (inside its network
namespace for containers). Never expose 9100 or PostgreSQL publicly.
