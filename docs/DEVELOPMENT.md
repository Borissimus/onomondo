# Developer setup

Requires Python 3.12+, uv 0.12.20, and (for TLS integration tests) Mosquitto,
mosquitto_passwd and OpenSSL. Work on `feature/sgp32-prototype` only.

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q
uv run pre-commit install
```

The wheel installs `sgp32`, `sgp32_agent` and `sgp32_common` from their backend,
device-agent and shared directories. The device code imports no backend module.
`uv.lock` pins runtime and development dependencies for supported Python versions.
CI tests Python 3.12/3.14, a real local Mosquitto, fake HTTP service, PTY modem and
PostgreSQL. No CI secret is needed; test identities are generated in temporary
folders, not committed. The PostgreSQL service has a disposable CI-only password.

## Test modes

* Default pytest: unit, SQLite/Alembic, fake HTTPS Onomondo, PTY, local TLS broker.
  Broker tests skip explicitly if Mosquitto/OpenSSL are not installed.
* `TEST_POSTGRES_URL=... uv run pytest tests/test_postgres.py -q`: uses an empty,
  **disposable** PostgreSQL database. Never point this at an operator database.
* `TEST_HARDWARE_PORT=/dev/cu.usbserial-... uv run pytest tests/test_hardware.py -q -s`:
  opt-in read-only physical modem diagnostics transported over a **local** broker.
  Onomondo is still fake in this test, and MQTT uses the host's local network.

Test fixtures run actual Alembic migrations; application startup never silently
creates tables. Use `DATABASE_URL=sqlite:///./data/sgp32.db uv run alembic upgrade head`
for local development. Restrict `data/` to your user and do not commit it.

The full API test client uses injected `httpx.MockTransport`. A second integration
test runs a genuine HTTPS fake server and verifies its Bearer header, paths,
submission body, polling transitions and persisted outcome. The production client
never follows redirects and never enables plaintext HTTP.

No fake mode is silently activated on API or modem failure. Agent simulation must
be explicitly enabled with `AGENT_SIMULATE=true`. Hosted beta JSON is provisional
until an operator confirms it; see DISCOVERY.md. CI success is not beta API or
modem-carried MQTT evidence.
