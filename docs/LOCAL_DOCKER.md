# Local Docker deployment

Run from the repository root on `feature/sgp32-prototype`. This is a separate
Compose project (`sgp32-local`) with its own volumes; it does not use the VPS
Compose environment or any real Onomondo credential.

## Start and verify

Prerequisites: running Docker Desktop/Engine and Compose, Python/uv, OpenSSL and
`mosquitto_passwd` (from the Mosquitto package). On this Mac Docker Desktop is
installed; start it with `open -a Docker` if needed. The existing local uv is
`.venv/bin/uv`; use that path if `uv` is not on your PATH.

```sh
uv sync --locked
uv run python deploy/local/manage.py up
uv run python deploy/local/manage.py smoke
```

`up` generates development credentials/TLS certificates once, builds the image,
starts PostgreSQL and MQTT, runs Alembic migrations to completion, then starts
the worker/API and simulated device. Repeated `up` preserves credentials and
volumes. Incomplete credential directories fail safely rather than being reset.

The smoke check verifies five synthetic eUICCs, a correlated diagnostic command,
and a mock `new → work → done/success` profile refresh persisted in PostgreSQL.
The upstream polling interval remains 15 seconds, so the full check takes about
45 seconds. The host's REST status checks do not trigger extra upstream polls.

## Access

* API docs: **https://localhost:8443/api/v1/docs**
* Health: https://localhost:8443/api/v1/health/ready
* Operator username: `admin`
* Generated operator password: `.local/sgp32/operator_password`
* Local CA: `.local/sgp32/ca.crt`
* MQTT TLS: `localhost:8883`
* PostgreSQL: `localhost:15432` (development only)

The browser does not initially trust this local CA. Import/trust it only in your
chosen local development trust store if you want browser access. No system trust
settings are changed by these scripts. For CLI access, explicitly use the CA:

```sh
curl --cacert .local/sgp32/ca.crt \
  https://localhost:8443/api/v1/health/ready

# Prompts for the generated operator password; do not put it in the command line.
curl --cacert .local/sgp32/ca.crt --user admin \
  https://localhost:8443/api/v1/euiccs
```

All published ports bind only to 127.0.0.1. HAProxy forwards TCP without terminating
TLS. Application services sit on an internal Docker network without Internet
egress; only the fixed-target TCP gateway is on the additional edge network.
Docker internal networks do not publish host ports directly, hence this gateway.

The stack contains API, worker, PostgreSQL, TLS Mosquitto, a durable HTTPS fake
Onomondo service, a simulated device, and the gateway. The migration container
exits successfully after preparing the database. Its exited state is expected.
The fake service rejects real EIDs and every operation except listProfileInfo.
The agent container receives only its MQTT credential and CA.

## Credentials

All generated material lives in ignored `.local/sgp32/`, mode 0700. It is excluded
from the Docker build context. The real `api-keys.md` is not read by these scripts,
not mounted and not copied. `mock_api_key` is freshly generated fake-service
credentials; `api.key` is a generated local TLS private key, not an Onomondo key.

For local Compose's file bind mounts, mounted files have mode 0444 **inside the
private host directory**, allowing container UID 10001 and Mosquitto's own UID to
read their individually granted files. The CA signing key and operator plaintext
password remain 0600 on the host and are never mounted. This is a local Docker
Desktop arrangement, not the recommended VPS ownership configuration.

The backend image uses UID/GID 10001:10001, read-only root filesystem and dropped
Linux capabilities. Persistent agent/mock state uses separate volumes at
`/var/lib/sgp32`; the image creates that directory with the matching owner.
Certificates expire in 30 days. Regeneration is intentionally manual: first stop
services and plan credential/DB-password rotation; do not remove the credential
directory while preserving an initialized PostgreSQL volume with its old password.

## Real UART with the local broker

The simulator is `device-a`. The host hardware agent uses `device-b` so the two
never share MQTT identity or command-ID storage. Docker Desktop does not directly
pass through USB UART; run the physical agent on macOS with the same MQTTS bus.
See [Docker's USB guidance](https://docs.docker.com/desktop/troubleshoot-and-support/faqs/general/).

After a successful initial smoke check has created the device bindings, run in
another terminal:

```sh
uv run python deploy/local/manage.py agent --port /dev/cu.usbserial-A50285BI
```

Then:

```sh
uv run python deploy/local/manage.py smoke --device device-b
```

The host agent is stopped with Ctrl-C. The smoke routine still uses synthetic
inventory and mock profile operations; hardware diagnostics alone come from the
real UART. This is not a PPP/LTE transport proof. The agent gets no backend secret
variables. Real eUICCs, observed during separately authorized inventory validation,
are stored in `.local/sgp32/live-inventory.db`, not mixed into the Docker demo.

## Status and stop

```sh
docker compose -f deploy/local/compose.yaml ps
uv run python deploy/local/manage.py stop
```

Stop preserves all data. Do not use `down -v` unless you deliberately intend to
remove local database/agent/mock history. No other Docker project is touched.

## Real key in production

Provision a dedicated runtime secret outside the checkout/image, for example
`/etc/sgp32/secrets/onomondo_api_key`. Grant read access only to the intended
backend runtime UID (for example owner 10001, mode 0400, protected parent directory).
Compose grants that file only to API/worker as `/run/secrets/onomondo_api_key`;
`ONOMONDO_API_KEY_FILE` contains the path, not the key. Local Compose file secrets
are bind mounts, not automatic encryption at rest. Protect VPS disk/backups and
restrict Docker administration, which can access container secrets.

With systemd, prefer `LoadCredential=onomondo_api_key:/etc/sgp32/secrets/onomondo_api_key`
and `Environment=ONOMONDO_API_KEY_FILE=%d/onomondo_api_key`. The source can remain
root-owned 0600: systemd reads it and makes a protected runtime credential available
to the service. A secret manager can supply the same runtime file interface.

Use distinct development/production credentials and the narrowest service permissions
available. Rotate/revoke without modifying Git or rebuilding images. After replacing a
secret file atomically, recreate the affected Compose API/worker containers so
the bind mount resolves the new file; for systemd credentials, restart the units. The device,
broker and browser never need the organization key. Do not place it in command
arguments, shell history, build arguments or diagnostics. A long-running backend
necessarily holds its credential in memory while making authorized HTTPS requests.

The user authorized a one-off read of `api-keys.md` after the initial handoff. That
key was read into a host Python process and used for HTTPS calls only; no copy was
made into local secret files, container mounts, environment variables or logs.
The running Docker stack still uses the generated mock key. Enabling live container
access requires a separately agreed secret-provisioning step and confirmed beta
order JSON; it is not done implicitly by `up`.

References: [Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/),
[Compose startup dependencies](https://docs.docker.com/compose/how-tos/startup-order/).
