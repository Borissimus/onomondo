"""Local Docker lifecycle. Generated credentials never appear in command output."""

import argparse
import json
import os
import secrets
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import httpx
from argon2 import PasswordHasher

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / ".local" / "sgp32"
COMPOSE = ["docker", "compose", "-f", str(ROOT / "deploy/local/compose.yaml")]
MOUNTED = {
    "postgres_password",
    "database_url",
    "operator_argon2id",
    "mock_api_key",
    "backend_mqtt_password",
    "device_a_password",
    "mqtt_passwords",
    "ca.crt",
    "api.crt",
    "api.key",
    "mqtt.crt",
    "mqtt.key",
    "mock.crt",
    "mock.key",
}


def prepare() -> None:
    if PRIVATE.exists():
        required = MOUNTED | {"operator_password", "device_b_password", "manifest.json"}
        if not all((PRIVATE / name).is_file() for name in required):
            raise RuntimeError("Local credential directory is incomplete; existing files preserved")
        if PRIVATE.stat().st_mode & 0o077:
            raise RuntimeError("Local credential directory must have mode 0700")
        print("Existing local credentials preserved.")
        return
    if not shutil.which("openssl") or not shutil.which("mosquitto_passwd"):
        raise RuntimeError("Install openssl and mosquitto (mosquitto_passwd) to prepare local TLS")
    PRIVATE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=PRIVATE.parent, prefix="sgp32-setup-") as temporary:
        work = Path(temporary) / "credentials"
        work.mkdir(mode=0o700)

        def write(name: str, value: str) -> None:
            target = work / name
            target.write_text(value + "\n")
            target.chmod(0o600)

        def run(*args: str) -> None:
            subprocess.run(  # noqa: S603
                args, cwd=work, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
            )  # noqa: S603

        values = {
            name: secrets.token_urlsafe(32)
            for name in (
                "postgres_password",
                "operator_password",
                "mock_api_key",
                "backend_mqtt_password",
                "device_a_password",
                "device_b_password",
            )
        }
        for name, value in values.items():
            write(name, value)
        write("operator_argon2id", PasswordHasher().hash(values["operator_password"]))
        write(
            "database_url",
            "postgresql+psycopg://sgp32:" + values["postgres_password"] + "@postgres/sgp32",
        )
        write(
            "mqtt_passwords",
            "\n".join(
                user + ":" + values[name]
                for user, name in (
                    ("backend", "backend_mqtt_password"),
                    ("device-a", "device_a_password"),
                    ("device-b", "device_b_password"),
                )
            ),
        )
        run("mosquitto_passwd", "-U", str(work / "mqtt_passwords"))
        run(
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "30",
            "-keyout",
            "ca.key",
            "-out",
            "ca.crt",
            "-subj",
            "/CN=SGP32 LOCAL development CA",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
        )
        for name, host in (("api", "api"), ("mqtt", "mosquitto"), ("mock", "mock-onomondo")):
            write(
                name + ".ext",
                "basicConstraints=critical,CA:FALSE\n"
                "keyUsage=critical,digitalSignature,keyEncipherment\n"
                "extendedKeyUsage=serverAuth\n"
                f"subjectAltName=DNS:localhost,DNS:{host},IP:127.0.0.1",
            )
            run(
                "openssl",
                "req",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                name + ".key",
                "-out",
                name + ".csr",
                "-subj",
                "/CN=" + host,
            )
            run(
                "openssl",
                "x509",
                "-req",
                "-in",
                name + ".csr",
                "-CA",
                "ca.crt",
                "-CAkey",
                "ca.key",
                "-CAcreateserial",
                "-days",
                "30",
                "-extfile",
                name + ".ext",
                "-out",
                name + ".crt",
            )
        # Compose file secrets are read-only bind mounts. Non-root container users must
        # read them; the host parent remains 0700. CA signing key/password stay 0600.
        for name in MOUNTED:
            (work / name).chmod(0o444)
        (work / "ca.key").chmod(0o600)
        write("manifest.json", json.dumps({"mode": "local-mock", "createdAt": time.time()}))
        os.rename(work, PRIVATE)
    print("Local TLS and credentials prepared; values were not printed.")


def client() -> httpx.Client:
    return httpx.Client(
        base_url="https://localhost:8443",
        verify=ssl.create_default_context(cafile=str(PRIVATE / "ca.crt")),
        auth=("admin", (PRIVATE / "operator_password").read_text().strip()),
        timeout=10,
        trust_env=False,
    )


