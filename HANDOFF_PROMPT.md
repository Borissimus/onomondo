# Implementation-agent prompt

You are implementing a specification-first Python prototype located at:

`/Users/bnykytiuk/projects/onomondo`

The approved specification baseline is committed on `main`. All implementation
must be performed on the existing `feature/sgp32-prototype` branch. Before any
edit, verify the current branch and working-tree status. Do not implement directly
on `main`, rewrite the baseline commit, force-push, or merge the branch yourself.

Before changing anything, read completely and follow, in order:

1. `README.md`
2. `docs/SPEC.md`
3. `docs/ARCHITECTURE.md`
4. `docs/SECURITY.md`
5. `docs/API_CONTRACT.md`
6. `docs/MQTT_CONTRACT.md`
7. `docs/TEST_PLAN.md`
8. `docs/IMPLEMENTATION_PLAN.md`

Implement milestones 0 through 4. Do not implement profile download, profile
enable, disable, delete, eIM configuration, raw APDU or default SM-DP+ changes
unless the user separately authorizes milestone 5.

Important constraints:

- Never open, print, modify, copy, commit or automatically import `api-keys.md`.
- Never put the Onomondo API key in source, tests, MQTT, device-agent config,
  fixtures, logs or command output. Define a documented VPS secret-loading path.
- The device agent must not possess the Onomondo API key.
- Do not invent Onomondo beta endpoints. Confirm them from authenticated API docs
  or restrict implementation to the guide-confirmed endpoints:
  `GET /api/euicc`, `POST /api/orders/psmo`, and
  `GET /api/orders/psmo/{resourceId}` with `listProfileInfo`.
- `done` is not equivalent to success; inspect `outcome`.
- Poll no faster than 10 seconds, default 15 seconds, and honor `Retry-After`.
- A7670E SGP.32 delivery is handled by the eUICC IPAe through proactive SIM
  Toolkit/BIP. Do not implement SGP.32 in AT commands or manually answer `+MSTK`.
- Allow only explicit diagnostic commands over MQTT; no remote raw AT console.
- Use safe mocks and a pseudo-terminal modem in automated tests.
- Preserve existing user files and unrelated changes.
- Make focused, reviewable commits on `feature/sgp32-prototype`; never commit
  secrets, runtime databases, generated credentials or private certificates.

Expected technology choices unless discovery provides a strong documented reason
to change them:

- Python 3.12+
- FastAPI and Pydantic
- `httpx` for Onomondo HTTP
- SQLAlchemy plus Alembic
- PostgreSQL on VPS, SQLite in tests/local development
- `paho-mqtt` or `gmqtt`
- `pyserial`
- pytest, Ruff and a static type checker
- Mosquitto with TLS and per-device ACLs
- systemd services or Docker Compose, with secrets mounted separately

Deliverables:

- working backend and device-agent source;
- dependency and developer setup;
- database migrations;
- fake Onomondo service tests;
- pseudo-terminal modem tests;
- MQTT TLS/ACL configuration and tests;
- VPS deployment templates without secrets;
- operator and hardware runbooks;
- `.env.example` files containing placeholders only;
- `IMPLEMENTATION_REPORT.md` mapping each acceptance criterion to evidence,
  commands run, remaining limitations and deferred work.

Work incrementally. Run appropriate tests after each milestone. Do not claim live
hardware or live Onomondo success unless you actually performed it and recorded
sanitized evidence. Stop and report a blocker if exact authenticated API behavior
is required but credentials or user interaction are unavailable; continue all
mocked and independent work first.
