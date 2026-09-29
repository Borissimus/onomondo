"""Run against a disposable database only; CI provisions one per test job."""

import os
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sgp32.onomondo import Onomondo, profile_request
from sgp32.storage import Database, Euicc, Operation, uid
from sgp32.worker import Worker
from sqlalchemy import select

from tests.conftest import EID, FakeOnomondo


@pytest.mark.postgres
def test_postgres_migration_and_worker_lease(settings, monkeypatch):
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL must point to an empty disposable PostgreSQL database")
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    command.check(Config("alembic.ini"))
    db = Database(url)
    correlation = uid()
    with db.session() as s:
        s.add(Euicc(eid=EID))
    with db.session() as s:
        s.add(
            Operation(
                eid=EID,
                active_eid=EID,
                requested_by="ci",
                correlation_id=correlation,
                request=profile_request(EID),
                deadline=time.time() + 300,
            )
        )
    fake = FakeOnomondo()
    client = Onomondo(settings, httpx.MockTransport(fake))
    worker = Worker(db, client, settings)
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda _: worker.tick(), range(4)))
    assert sum(x.method == "POST" for x in fake.calls) == 1
    with db.session() as s:
        assert s.scalar(select(Operation)).state == "queued"
    db.engine.dispose()
    client.close()
