import json
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer

import httpx
from fastapi.testclient import TestClient
from sgp32.api import create_app
from sgp32.onomondo import Onomondo
from sgp32.worker import Worker

from tests.conftest import EID, SUCCESS
from tests.test_mqtt import broker  # noqa: F401


def test_fake_https_onomondo_server(broker, settings, db):  # noqa: F811
    _, certificates, _ = broker
    received = []
    states = [{"status": "new"}, {"status": "work"}, SUCCESS]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            assert self.path == "/api/orders/psmo"
            assert self.headers.get("Authorization") == "Bearer unit-test-only"
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append(json.loads(body))
            self.respond({"resourceId": "https-resource"}, 201)

        def do_GET(self):
            assert self.headers.get("Authorization") == "Bearer unit-test-only"
            if self.path == "/api/euicc":
                self.respond([{"eidValue": EID}])
            else:
                assert self.path == "/api/orders/psmo/https-resource"
                self.respond(states.pop(0))

        def respond(self, value, status=200):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class LocalServer(ThreadingHTTPServer):
        def server_bind(self):
            TCPServer.server_bind(self)
            self.server_name = "localhost"
            self.server_port = self.server_address[1]

    server = LocalServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificates / "server.crt", certificates / "server.key")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings.onomondo_api_url = f"https://localhost:{server.server_port}/api"
    settings.onomondo_schema_confirmed = True
    client_tls = ssl.create_default_context(cafile=str(certificates / "ca.crt"))
    upstream = Onomondo(settings, httpx.HTTPTransport(verify=client_tls))
    try:
        with TestClient(create_app(settings, db, upstream)) as api:
            api.auth = ("admin", "test")
            assert api.post("/api/v1/euiccs/sync").status_code == 200
            operation = api.post(f"/api/v1/euiccs/{EID}/profiles/refresh").json()["operationId"]
            worker = Worker(db, upstream, settings)
            now = time.time()
            for index in range(4):
                worker.tick(now + index * 15)
            result = api.get("/api/v1/operations/" + operation).json()
            assert result["resource_id"] == "https-resource"
            assert result["state"] == "succeeded"
            assert len(received) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
