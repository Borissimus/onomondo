import queue
import threading
import time
from collections.abc import Callable
from typing import Protocol

import serial
from sgp32_common.security import scrub

URC_PREFIXES = (
    "+CEREG:",
    "+CREG:",
    "+CGREG:",
    "+CGEV:",
    "+MSTK:",
    "+CMTI:",
    "+CPIN:",
    "RDY",
    "SMS Ready",
    "PB DONE",
)


class ModemTransport(Protocol):
    def send_command(
        self, command: str, *, timeout: float = 5, prefix: str | None = None
    ) -> list[str]: ...


class ModemError(Exception):
    pass


class SerialTransport:
    """Single owner, serialized commands and independent URC reader; never answers MSTK."""

    def __init__(self, port: str, baud: int = 115200, on_urc: Callable[[str], None] | None = None):
        self.port, self.baud = port, baud
        self.on_urc = on_urc or (lambda _: None)
        self._command_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._responses: queue.Queue[str] | None = None
        self._prefix: str | None = None
        self._serial: serial.Serial | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._quarantine_until = 0.0
        self._awaiting_final = False

    def connect(self) -> None:
        if self._awaiting_final:
            raise ModemError("modem_resynchronizing")
        if self._serial and self._serial.is_open:
            return
        if time.monotonic() < self._quarantine_until:
            raise ModemError("modem_resynchronizing")
        try:
            port = serial.Serial(self.port, self.baud, timeout=0.1, write_timeout=2, exclusive=True)
            port.reset_input_buffer()
        except (OSError, serial.SerialException):
            raise ModemError("serial_unavailable") from None
        self._serial = port
        self._stop.clear()
        self._thread = threading.Thread(target=self._read, args=(port,), daemon=True)
        self._thread.start()

    def _read(self, port: serial.Serial) -> None:
        buffer = bytearray()
        try:
            while not self._stop.is_set():
                chunk = port.read(256)
                if not chunk:
                    continue
                buffer.extend(chunk)
                if len(buffer) > 8192:
                    raise ModemError("serial_line_limit")
                while b"\n" in buffer:
                    raw, _, rest = buffer.partition(b"\n")
                    buffer = bytearray(rest)
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    with self._state_lock:
                        pending, prefix = self._responses, self._prefix
                        solicited = prefix is not None and line.startswith(prefix)
                        # Query CEREG response has n,stat; a bare stat is always unsolicited.
                        if prefix == "+CEREG:" and "," not in line:
                            solicited = False
                        if line.startswith(URC_PREFIXES) and not solicited:
                            self.on_urc(
                                "+MSTK: [REDACTED]"
                                if line.startswith("+MSTK:")
                                else str(scrub(line))
                            )
                        elif pending is not None:
                            try:
                                pending.put_nowait(line)
                            except queue.Full:
                                raise ModemError("serial_response_limit") from None
                        else:
                            if line in {"OK", "ERROR"} or line.startswith(
                                ("+CME ERROR:", "+CMS ERROR:")
                            ):
                                self._awaiting_final = False
                            self.on_urc(
                                "+MSTK: [REDACTED]"
                                if line.startswith("+MSTK:")
                                else str(scrub(line))
                            )
        except (OSError, serial.SerialException, ModemError):
            with self._state_lock:
                if self._responses:
                    try:
                        self._responses.put_nowait("__DISCONNECTED__")
                    except queue.Full:
                        pass
            try:
                port.close()
            except OSError:
                pass

    def send_command(
        self, command: str, *, timeout: float = 5, prefix: str | None = None
    ) -> list[str]:
        if not command.startswith("AT") or "\r" in command or "\n" in command:
            raise ModemError("invalid_local_command")
        with self._command_lock:
            self.connect()
            responses: queue.Queue[str] = queue.Queue(maxsize=256)
            with self._state_lock:
                self._responses, self._prefix = responses, prefix
            rows: list[str] = []
            failed = False
            try:
                assert self._serial
                self._serial.write((command + "\r").encode("ascii"))
                end = time.monotonic() + timeout
                while True:
                    try:
                        line = responses.get(timeout=max(0.001, end - time.monotonic()))
                    except queue.Empty:
                        self._awaiting_final = True
                        raise ModemError("at_timeout") from None
                    if line == "OK":
                        return rows
                    if line == "ERROR" or line.startswith(("+CME ERROR:", "+CMS ERROR:")):
                        raise ModemError("at_error")
                    if line == "__DISCONNECTED__":
                        failed = True
                        raise ModemError("serial_disconnected")
                    if line != command:
                        rows.append(line)
                    if time.monotonic() >= end:
                        self._awaiting_final = True
                        raise ModemError("at_timeout")
            except (OSError, serial.SerialException):
                failed = True
                raise ModemError("serial_disconnected") from None
            finally:
                with self._state_lock:
                    self._responses, self._prefix = None, None
                if failed:
                    self.close()
                    # Discard late replies before reopening; commands are not pipelined.
                    self._quarantine_until = time.monotonic() + max(2, timeout)

    def close(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=0.5)
        if self._serial:
            self._serial.close()
        self._serial = None