def smoke(device_id: str = "device-a") -> None:
    with client() as api:
        api.get("/api/v1/health/ready").raise_for_status()
        response = api.post("/api/v1/euiccs/sync")
        response.raise_for_status()
        if response.json()["count"] != 5:
            raise RuntimeError("Expected five LOCAL synthetic eUICCs")
        cards = api.get("/api/v1/euiccs").json()["items"]
        for index, device in enumerate(("device-a", "device-b")):
            if api.get("/api/v1/devices/" + device).status_code == 404:
                api.post(
                    "/api/v1/devices",
                    json={
                        "device_id": device,
                        "name": "Local simulator" if index == 0 else "Local USB diagnostics",
                        "eid": cards[index]["eid"],
                    },
                ).raise_for_status()
        diagnostics = api.post(
            "/api/v1/devices/" + device_id + "/commands/diagnostics",
            headers={"Idempotency-Key": str(uuid4())},
        )
        diagnostics.raise_for_status()
        command = diagnostics.json()["commandId"]
        end = time.monotonic() + 75
        while time.monotonic() < end:
            value = api.get("/api/v1/device-commands/" + command).json()
            if value["state"] != "queued":
                if value["state"] != "succeeded":
                    raise RuntimeError("Local diagnostic command did not succeed")
                print("REST -> MQTTS -> " + device_id + " -> MQTTS -> PostgreSQL: passed.")
                break
            time.sleep(1)
        else:
            raise RuntimeError("Local diagnostic command timed out")
        eid = cards[0]["eid"]
        refresh = api.post(
            "/api/v1/euiccs/" + eid + "/profiles/refresh", headers={"Idempotency-Key": str(uuid4())}
        )
        refresh.raise_for_status()
        operation = refresh.json()["operationId"]
        end = time.monotonic() + 100
        while time.monotonic() < end:
            result = api.get("/api/v1/operations/" + operation).json()
            if result["state"] == "succeeded":
                profiles = api.get("/api/v1/euiccs/" + eid + "/profiles").json()["items"]
                if not profiles or not result["resource_id"]:
                    raise RuntimeError("Local profile result missing")
                print("LOCAL mock Onomondo new/work/done -> persisted profiles: passed.")
                break
            if result["state"] in {"failed", "absent", "timed_out", "submission_unknown"}:
                raise RuntimeError("Local profile refresh failed")
            time.sleep(2)
        else:
            raise RuntimeError("Local profile refresh timed out")
    print(
        "API docs: https://localhost:8443/api/v1/docs "
        "(admin; password in .local/sgp32/operator_password)"
    )


def host_agent(port: str) -> None:
    env = os.environ.copy()
    env.update(
        {
            "AGENT_DEVICE_ID": "device-b",
            "AGENT_SIMULATE": "false",
            "AGENT_SERIAL_PORT": port,
            "AGENT_COMMAND_DB": str(PRIVATE / "hardware-commands.db"),
            "MQTT_HOST": "localhost",
            "MQTT_PORT": "8883",
            "MQTT_CA": str(PRIVATE / "ca.crt"),
            "MQTT_USERNAME": "device-b",
            "MQTT_PASSWORD_FILE": str(PRIVATE / "device_b_password"),
        }
    )
    # Explicitly keep backend secret variables out of the device process.
    for key in list(env):
        if key.startswith(("ONOMONDO_", "OPERATOR_", "DATABASE_")):
            del env[key]
    subprocess.run(  # noqa: S603
        [sys.executable, "-c", "from sgp32_agent.main import main; main()"], env=env, check=True
    )  # noqa: S603


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "up", "smoke", "stop", "agent"])
    parser.add_argument("--device", choices=["device-a", "device-b"], default="device-a")
    parser.add_argument("--port", help="Host UART port, used only by agent action")
    args = parser.parse_args()
    try:
        if args.action in {"prepare", "up"}:
            prepare()
        if args.action == "up":
            subprocess.run(  # noqa: S603
                [*COMPOSE, "up", "-d", "--build", "--wait", "--wait-timeout", "180"], check=True
            )  # noqa: S603
        if args.action == "smoke":
            smoke(args.device)
        if args.action == "stop":
            # Preserve all volumes, credentials and observations.
            subprocess.run([*COMPOSE, "stop"], check=True)  # noqa: S603
        if args.action == "agent":
            if not args.port:
                parser.error("--port is required for a physical modem")
            host_agent(args.port)
    except (OSError, RuntimeError, subprocess.CalledProcessError, httpx.HTTPError):
        print(
            "Local action failed; inspect service status and the local runbook. "
            "No secrets printed.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
