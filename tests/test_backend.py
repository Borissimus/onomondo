import json
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sgp32.onomondo import Onomondo, UpstreamError, normalize, retry_delay
from sgp32.storage import AuditEvent, Operation, Outbox
from sgp32.worker import Worker
from sqlalchemy import select

from tests.conftest import EID, ICCID, SUCCESS


def refresh(backend):
    client, _, _ = backend
    assert client.post("/api/v1/euiccs/sync").json() == {"count": 5}
    assert (
        client.post(
            "/api/v1/devices", json={"device_id": "device-a", "name": "A", "eid": EID}
        ).status_code
        == 201
    )
    response = client.post(
        f"/api/v1/euiccs/{EID}/profiles/refresh", headers={"Idempotency-Key": "refresh-1"}
    )
    assert response.status_code == 202
    return response.json()["operationId"]


def test_success_flow(backend, db, settings):
    op_id = refresh(backend)
    client, fake, upstream = backend
    worker = Worker(db, upstream, settings)
    now = time.time()
    for i in range(4):
        assert worker.tick(now + i * 15)
        assert not worker.tick(now + i * 15 + 1)
    assert client.get(f"/api/v1/operations/{op_id}").json()["state"] == "succeeded"
    profiles = client.get(f"/api/v1/euiccs/{EID}/profiles").json()
    assert profiles["items"][0]["iccid"] == ICCID
    assert profiles["observedAt"]
    assert len(client.get("/api/v1/euiccs").json()["items"]) == 5
    assert json.loads(fake.calls[1].content) == {
        "eidValue": EID,
        "order": {"psmo": [{"listProfileInfo": {}}]},
    }
    with db.session() as s:
        assert (
            s.scalar(select(Outbox).order_by(Outbox.created_at.desc())).payload["payload"]["state"]
            == "succeeded"
        )
        assert len(list(s.scalars(select(AuditEvent)))) >= 7


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ({"status": "done", "outcome": [{"procedureError": {"code": "failed"}}]}, "failed"),
        ({"status": "absent"}, "absent"),
        ({"status": "done"}, "failed"),
        ({"status": "unexpected"}, "failed"),
        (
            {
                "status": "done",
                "outcome": [{"listProfileInfoResult": {"finalResult": "successResult"}}],
            },
            "failed",
        ),
    ],
)
def test_terminal_outcomes(backend, db, settings, outcome, expected):
    op = refresh(backend)
    client, fake, upstream = backend
    fake.states = [outcome]
    worker = Worker(db, upstream, settings)
    now = time.time()
    worker.tick(now)
    worker.tick(now + 15)
    assert client.get(f"/api/v1/operations/{op}").json()["state"] == expected


def test_ambiguous_post_not_repeated(backend, db, settings):
    op = refresh(backend)
    client, fake, upstream = backend
    fake.post_error = httpx.ReadTimeout("unsafe token must not escape")
    worker = Worker(db, upstream, settings)
    worker.tick()
    worker.tick(time.time() + 60)
    response = client.get(f"/api/v1/operations/{op}")
    assert response.json()["state"] == "submission_unknown"
    assert "unsafe token" not in response.text
    assert sum(x.method == "POST" for x in fake.calls) == 1
    assert client.post(f"/api/v1/operations/{op}/reconcile", json={}).status_code == 409
    assert client.post(f"/api/v1/euiccs/{EID}/profiles/refresh").status_code == 409


def test_rate_limit_and_get_retry(backend, db, settings):
    op = refresh(backend)
    client, fake, upstream = backend
    fake.states = [httpx.Response(429, headers={"Retry-After": "90"}), httpx.Response(502), SUCCESS]
    worker, now = Worker(db, upstream, settings), time.time()
    worker.tick(now)
    worker.tick(now + 15)
    assert not worker.tick(now + 104)
    worker.tick(now + 105)
    assert not worker.tick(now + 110)
    worker.tick(now + 150)
    assert client.get(f"/api/v1/operations/{op}").json()["state"] == "succeeded"


def test_auth_idempotency_conflict_and_no_destructive_routes(backend):
    client, _, _ = backend
    for path in ("/api/v1/euiccs", "/api/v1/docs", "/api/v1/openapi.json", "/api/v1/metrics"):
        assert client.get(path, auth=("admin", "wrong")).status_code == 401
    assert client.get("/api/v1/health/live", auth=None).status_code == 200
    op = refresh(backend)
    again = client.post(
        f"/api/v1/euiccs/{EID}/profiles/refresh", headers={"Idempotency-Key": "refresh-1"}
    )
    assert again.json()["operationId"] == op
    assert (
        client.post("/api/v1/euiccs/sync", headers={"Idempotency-Key": "refresh-1"}).status_code
        == 409
    )
    for action in ("download", "delete", "enable", "disable"):
        response = client.post(f"/api/v1/euiccs/{EID}/profiles/{action}")
        assert response.status_code == 404
        assert response.headers["X-Correlation-ID"] == response.json()["error"]["correlationId"]


def test_worker_lease_and_crash_recovery(backend, db, settings):
    op = refresh(backend)
    _, fake, upstream = backend
    worker = Worker(db, upstream, settings)
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: worker.tick(), range(2)))
    assert sum(x.method == "POST" for x in fake.calls) == 1
    with db.session() as s:
        row = s.get(Operation, op)
        row.state, row.resource_id, row.next_poll = "submitting", None, 0
    worker.tick()
    with db.session() as s:
        assert s.get(Operation, op).state == "submission_unknown"
    assert sum(x.method == "POST" for x in fake.calls) == 1


def test_validation_redaction_and_correlation(backend):
    client, _, _ = backend
    result = client.post("/api/v1/devices", json={"name": "Bearer secret-value"})
    assert result.status_code == 422 and "secret-value" not in result.text
    assert "X-Correlation-ID" in result.headers
    assert client.get("/api/v1/health/ready").status_code == 503


def test_outcomes_and_delays():
    state, profiles, _ = normalize(SUCCESS)
    assert state == "succeeded" and profiles[0]["iccid"] == ICCID
    assert retry_delay("91", 0) >= 91
    assert retry_delay("bad", 0, 1) >= 10
    assert retry_delay("Tue, 29 Sep 2099 00:00:00 GMT", 0) > 90


def test_schema_gate_and_redirect(settings):
    with pytest.raises(UpstreamError, match="beta_schema_unconfirmed"):
        Onomondo(settings).inventory()
    calls = []

    def redirect(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://other.invalid/steal"})

    upstream = Onomondo(settings, httpx.MockTransport(redirect))
    with pytest.raises(UpstreamError, match="upstream_rejected"):
        upstream.inventory()
    assert len(calls) == 1


def test_migration_roundtrip(db):
    cfg = Config("alembic.ini")
    command.check(cfg)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    command.check(cfg)
