from pathlib import Path

from fastapi.testclient import TestClient

from deploy.local.mock_onomondo import EIDS, create_mock


def test_local_mock_readonly_and_durable(tmp_path):
    secret = tmp_path / "mock-secret"
    secret.write_text("local-test-credential")
    database = tmp_path / "mock.db"
    app = create_mock(database, secret)
    headers = {"Authorization": "Bearer local-test-credential"}
    with TestClient(app) as api:
        assert api.get("/api/euicc").status_code == 401
        assert len(api.get("/api/euicc", headers=headers).json()["data"]) == 5
        for operation in ("enable", "disable", "delete", "download"):
            assert (
                api.post(
                    "/api/orders/psmo",
                    headers=headers,
                    json={
                        "eidValue": EIDS[0],
                        "order": {"psmo": [{operation: {}}]},
                    },
                ).status_code
                == 422
            )
        assert (
            api.post(
                "/api/orders/psmo",
                headers=headers,
                json={
                    "eidValue": "89000000000000000000000000000099",
                    "order": {"psmo": [{"listProfileInfo": {}}]},
                },
            ).status_code
            == 422
        )
        request = {"eidValue": EIDS[0], "order": {"psmo": [{"listProfileInfo": {}}]}}
        operation = api.post("/api/orders/psmo", headers=headers, json=request).json()["resourceId"]
        path = "/api/orders/psmo/" + operation
        assert api.get(path, headers=headers).json()["status"] == "new"
    with TestClient(create_mock(database, secret)) as restarted:
        assert restarted.get(path, headers=headers).json()["status"] == "work"
        result = restarted.get(path, headers=headers).json()
        assert result["status"] == "done" and result["resource"] == request
        assert result["outcome"][0]["listProfileInfoResult"]["finalResult"] == "successResult"
        assert (
            restarted.get("/api/orders/psmo/missing", headers=headers).json()["status"] == "absent"
        )


def test_local_compose_is_separate_and_loopback_only():
    import yaml

    config = yaml.safe_load(Path("deploy/local/compose.yaml").read_text())
    assert config["networks"]["default"]["internal"] is True
    for service in config["services"].values():
        for port in service.get("ports", []):
            assert port.startswith("127.0.0.1:")
    agent = config["services"]["agent-sim"]
    assert agent["environment"]["AGENT_SIMULATE"] == "true"
    assert not any("onomondo" in key.lower() for key in agent["environment"])
    assert "mock_api_key" not in agent["secrets"]
    for role in ("api", "worker"):
        assert (
            config["services"][role]["depends_on"]["migrate"]["condition"]
            == "service_completed_successfully"
        )


def test_local_gateway_reresolves_recreated_services():
    config = Path("deploy/local/haproxy.cfg").read_text()
    assert "nameserver docker_dns 127.0.0.11:53" in config
    for service in ("api:8443", "mosquitto:8883", "postgres:5432"):
        assert service in config
    assert config.count("resolvers docker init-addr last,libc,none") == 3


def test_live_overlay_keeps_key_backend_only_and_disables_mock_services():
    config = Path("deploy/local/compose.live.yaml").read_text()
    assert "name: sgp32-live" in config
    assert config.count("ONOMONDO_API_KEY_FILE: /run/secrets/onomondo_api_key") == 2
    assert config.count("ONOMONDO_SCHEMA_CONFIRMED: 'true'") == 2
    assert config.count("networks: !override [default, edge]") == 2
    assert config.count("profiles: [mock-only]") == 2
    agent_block = config.split("  agent-sim:", 1)[1].split("  api:", 1)[0]
    assert "onomondo_api_key" not in agent_block


def test_production_compose_exposes_only_https_and_mqtts():
    import yaml

    config = yaml.safe_load(Path("deploy/compose.yaml").read_text())
    services = config["services"]
    assert services["caddy"]["ports"] == ["80:80", "443:443"]
    assert services["mosquitto"]["ports"] == ["8883:8883"]
    for service in ("api", "worker", "postgres"):
        assert "ports" not in services[service]
    assert services["api"]["expose"] == ["8000"]
    aliases = services["mosquitto"]["networks"]["default"]["aliases"]
    assert aliases == ["sgp32-mqtt.borissimus.top"]
    caddyfile = Path("deploy/production/Caddyfile").read_text()
    assert "sgp32-api.borissimus.top" in caddyfile
    assert "reverse_proxy api:8000" in caddyfile
    assert "email off" not in caddyfile


def test_production_image_normalizes_source_permissions_for_runtime_user():
    dockerfile = Path("deploy/Dockerfile").read_text()

    assert "chmod -R a+rX backend shared device-agent migrations alembic.ini" in dockerfile
    assert "USER 10001:10001" in dockerfile
