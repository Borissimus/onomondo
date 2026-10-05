import json

import httpx
import pytest

from tools.sgp32_readonly_test import ReadOnlySgp32Test, ReadOnlyTestFailure

EID = "89033085000000000000000000876379"


def test_readonly_tool_accepts_only_successful_profile_inventory():
    states = iter(
        [
            {"state": "created"},
            {"state": "queued"},
            {"state": "working"},
            {
                "state": "succeeded",
                "resource_id": "upstream-resource",
                "raw_outcome": {"outcome": [{"listProfileInfoResult": {}}]},
            },
        ]
    )

    def handler(request):
        assert request.url.scheme == "https"
        if request.url.path == "/api/v1/health/ready":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/api/v1/euiccs/sync":
            assert request.method == "POST" and request.headers["Idempotency-Key"]
            return httpx.Response(200, json={"count": 5})
        if request.url.path == "/api/v1/euiccs":
            return httpx.Response(200, json={"items": [{"eid": EID}]})
        if request.url.path.endswith("/profiles/refresh"):
            assert request.method == "POST" and request.headers["Idempotency-Key"]
            return httpx.Response(202, json={"operationId": "d1a0e0cd-c13d-40f6-97df-04929fcf0b91"})
        if request.url.path.startswith("/api/v1/operations/"):
            return httpx.Response(200, json=next(states))
        if request.url.path.endswith("/profiles"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "iccid": "89457300000045623769",
                            "name": "Onomondo",
                            "provider": "Onomondo",
                            "profile_class": "operational",
                            "state": "enabled",
                        }
                    ]
                },
            )
        raise AssertionError(request.url)

    output = []
    with httpx.Client(
        base_url="https://sgp32-api.example", transport=httpx.MockTransport(handler)
    ) as client:
        result = ReadOnlySgp32Test(
            client, EID, poll_interval=0, sleep=lambda _: None, output=output.append
        ).run()

    assert result["status"] == "passed"
    rendered = json.dumps(result)
    assert EID not in rendered
    assert "89457300000045623769" not in rendered
    assert result["profiles"][0]["iccid"] == "***623769"


@pytest.mark.parametrize(
    "terminal",
    [
        {"state": "failed", "error": "procedure_error"},
        {"state": "absent"},
        {"state": "timed_out"},
        {"state": "submission_unknown"},
    ],
)
def test_readonly_tool_fails_closed_on_non_success_terminal_state(terminal):
    def handler(request):
        responses = {
            "/api/v1/health/ready": {"status": "ready"},
            "/api/v1/euiccs/sync": {"count": 5},
            "/api/v1/euiccs": {"items": [{"eid": EID}]},
            f"/api/v1/euiccs/{EID}/profiles/refresh": {
                "operationId": "d1a0e0cd-c13d-40f6-97df-04929fcf0b91"
            },
            "/api/v1/operations/d1a0e0cd-c13d-40f6-97df-04929fcf0b91": terminal,
        }
        return httpx.Response(200, json=responses[request.url.path])

    with httpx.Client(
        base_url="https://sgp32-api.example", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(ReadOnlyTestFailure, match="operation_failed"):
            ReadOnlySgp32Test(client, EID, poll_interval=0, sleep=lambda _: None).run()


def test_readonly_tool_blocks_unknown_post_endpoint():
    with httpx.Client(
        base_url="https://sgp32-api.example",
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
    ) as client:
        tool = ReadOnlySgp32Test(client, EID)
        with pytest.raises(ReadOnlyTestFailure, match="unsafe_or_unknown_endpoint_blocked"):
            tool._request("POST", f"/api/v1/euiccs/{EID}/profiles/download")


def test_readonly_tool_resumes_without_submitting_another_order():
    operation_id = "d1a0e0cd-c13d-40f6-97df-04929fcf0b91"
    requests = []

    def handler(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/api/v1/health/ready":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == f"/api/v1/operations/{operation_id}":
            return httpx.Response(
                200,
                json={
                    "state": "succeeded",
                    "resource_id": "upstream-resource",
                },
            )
        if request.url.path == f"/api/v1/euiccs/{EID}/profiles":
            return httpx.Response(
                200,
                json={"items": [{"iccid": "89457300000045623769", "state": "enabled"}]},
            )
        raise AssertionError(request.url)

    with httpx.Client(
        base_url="https://sgp32-api.example", transport=httpx.MockTransport(handler)
    ) as client:
        result = ReadOnlySgp32Test(client, EID, output=lambda _: None).run(operation_id)

    assert result["status"] == "passed"
    assert all(method == "GET" for method, _ in requests)
