import hashlib
import json
import math
import secrets
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, ConfigDict, Field
from sgp32_common.messages import envelope
from sgp32_common.security import configure_logging, scrub
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from sgp32.config import Settings
from sgp32.onomondo import Onomondo, UpstreamError, profile_request
from sgp32.storage import (
    Database,
    Device,
    DeviceCommand,
    Euicc,
    Idempotency,
    Observation,
    Operation,
    Outbox,
    Profile,
    audit,
    uid,
)

basic = HTTPBasic(auto_error=False)


class BindDevice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    name: str = Field(min_length=1, max_length=128)
    eid: str = Field(pattern=r"^\d{32}$")


class Reconcile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resourceId: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$")


def timestamp(value: float | None) -> str | None:
    return datetime.fromtimestamp(value, UTC).isoformat().replace("+00:00", "Z") if value else None


def row_dict(row: Any) -> dict[str, Any]:
    value = {column.key: getattr(row, column.key) for column in row.__table__.columns}
    for key in list(value):
        if key.endswith("_at") or key in {"last_seen", "next_poll", "deadline"}:
            value[key] = timestamp(value[key])
    for key in ("active_eid", "lease_token", "lease_until", "attempts"):
        value.pop(key, None)
    return dict(scrub(value, identifiers=False))


def create_app(
    settings: Settings | None = None,
    db: Database | None = None,
    upstream: Onomondo | None = None,
    dependencies_ready: Callable[[], bool] | None = None,
) -> FastAPI:
    settings = settings or Settings()
    db = db or Database(settings.database_url.get_secret_value())
    upstream = upstream or Onomondo(settings)
    config, database, client = settings, db, upstream

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
        configure_logging()
        yield
        client.close()
        database.engine.dispose()

    app = FastAPI(
        title="Read-only SGP.32 prototype",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.db, app.state.upstream = db, upstream

    @app.middleware("http")
    async def correlation(request: Request, call_next: Any) -> Response:
        try:
            value = str(UUID(request.headers.get("X-Correlation-ID", "")))
        except ValueError:
            value = uid()
        request.state.correlation_id = value
        try:
            response = await call_next(request)
        except Exception:
            response = JSONResponse(
                {
                    "error": {
                        "code": "internal_error",
                        "message": "Request could not be completed",
                        "correlationId": value,
                        "details": {},
                    }
                },
                status_code=500,
            )
        response.headers["X-Correlation-ID"] = value
        return response

    def fail(request: Request, status: int, code: str) -> JSONResponse:
        return JSONResponse(
            {
                "error": {
                    "code": code,
                    "message": code.replace("_", " "),
                    "correlationId": request.state.correlation_id,
                    "details": {},
                }
            },
            status_code=status,
            headers={"WWW-Authenticate": "Basic"} if status == 401 else None,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return fail(request, exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _: RequestValidationError) -> JSONResponse:
        return fail(request, 422, "invalid_request")

    @app.exception_handler(UpstreamError)
    async def upstream_error(request: Request, exc: UpstreamError) -> JSONResponse:
        response = fail(request, 503, exc.code)
        if exc.retry_after:
            response.headers["Retry-After"] = str(math.ceil(exc.retry_after))
        return response

    def authenticate(
        request: Request, credentials: Annotated[HTTPBasicCredentials | None, Depends(basic)]
    ) -> str:
        accepted = False
        if credentials:
            try:
                assert config.operator_password_hash
                valid_password = PasswordHasher().verify(
                    config.operator_password_hash.get_secret_value(), credentials.password
                )
                accepted = valid_password and secrets.compare_digest(
                    credentials.username.encode(), config.operator_username.encode()
                )
            except (VerificationError, ValueError):
                pass
        if not accepted:
            with database.session() as s:
                audit(
                    s,
                    "anonymous",
                    "authenticate",
                    "management",
                    "denied",
                    request.state.correlation_id,
                )
            raise HTTPException(401, "authentication_required")
        return config.operator_username

    actor_dep = Depends(authenticate)
    router = APIRouter(prefix="/api/v1", dependencies=[actor_dep])

    @app.get("/api/v1/health/live")
    def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/api/v1/health/ready")
    def ready() -> JSONResponse:
        try:
            with database.session() as s:
                s.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            available = config.onomondo_schema_confirmed and (
                dependencies_ready() if dependencies_ready else False
            )
            return JSONResponse(
                {"status": "ready" if available else "not_ready"},
                status_code=200 if available else 503,
            )
        except Exception:
            return JSONResponse({"status": "not_ready"}, status_code=503)

    @router.get("/openapi.json", include_in_schema=False)
    def schema() -> dict[str, Any]:
        return app.openapi()

    @router.get("/docs", include_in_schema=False)
    def docs() -> Response:
        return get_swagger_ui_html(openapi_url="/api/v1/openapi.json", title="SGP.32 API")

    @router.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    def mutation(
        request: Request,
        payload: dict[str, Any],
        status: int,
        action: Callable[[Session], dict[str, Any]],
    ) -> JSONResponse:
        actor = config.operator_username
        key = request.headers.get("Idempotency-Key")
        if key is not None and (not key.strip() or len(key) > 128):
            raise HTTPException(422, "invalid_idempotency_key")
        digest = hashlib.sha256(
            json.dumps(
                [request.method, request.url.path, payload], sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        try:
            with database.session() as s:
                if key:
                    prior = s.scalar(
                        select(Idempotency).where(
                            Idempotency.actor == actor, Idempotency.key == key
                        )
                    )
                    if prior:
                        if prior.request_hash != digest:
                            raise HTTPException(409, "idempotency_conflict")
                        return JSONResponse(prior.response, status_code=prior.status_code)
                    entry = Idempotency(
                        actor=actor, key=key, request_hash=digest, status_code=status
                    )
                    s.add(entry)
                    s.flush()
                result = action(s)
                if key:
                    entry.response = result
                audit(
                    s,
                    actor,
                    "management.mutate",
                    request.url.path,
                    "allowed",
                    request.state.correlation_id,
                )
            return JSONResponse(result, status_code=status)
        except IntegrityError:
            # A concurrent idempotent request may have committed while we waited on its key.
            if key:
                with database.session() as s:
                    prior = s.scalar(
                        select(Idempotency).where(
                            Idempotency.actor == actor, Idempotency.key == key
                        )
                    )
                    if prior and prior.request_hash == digest:
                        return JSONResponse(prior.response, status_code=prior.status_code)
            with database.session() as s:
                audit(
                    s,
                    actor,
                    "management.mutate",
                    request.url.path,
                    "conflict",
                    request.state.correlation_id,
                )
            raise HTTPException(409, "operation_conflict") from None
        except (HTTPException, UpstreamError):
            with database.session() as s:
                audit(
                    s,
                    actor,
                    "management.mutate",
                    request.url.path,
                    "denied",
                    request.state.correlation_id,
                )
            raise

    def require(s: Session, model: Any, identity: str) -> Any:
        row = s.get(model, identity)
        if row is None:
            raise HTTPException(404, "not_found")
        return row

    @router.post("/euiccs/sync")
    def sync(request: Request) -> JSONResponse:
        def action(s: Session) -> dict[str, Any]:
            rows = client.inventory()
            for row in rows:
                current = s.get(Euicc, row["eidValue"])
                if current is None:
                    current = Euicc(eid=row["eidValue"])
                    s.add(current)
                current.metadata_json = scrub(row, identifiers=False)
                current.updated_at = time.time()
            return {"count": len(rows)}

        return mutation(request, {}, 200, action)

    @router.get("/euiccs")
    def euiccs(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> Any:
        with database.session() as s:
            return {
                "items": [
                    row_dict(x)
                    for x in s.scalars(
                        select(Euicc).order_by(Euicc.eid).limit(limit).offset(offset)
                    )
                ]
            }

    @router.get("/euiccs/{eid}")
    def euicc(eid: str) -> Any:
        with database.session() as s:
            return {
                "euicc": row_dict(require(s, Euicc, eid)),
                "profiles": [
                    row_dict(x) for x in s.scalars(select(Profile).where(Profile.eid == eid))
                ],
                "activeOperations": [
                    row_dict(x)
                    for x in s.scalars(select(Operation).where(Operation.active_eid == eid))
                ],
            }

    @router.post("/devices", status_code=201)
    def bind(request: Request, body: BindDevice) -> JSONResponse:
        def action(s: Session) -> dict[str, Any]:
            require(s, Euicc, body.eid)
            device = Device(**body.model_dump())
            s.add(device)
            s.flush()
            return row_dict(device)

        return mutation(request, body.model_dump(), 201, action)

    def device_summary(s: Session, device: Device) -> dict[str, Any]:
        latest = s.scalar(
            select(Observation)
            .where(Observation.device_id == device.device_id)
            .order_by(Observation.observed_at.desc())
            .limit(1)
        )
        value = row_dict(device)
        if device.last_seen and time.time() - device.last_seen > 180:
            value["state"] = "offline"
        value["modem"] = row_dict(latest) if latest else None
        return value

    @router.get("/devices")
    def devices(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> Any:
        with database.session() as s:
            return {
                "items": [
                    device_summary(s, x)
                    for x in s.scalars(
                        select(Device).order_by(Device.device_id).limit(limit).offset(offset)
                    )
                ]
            }

    @router.get("/devices/{device_id}")
    def device(device_id: str) -> Any:
        with database.session() as s:
            value = require(s, Device, device_id)
            return {**device_summary(s, value), "euicc": row_dict(require(s, Euicc, value.eid))}

    @router.post("/euiccs/{eid}/profiles/refresh", status_code=202)
    def refresh(request: Request, eid: str) -> JSONResponse:
        def action(s: Session) -> dict[str, Any]:
            require(s, Euicc, eid)
            op = Operation(
                eid=eid,
                active_eid=eid,
                requested_by=config.operator_username,
                correlation_id=request.state.correlation_id,
                request=profile_request(eid),
                deadline=time.time() + config.operation_timeout,
            )
            s.add(op)
            s.flush()
            return {"operationId": op.operation_id}

        return mutation(request, {}, 202, action)

    @router.get("/euiccs/{eid}/profiles")
    def profiles(eid: str) -> Any:
        with database.session() as s:
            card = require(s, Euicc, eid)
            return {
                "observedAt": timestamp(card.profiles_observed_at),
                "items": [
                    row_dict(x) for x in s.scalars(select(Profile).where(Profile.eid == eid))
                ],
            }

    @router.get("/operations/{operation_id}")
    def operation(operation_id: str) -> Any:
        with database.session() as s:
            return row_dict(require(s, Operation, operation_id))

    @router.get("/operations")
    def operations(
        eid: str | None = None,
        status: str | None = None,
        operation_type: str | None = Query(None, alias="type"),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> Any:
        query = select(Operation)
        for column, value in (
            (Operation.eid, eid),
            (Operation.state, status),
            (Operation.operation_type, operation_type),
        ):
            if value:
                query = query.where(column == value)
        with database.session() as s:
            return {
                "items": [
                    row_dict(x)
                    for x in s.scalars(
                        query.order_by(Operation.created_at.desc()).limit(limit).offset(offset)
                    )
                ]
            }

    @router.post("/operations/{operation_id}/reconcile", status_code=202)
    def reconcile(request: Request, operation_id: str, body: Reconcile) -> JSONResponse:
        def action(s: Session) -> dict[str, Any]:
            op = s.scalar(
                select(Operation).where(Operation.operation_id == operation_id).with_for_update()
            )
            if op is None:
                raise HTTPException(404, "not_found")
            if op.state not in {"submission_unknown", "timed_out"}:
                raise HTTPException(409, "not_reconcilable")
            resource = op.resource_id or body.resourceId
            if not resource:
                raise HTTPException(409, "resource_id_required_no_resubmission")
            # GET only. Verify resource binding before associating an unknown submission.
            result = client.poll(resource)
            if result.get("resource") != op.request:
                raise HTTPException(409, "resource_binding_mismatch")
            op.resource_id, op.state = resource, "queued"
            op.error, op.next_poll = None, time.time() + config.poll_interval
            op.deadline = time.time() + config.operation_timeout
            return {"operationId": op.operation_id, "state": op.state}

        return mutation(request, body.model_dump(), 202, action)

    @router.post("/devices/{device_id}/commands/diagnostics", status_code=202)
    def diagnostics(request: Request, device_id: str) -> JSONResponse:
        def action(s: Session) -> dict[str, Any]:
            device = require(s, Device, device_id)
            if not device.enabled:
                raise HTTPException(409, "device_disabled")
            command_id, now = uid(), time.time()
            payload = envelope(
                "modem.diagnostics.request",
                {
                    "commandId": command_id,
                    "createdAt": timestamp(now),
                    "expiresAt": timestamp(now + 300),
                    "includeSensitiveIdentifiers": False,
                },
                request.state.correlation_id,
            )
            s.add(
                DeviceCommand(
                    command_id=command_id,
                    device_id=device_id,
                    correlation_id=request.state.correlation_id,
                    request=payload,
                    expires_at=now + 300,
                )
            )
            s.add(Outbox(topic=f"devices/{device_id}/commands", payload=payload))
            return {"commandId": command_id}

        return mutation(request, {}, 202, action)

    @router.get("/device-commands/{command_id}")
    def command(command_id: str) -> Any:
        with database.session() as s:
            cmd = require(s, DeviceCommand, command_id)
            if cmd.state == "queued" and cmd.expires_at < time.time():
                cmd.state = "expired"
            return row_dict(cmd)

    app.include_router(router)
    return app
