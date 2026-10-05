#!/usr/bin/env python3
"""Fail-closed hosted SGP.32 listProfileInfo acceptance test."""

import argparse
import getpass
import json
import re
import sys
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import httpx

TERMINAL = {"succeeded", "failed", "absent", "timed_out", "submission_unknown"}
NON_TERMINAL = {"created", "submitting", "queued", "working"}
EID_PATTERN = re.compile(r"^\d{32}$")


class ReadOnlyTestFailure(RuntimeError):
    pass


def masked(value: str, visible: int = 6) -> str:
    return "***" + value[-visible:] if len(value) > visible else "***"


class ReadOnlySgp32Test:
    def __init__(
        self,
        client: httpx.Client,
        eid: str,
        *,
        expected_count: int = 5,
        poll_interval: float = 15,
        timeout: float = 1800,
        sleep: Callable[[float], None] = time.sleep,
        output: Callable[[str], None] = print,
    ):
        if not EID_PATTERN.fullmatch(eid):
            raise ValueError("EID must contain exactly 32 digits")
        self.client = client
        self.eid = eid
        self.expected_count = expected_count
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.sleep = sleep
        self.output = output

    def _request(self, method: str, path: str, *, idempotent: bool = False) -> dict[str, Any]:
        allowed = method == "GET" or (
            method == "POST"
            and (
                path == "/api/v1/euiccs/sync"
                or path == f"/api/v1/euiccs/{self.eid}/profiles/refresh"
            )
        )
        if not allowed:
            raise ReadOnlyTestFailure("unsafe_or_unknown_endpoint_blocked")
        headers = {"Idempotency-Key": str(uuid4())} if idempotent else {}
        response = self.client.request(method, path, headers=headers)
        try:
            payload = response.json()
        except ValueError:
            raise ReadOnlyTestFailure(f"non_json_response HTTP {response.status_code}") from None
        if response.status_code >= 400:
            code = payload.get("error", {}).get("code", "http_error")
            raise ReadOnlyTestFailure(f"{code} HTTP {response.status_code}")
        if not isinstance(payload, dict):
            raise ReadOnlyTestFailure("unexpected_response_shape")
        return payload

    def run(self, operation_id: str | None = None) -> dict[str, Any]:
        ready = self._request("GET", "/api/v1/health/ready")
        if ready.get("status") != "ready":
            raise ReadOnlyTestFailure("service_not_ready")
        self.output("health: ready")

        if operation_id is None:
            sync = self._request("POST", "/api/v1/euiccs/sync", idempotent=True)
            if sync.get("count") != self.expected_count:
                raise ReadOnlyTestFailure("unexpected_euicc_count")
            inventory = self._request("GET", "/api/v1/euiccs?limit=50&offset=0")
            items = inventory.get("items")
            if not isinstance(items, list) or self.eid not in {item.get("eid") for item in items}:
                raise ReadOnlyTestFailure("target_eid_missing")
            self.output(f"inventory: {len(items)} eUICCs; target {masked(self.eid)} present")

            created = self._request(
                "POST", f"/api/v1/euiccs/{self.eid}/profiles/refresh", idempotent=True
            )
            operation_id = created.get("operationId")
            try:
                UUID(str(operation_id))
            except ValueError:
                raise ReadOnlyTestFailure("invalid_operation_id") from None
            self.output(f"operation: {operation_id} created")
        else:
            try:
                UUID(operation_id)
            except ValueError:
                raise ReadOnlyTestFailure("invalid_operation_id") from None
            self.output(f"operation: {operation_id} resumed; no order submitted")

        deadline = time.monotonic() + self.timeout
        previous = None
        operation: dict[str, Any] = {}
        while time.monotonic() < deadline:
            operation = self._request("GET", f"/api/v1/operations/{operation_id}")
            state = operation.get("state")
            if state != previous:
                self.output(f"operation: {state}")
                previous = state
            if state in TERMINAL:
                break
            if state not in NON_TERMINAL:
                raise ReadOnlyTestFailure("unknown_operation_state")
            self.sleep(self.poll_interval)
        else:
            raise ReadOnlyTestFailure(f"local_poll_timeout operation={operation_id}")

        if operation.get("state") != "succeeded":
            error = operation.get("error") or operation.get("state")
            raise ReadOnlyTestFailure(f"operation_failed: {error}")
        if not operation.get("resource_id"):
            raise ReadOnlyTestFailure("missing_upstream_resource_id")

        response = self._request("GET", f"/api/v1/euiccs/{self.eid}/profiles")
        profiles = response.get("items")
        if not isinstance(profiles, list) or not profiles:
            raise ReadOnlyTestFailure("successful_operation_without_profiles")
        safe_profiles = []
        for profile in profiles:
            iccid = profile.get("iccid")
            safe_profiles.append(
                {
                    "iccid": masked(iccid) if isinstance(iccid, str) else "unavailable",
                    "name": profile.get("name"),
                    "provider": profile.get("provider"),
                    "class": profile.get("profile_class"),
                    "state": profile.get("state"),
                }
            )
        result = {
            "status": "passed",
            "eid": masked(self.eid),
            "operationId": operation_id,
            "profiles": safe_profiles,
        }
        self.output(json.dumps(result, indent=2, sort_keys=True))
        return result


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--api-url", default="https://sgp32-api.borissimus.top")
    value.add_argument("--eid", required=True, help="32-digit EID; output is masked")
    value.add_argument("--username", default="admin")
    value.add_argument("--expected-count", type=int, default=5)
    value.add_argument("--poll-interval", type=float, default=15)
    value.add_argument("--timeout", type=float, default=1800)
    value.add_argument(
        "--operation-id",
        help="resume polling an existing operation without synchronizing or submitting an order",
    )
    return value


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    password = getpass.getpass(f"Password for {args.username}: ")
    try:
        with httpx.Client(
            base_url=args.api_url.rstrip("/"),
            auth=(args.username, password),
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": "sgp32-readonly-test/1"},
        ) as client:
            ReadOnlySgp32Test(
                client,
                args.eid,
                expected_count=args.expected_count,
                poll_interval=args.poll_interval,
                timeout=args.timeout,
            ).run(args.operation_id)
    except (httpx.HTTPError, ReadOnlyTestFailure, ValueError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        password = ""


if __name__ == "__main__":
    main()
