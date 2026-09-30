"""Local-only fake. It accepts synthetic EIDs and listProfileInfo exclusively."""

import json
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException

EIDS = [str(89000000000000000000000000000001 + index) for index in range(5)]


def create_mock(database: Path, key_file: Path) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        with sqlite3.connect(database) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS orders "
                "(id TEXT PRIMARY KEY, request TEXT NOT NULL, polls INTEGER NOT NULL)"
            )
        app.state.key = key_file.read_text().strip()
        if not app.state.key:
            raise RuntimeError("Mock credential required")
        yield

    app = FastAPI(title="LOCAL FAKE Onomondo", lifespan=lifespan, docs_url=None, openapi_url=None)

    def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
        if not authorization or not secrets.compare_digest(
            authorization, "Bearer " + app.state.key
        ):
            raise HTTPException(401, "Mock authentication required")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "local_mock"}

    @app.get("/api/euicc", dependencies=[Depends(authenticate)])
    def inventory() -> dict[str, Any]:
        return {
            "data": [
                {"eid_value": eid, "id": f"local-fake-{index + 1}", "consumer_euicc": False}
                for index, eid in enumerate(EIDS)
            ],
            "pagination": {"has_more": False, "next_page": None},
        }

    @app.post("/api/orders/psmo", status_code=201, dependencies=[Depends(authenticate)])
    def submit(body: dict[str, Any]) -> dict[str, str]:
        eid = body.get("eidValue")
        if eid not in EIDS or body != {
            "eidValue": eid,
            "order": {"psmo": [{"listProfileInfo": {}}]},
        }:
            raise HTTPException(422, "Only synthetic listProfileInfo is supported")
        resource_id = str(uuid4())
        with sqlite3.connect(database) as db:
            db.execute("INSERT INTO orders VALUES (?, ?, 0)", (resource_id, json.dumps(body)))
        return {"resourceId": resource_id}

    @app.get("/api/orders/psmo/{resource_id}", dependencies=[Depends(authenticate)])
    def poll(resource_id: str) -> dict[str, Any]:
        with sqlite3.connect(database) as db:
            # Serialize GET state advances, including across processes/restarts.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT request, polls FROM orders WHERE id = ?", (resource_id,)
            ).fetchone()
            if row is None:
                return {"status": "absent"}
            request, count = json.loads(row[0]), row[1]
            db.execute("UPDATE orders SET polls = polls + 1 WHERE id = ?", (resource_id,))
        result: dict[str, Any] = {
            "status": ("new", "work", "done")[min(count, 2)],
            "resource": request,
        }
        if count >= 2:
            index = EIDS.index(request["eidValue"]) + 1
            result["outcome"] = [
                {
                    "listProfileInfoResult": {
                        "finalResult": "successResult",
                        "profileInfoList": [
                            {
                                "iccid": f"89450000000000{index:05d}",
                                "profileName": "LOCAL FAKE Onomondo",
                                "serviceProviderName": "LOCAL SIMULATOR",
                                "profileClass": "operational",
                                "profileState": "enabled",
                                "fallbackAttribute": False,
                            }
                        ],
                    }
                }
            ]
        return result

    return app


app = create_mock(
    Path(os.environ.get("MOCK_DATABASE", "/var/lib/sgp32/mock.db")),
    Path(os.environ.get("MOCK_KEY_FILE", "/run/secrets/mock_api_key")),
)
