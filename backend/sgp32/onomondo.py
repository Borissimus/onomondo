"""Only the three guide-confirmed paths. Hosted wire shape requires confirmation."""

import math
import random
import re
import ssl
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import httpx
from prometheus_client import Counter
from sgp32_common.security import scrub

from sgp32.config import Settings

HTTP_CODES = Counter("onomondo_http_responses_total", "Upstream responses", ["status"])


class UpstreamError(Exception):
    def __init__(self, code: str, *, retry_after: float = 0, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after
        self.retryable = retryable


class SubmissionUnknown(UpstreamError):
    def __init__(self) -> None:
        super().__init__("submission_unknown")


def retry_delay(header: str | None, attempt: int, interval: float = 15) -> float:
    delay = max(10, interval, min(300, 2 ** min(attempt, 8)) + random.uniform(0, 1))
    if header:
        try:
            seconds = float(header)
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(header) - datetime.now(UTC)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                seconds = 0
        if math.isfinite(seconds):
            delay = max(delay, seconds)
    return delay


def profile_request(eid: str) -> dict[str, Any]:
    if not re.fullmatch(r"\d{32}", eid):
        raise UpstreamError("invalid_eid")
    return {"eidValue": eid, "order": {"psmo": [{"listProfileInfo": {}}]}}


class Onomondo:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.settings = settings
        self.sleep = sleep
        assert settings.onomondo_api_key
        self.client = httpx.Client(
            base_url=settings.onomondo_api_url.rstrip("/") + "/",
            headers={"Authorization": "Bearer " + settings.onomondo_api_key.get_secret_value()},
            timeout=httpx.Timeout(10, connect=5),
            follow_redirects=False,
            transport=transport,
            trust_env=False,
            verify=ssl.create_default_context(cafile=str(settings.onomondo_ca_file))
            if settings.onomondo_ca_file
            else True,
        )
        self.mock = isinstance(transport, httpx.MockTransport)

    def close(self) -> None:
        self.client.close()

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        if not self.mock and not self.settings.onomondo_schema_confirmed:
            raise UpstreamError("beta_schema_unconfirmed")
        started = time.monotonic()
        try:
            # Streaming supplies a hard wall-clock budget in addition to per-I/O timeouts.
            with self.client.stream(method, path, json=body) as response:
                HTTP_CODES.labels(str(response.status_code)).inc()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 1024 * 1024 or time.monotonic() - started > 25:
                        raise UpstreamError("upstream_response_limit")
                if response.status_code == 429:
                    raise UpstreamError(
                        "upstream_rate_limited",
                        retryable=True,
                        retry_after=retry_delay(response.headers.get("Retry-After"), 0),
                    )
                if response.status_code >= 500:
                    if method == "POST":
                        raise SubmissionUnknown()
                    raise UpstreamError("upstream_unavailable", retryable=True)
                if response.status_code >= 300:
                    raise UpstreamError("upstream_rejected")
                import json

                try:
                    return json.loads(data)
                except (ValueError, UnicodeError):
                    raise UpstreamError("upstream_schema_error") from None
        except (httpx.TransportError, UpstreamError) as exc:
            if method == "POST" and (
                isinstance(exc, httpx.TransportError)
                or isinstance(exc, UpstreamError)
                and exc.code in {"upstream_schema_error", "upstream_response_limit"}
            ):
                raise SubmissionUnknown() from None
            if isinstance(exc, httpx.TransportError):
                raise UpstreamError("upstream_transport_error", retryable=True) from None
            raise

    def inventory(self) -> list[dict[str, Any]]:
        budget = time.monotonic() + 45
        for attempt in range(3):
            try:
                value = self.request("GET", "euicc")
                break
            except UpstreamError as exc:
                delay = max(
                    exc.retry_after, retry_delay(None, attempt, self.settings.poll_interval)
                )
                if not exc.retryable or attempt == 2 or time.monotonic() + delay >= budget:
                    raise
                self.sleep(delay)
        if isinstance(value, dict):
            pagination = value.get("pagination")
            if not isinstance(pagination, dict) or not isinstance(pagination.get("has_more"), bool):
                raise UpstreamError("upstream_schema_error")
            if pagination["has_more"]:
                # The hosted pagination request contract is not yet authenticated/documented.
                # Never silently return an incomplete organization inventory.
                raise UpstreamError("inventory_pagination_unconfirmed")
            data = value.get("data")
            if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
                raise UpstreamError("upstream_schema_error")
            # Observed hosted shape. Association tokens and key material are not inventory metadata.
            allowed = {"id", "counter_value", "consumer_euicc", "created_at", "updated_at"}
            value = [
                {
                    "eidValue": row.get("eid_value"),
                    **{key: item for key, item in row.items() if key in allowed},
                }
                for row in data
            ]
        if not isinstance(value, list) or any(
            not isinstance(row, dict) or not re.fullmatch(r"\d{32}", str(row.get("eidValue", "")))
            for row in value
        ):
            raise UpstreamError("upstream_schema_error")
        return value

    def submit(self, eid: str) -> str:
        value = self.request("POST", "orders/psmo", profile_request(eid))
        resource = value.get("resourceId") if isinstance(value, dict) else None
        if not isinstance(resource, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", resource):
            raise SubmissionUnknown()
        return resource

    def poll(self, resource: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", resource):
            raise UpstreamError("invalid_resource_id")
        value = self.request("GET", "orders/psmo/" + quote(resource, safe=""))
        if not isinstance(value, dict):
            raise UpstreamError("upstream_schema_error")
        return value


def normalize(value: dict[str, Any]) -> tuple[str, list[dict[str, Any]], str | None]:
    status = value.get("status")
    if not isinstance(status, str):
        raise UpstreamError("upstream_schema_error")
    if status in {"new", "work", "absent"}:
        return {"new": "queued", "work": "working", "absent": "absent"}[status], [], None
    if status != "done":
        raise UpstreamError("upstream_schema_error")
    outcome = value.get("outcome")
    if not isinstance(outcome, list) or len(outcome) != 1 or not isinstance(outcome[0], dict):
        return "failed", [], "unrecognized_outcome"
    item = outcome[0]
    if "procedureError" in item:
        return "failed", [], "procedure_error"
    result = item.get("listProfileInfoResult")
    if (
        set(item) != {"listProfileInfoResult"}
        or not isinstance(result, dict)
        or result.get("finalResult") != "successResult"
        or "procedureError" in result
    ):
        return "failed", [], "unsuccessful_outcome"
    rows = result.get("profileInfoList")
    if not isinstance(rows, list):
        return "failed", [], "invalid_profile_inventory"
    profiles = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            return "failed", [], "invalid_profile_inventory"
        iccid = row.get("iccid")
        # Conventional ICCIDs are not byte-swapped. BCD encodings require a confirmed adapter.
        if not isinstance(iccid, str) or not re.fullmatch(r"\d{18,22}", iccid) or iccid in seen:
            return "failed", [], "invalid_profile_inventory"
        seen.add(iccid)
        fields = {
            "name": "profileName",
            "provider": "serviceProviderName",
            "profile_class": "profileClass",
            "state": "profileState",
        }
        normalized: dict[str, Any] = {"iccid": iccid}
        for local, remote in fields.items():
            val = row.get(remote)
            if val is not None and (
                not isinstance(val, str)
                or len(val) > (32 if local in {"profile_class", "state"} else 128)
            ):
                return "failed", [], "invalid_profile_inventory"
            normalized[local] = val
        fallback = row.get("fallbackAttribute")
        if fallback is not None and not isinstance(fallback, bool):
            return "failed", [], "invalid_profile_inventory"
        normalized["fallback"] = fallback
        profiles.append(normalized)
    return "succeeded", profiles, None


def safe_raw(value: dict[str, Any]) -> dict[str, Any]:
    return dict(scrub(value, identifiers=False))
