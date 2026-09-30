import httpx
import pytest
from alembic import command
from alembic.config import Config
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sgp32.api import create_app
from sgp32.config import Settings
from sgp32.onomondo import Onomondo
from sgp32.storage import Database

EID = "89000000000000000000000000000001"
ICCID = "8945000000000000001"
SUCCESS = {
    "status": "done",
    "outcome": [
        {
            "listProfileInfoResult": {
                "finalResult": "successResult",
                "profileInfoList": [
                    {
                        "iccid": ICCID,
                        "profileName": "Test Onomondo",
                        "profileState": "enabled",
                        "profileClass": "operational",
                    }
                ],
            }
        }
    ],
}


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path}/test.db",
        onomondo_api_key="unit-test-only",
        operator_password_hash=PasswordHasher(time_cost=1, memory_cost=8192).hash("test"),
    )


@pytest.fixture
def db(settings, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", settings.database_url.get_secret_value())
    command.upgrade(Config("alembic.ini"), "head")
    value = Database(settings.database_url.get_secret_value())
    yield value
    value.engine.dispose()


class FakeOnomondo:
    def __init__(self):
        self.states = [{"status": "new"}, {"status": "work"}, SUCCESS]
        self.calls = []
        self.post_error = None

    def __call__(self, request):
        self.calls.append(request)
        assert request.headers["authorization"] == "Bearer unit-test-only"
        if request.url.path == "/api/euicc":
            return httpx.Response(200, json=[{"eidValue": str(int(EID) + n)} for n in range(5)])
        if request.method == "POST":
            if self.post_error:
                raise self.post_error
            return httpx.Response(201, json={"resourceId": "resource-1"})
        state = self.states.pop(0)
        return state if isinstance(state, httpx.Response) else httpx.Response(200, json=state)


@pytest.fixture
def backend(settings, db):
    fake = FakeOnomondo()
    upstream = Onomondo(settings, httpx.MockTransport(fake))
    app = create_app(settings, db, upstream)
    with TestClient(app) as client:
        client.auth = ("admin", "test")
        yield client, fake, upstream
