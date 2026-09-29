import logging
import time
from typing import Any

from prometheus_client import Counter, Histogram, start_http_server
from sgp32_common.messages import envelope
from sgp32_common.security import configure_logging
from sqlalchemy import delete, select, update

from sgp32.config import Settings
from sgp32.onomondo import (
    Onomondo,
    SubmissionUnknown,
    UpstreamError,
    normalize,
    retry_delay,
    safe_raw,
)
from sgp32.storage import Database, Device, Euicc, Operation, Outbox, Profile, audit, uid

TERMINAL = {"succeeded", "failed", "absent", "timed_out", "submission_unknown"}
COUNTS = Counter("sgp32_operations_total", "Terminal operation states", ["state"])
DURATION = Histogram("sgp32_operation_duration_seconds", "Operation duration")


class Worker:
    def __init__(self, db: Database, upstream: Onomondo, settings: Settings):
        self.db, self.upstream, self.settings = db, upstream, settings

    def tick(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        token = uid()
        with self.db.session() as s:
            candidate = s.scalar(
                select(Operation.operation_id)
                .where(
                    Operation.state.not_in(TERMINAL),
                    Operation.next_poll <= now,
                    Operation.lease_until <= now,
                )
                .order_by(Operation.next_poll)
                .limit(1)
            )
            if not candidate:
                return False
            won = s.execute(
                update(Operation)
                .where(
                    Operation.operation_id == candidate,
                    Operation.lease_until <= now,
                    Operation.state.not_in(TERMINAL),
                )
                .values(lease_token=token, lease_until=now + 120)
            )
            if won.rowcount != 1:  # type: ignore[attr-defined]
                return False
        with self.db.session() as s:
            op = s.get(Operation, candidate)
            assert op
            state, resource, eid = op.state, op.resource_id, op.eid
            deadline, attempt = op.deadline, op.attempts
            if state == "created":
                # Commit before touching the network. A crash is reconciled, never re-POSTed.
                op.state = "submitting"
                op.updated_at = now
        result: dict[str, Any] | None = None
        profiles: list[dict[str, Any]] = []
        error: str | None = None
        delay = self.settings.poll_interval
        next_state = state
        try:
            if state == "submitting":
                next_state, error = "submission_unknown", "interrupted_submission"
            elif now >= deadline:
                next_state, error = "timed_out", "deadline_exceeded"
            elif state == "created":
                resource = self.upstream.submit(eid)
                next_state = "queued"
            elif resource:
                result = self.upstream.poll(resource)
                next_state, profiles, error = normalize(result)
                if state == "working" and next_state == "queued":
                    next_state = "working"
                attempt = 0
            else:
                next_state, error = "submission_unknown", "missing_resource_id"
        except SubmissionUnknown:
            next_state, error = "submission_unknown", "submission_unknown"
        except UpstreamError as exc:
            error = exc.code
            if state != "created" and exc.retryable and attempt < 6:
                attempt += 1
                delay = max(exc.retry_after, retry_delay(None, attempt, delay))
            else:
                next_state = "failed"
        with self.db.session() as s:
            op = s.scalar(
                select(Operation)
                .where(
                    Operation.operation_id == candidate,
                    Operation.lease_token == token,
                )
                .with_for_update()
            )
            if op is None:
                return True
            op.state, op.resource_id, op.error = next_state, resource, error
            op.updated_at, op.next_poll, op.attempts = (
                now,
                min(now + max(10, delay), op.deadline),
                attempt,
            )
            op.lease_until, op.lease_token = 0, None
            if result is not None:
                op.raw_outcome = safe_raw(result)
            if next_state == "succeeded":
                s.execute(delete(Profile).where(Profile.eid == eid))
                for profile in profiles:
                    s.add(Profile(eid=eid, observed_at=now, **profile))
                euicc = s.get(Euicc, eid)
                assert euicc
                euicc.profiles_observed_at = now
            if next_state in TERMINAL:
                # Unknown and timeout retain the EID lock until explicit reconciliation.
                if next_state not in {"submission_unknown", "timed_out"}:
                    op.active_eid = None
                COUNTS.labels(next_state).inc()
                DURATION.observe(max(0, now - op.created_at))
            audit(
                s, "worker", "operation.transition", op.operation_id, next_state, op.correlation_id
            )
            for device in s.scalars(select(Device).where(Device.eid == eid, Device.enabled)):
                s.add(
                    Outbox(
                        topic=f"devices/{device.device_id}/esim/operations/{candidate}",
                        payload=envelope(
                            "esim.operation.status",
                            {
                                "operationId": candidate,
                                "operationType": op.operation_type,
                                "state": next_state,
                                "deviceActionRequired": False,
                            },
                            op.correlation_id,
                        ),
                    )
                )
        return True


def main() -> None:
    configure_logging()
    try:
        settings = Settings()
        db = Database(settings.database_url.get_secret_value())
        upstream = Onomondo(settings)
        from sgp32.mqtt import BackendMessaging

        messaging = BackendMessaging.from_env(db)
        messaging.start()
        worker = Worker(db, upstream, settings)
        start_http_server(9100, addr="127.0.0.1")
        try:
            while True:
                worker.tick()
                messaging.flush()
                time.sleep(1)
        finally:
            messaging.stop()
            upstream.close()
            db.engine.dispose()
    except KeyboardInterrupt:
        return
    except Exception:
        logging.error("worker_stopped_configuration_or_dependency_error")
        raise SystemExit(1) from None
