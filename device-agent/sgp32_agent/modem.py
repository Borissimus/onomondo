import ipaddress
import re
from typing import Any

from sgp32_common.security import scrub

from sgp32_agent.serial_transport import ModemError, ModemTransport


class ModemDiagnostics:
    def __init__(self, transport: ModemTransport, *, allow_imsi: bool = False):
        self.transport, self.allow_imsi = transport, allow_imsi

    def collect(self, include_sensitive: bool = False) -> dict[str, Any]:
        errors: list[str] = []

        def command(value: str, prefix: str | None = None) -> str:
            try:
                return "\n".join(self.transport.send_command(value, prefix=prefix))
            except ModemError as exc:
                errors.append(f"{value}: {exc}")
                return ""

        identity = command("ATI")
        ready = "READY" in command("AT+CPIN?", "+CPIN:")
        iccid = command("AT+CICCID", "+ICCID:")
        imsi = command("AT+CIMI") if include_sensitive and self.allow_imsi else ""
        operator = command("AT+COPS?", "+COPS:")
        registration = command("AT+CEREG?", "+CEREG:")
        attached = command("AT+CGATT?", "+CGATT:")
        pdp = command("AT+CGDCONT?", "+CGDCONT:")
        signal = command("AT+CSQ", "+CSQ:")
        addresses = command("AT+CGPADDR", "+CGPADDR:")
        match = re.search(r"\+CEREG:\s*\d+,(\d+)", registration)
        status = int(match[1]) if match else -1
        name = re.search(r'\+COPS:\s*\d+,\d+,"([^"]+)"', operator)
        op: dict[str, str] = {"name": name[1]} if name else {}
        if name and re.fullmatch(r"\d{5,6}", name[1]):
            op = {"mcc": name[1][:3], "mnc": name[1][3:]}
        match = re.search(r"\+CSQ:\s*(\d+)", signal)
        csq = int(match[1]) if match and match[1] != "99" else None
        ips = []
        for line in addresses.splitlines():
            for candidate in line.partition(",")[2].split(","):
                try:
                    ip = ipaddress.ip_address(candidate.strip(' "'))
                    if not ip.is_unspecified:
                        ips.append(str(ip))
                except ValueError:
                    pass
        return {
            "status": "failed" if errors else "succeeded",
            "identity": str(scrub(identity))[:256],
            "simReady": ready,
            "registration": {
                1: "registered_home",
                5: "registered_roaming",
                2: "searching",
                3: "denied",
                0: "not_registered",
            }.get(status, "unknown"),
            "operator": op,
            "packetAttached": bool(re.search(r"\+CGATT:\s*1\b", attached)),
            "signal": {"csq": csq},
            "ipAddresses": ips,
            "pdpContexts": [str(scrub(line)) for line in pdp.splitlines()][:16],
            "identifiers": {
                "iccid": "[REDACTED]" if iccid else "unavailable",
                "imsi": "[REDACTED]" if imsi else "not_collected",
            },
            "errors": errors,
        }


class SimulatedModem:
    """Explicit simulator, never silently substituted for failed hardware."""

    def send_command(
        self, command: str, *, timeout: float = 5, prefix: str | None = None
    ) -> list[str]:
        return {
            "ATI": ["SIMULATED A7670E"],
            "AT+CPIN?": ["+CPIN: READY"],
            "AT+CICCID": ["+ICCID: 8945000000000000001"],
            "AT+CIMI": ["001010123456789"],
            "AT+COPS?": ['+COPS: 0,0,"Test Network",7'],
            "AT+CEREG?": ["+CEREG: 0,5"],
            "AT+CGATT?": ["+CGATT: 1"],
            "AT+CGDCONT?": ['+CGDCONT: 1,"IP","test.apn"'],
            "AT+CSQ": ["+CSQ: 24,99"],
            "AT+CGPADDR": ['+CGPADDR: 1,"192.0.2.2"'],
        }.get(command, [])
