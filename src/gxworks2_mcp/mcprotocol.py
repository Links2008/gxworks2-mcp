"""MC protocol client (QnA-compatible 3E frame, binary), dependency-free.

Frame layout and byte order were verified byte-for-byte against a reference
implementation (pymcprotocol) and captured as golden frames in the test suite.

    request  = 50 00 | network(1) | pc(1) | module-io(2) | station(1)
               | request-data-length(2) | monitor-timer(2) | command(2)
               | subcommand(2) | data
    response = D0 00 | network(1) | pc(1) | module-io(2) | station(1)
               | response-data-length(2) | end-code(2) | data

**Byte order is the trap**: only the two-byte subheader is big-endian; every other
numeric field - the length, the monitor timer, the command and sub-command, the device
number, the point count and every data word - is little-endian. A device reference is
a 3-byte little-endian number followed by a **1-byte** device code on Q/L/QnA/iQ-L;
only iQ-R uses 4-byte numbers and 2-byte codes.

The length field counts from the monitor timer onward in a request, and from the end
code onward in a response.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from typing import Iterable, Literal

from .devices import DeviceRange, DeviceRef, device_code, parse_device, parse_range

Transport = Literal["tcp", "udp"]
RemoteAction = Literal["run", "stop", "pause", "latch_clear"]

#: End codes seen most often; the full table is in the reference manual.
END_CODES: dict[int, str] = {
    0x0000: "normal completion",
    0xC050: "the request data is longer than the maximum",
    0xC051: "the request data length does not match the number of data",
    0xC052: "the request data is shorter than the minimum",
    0xC053: "the number of points is out of range",
    0xC054: "the head device number is out of range",
    0xC055: "the device code is out of range",
    0xC056: "the head device number + number of points is out of range",
    0xC057: "the sub-command is invalid",
    0xC058: "the command is invalid",
    0xC059: "the network number is out of range",
    0xC05A: "the PC number is out of range",
    0xC05B: "the request destination module I/O number is out of range",
    0xC05C: "the request destination module station number is out of range",
    0xC05D: "the monitor timer is invalid",
    0xC05E: "the request data is invalid",
    0xC05F: "the request is unsupported by the target CPU",
    0xC060: "the data cannot be written to the target device",
    0xC061: "the target device is write-protected (remote password / device write protect)",
    0xC070: "the target CPU is busy",
    0xC0B5: "the target CPU rejects the request in its current state (e.g. write while running)",
}

COMMANDS = {
    "batch_read": 0x0401,
    "batch_write": 0x1401,
    "random_read": 0x0403,
    "random_write": 0x1402,
    "monitor": 0x0801,
    "remote_run": 0x1001,
    "remote_stop": 0x1002,
    "remote_pause": 0x1003,
    "remote_latch_clear": 0x1005,
    "remote_password_unlock": 0x1630,
    "cpu_type_read": 0x0101,
}

SUBCMD_WORD = 0x0000
SUBCMD_BIT = 0x0001          # bit units, two points packed per byte
SUBCMD_BIT_WORD = 0x0002     # bit units, one bit per word

MAX_WORDS = 960
MAX_BITS = 7168


class MCProtocolError(RuntimeError):
    """Protocol-level failure: bad end code, timeout, or a malformed response."""

    def __init__(self, message: str, end_code: int | None = None, raw: bytes | None = None) -> None:
        super().__init__(message)
        self.end_code = end_code
        self.raw = raw

    @property
    def description(self) -> str:
        if self.end_code is None:
            return "no end code"
        return END_CODES.get(self.end_code, "unknown end code")


@dataclass
class MCConfig:
    host: str = "127.0.0.1"
    port: int = 5000
    timeout: float = 3.0
    network: int = 0
    pc: int = 0xFF
    dest_module_io: int = 0x03FF
    dest_station: int = 0
    #: Monitor timer in 250 ms units; the PLC aborts the request when it elapses.
    monitor_timer: int = 4
    transport: Transport = "tcp"


def _le16(value: int) -> bytes:
    return struct.pack("<H", value & 0xFFFF)


def _le24(value: int) -> bytes:
    """A device number is a 3-byte little-endian integer on Q/L/QnA/iQ-L."""

    return struct.pack("<I", value & 0xFFFFFF)[:3]


def build_request(config: MCConfig, command: int, subcommand: int, data: bytes = b"") -> bytes:
    """Assemble one 3E binary request frame."""

    # Length counts monitor timer + command + subcommand + data.
    tail = _le16(config.monitor_timer) + _le16(command) + _le16(subcommand) + data
    return (
        b"\x50\x00"                                   # subheader: the only big-endian field
        + bytes([config.network & 0xFF])
        + bytes([config.pc & 0xFF])
        + _le16(config.dest_module_io)
        + bytes([config.dest_station & 0xFF])
        + _le16(len(tail))
        + tail
    )


def parse_response(response: bytes) -> tuple[int, bytes]:
    """Return (end_code, payload) from a 3E response frame."""

    if len(response) < 11:
        raise MCProtocolError("response too short: " + response.hex(), raw=response)
    if response[0:2] != b"\xD0\x00":
        raise MCProtocolError(
            "unexpected response subheader " + response[0:2].hex() + " (expected d000)", raw=response
        )
    length = struct.unpack("<H", response[7:9])[0]
    end_code = struct.unpack("<H", response[9:11])[0]
    payload = response[11 : 9 + length]
    return end_code, payload


class MCClient:
    """Synchronous MC protocol client; use it as a context manager."""

    def __init__(self, config: MCConfig | None = None) -> None:
        self.config = config or MCConfig()
        self._socket: socket.socket | None = None

    # -- transport ---------------------------------------------------------

    def connect(self) -> "MCClient":
        if self._socket is not None:
            return self
        kind = socket.SOCK_STREAM if self.config.transport == "tcp" else socket.SOCK_DGRAM
        sock = socket.socket(socket.AF_INET, kind)
        sock.settimeout(self.config.timeout)
        try:
            sock.connect((self.config.host, self.config.port))
        except OSError as exc:
            sock.close()
            raise MCProtocolError(
                "cannot reach " + self.config.host + ":" + str(self.config.port) + " (" + str(exc) + "); "
                "check the PLC/emulator address, the port (5000 for Q/L over TCP), and that the "
                "Ethernet module is configured for MC protocol with an open connection"
            ) from exc
        self._socket = sock
        return self

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None

    def __enter__(self) -> "MCClient":
        return self.connect()

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _exchange(self, frame: bytes) -> bytes:
        if self._socket is None:
            self.connect()
        assert self._socket is not None
        try:
            self._socket.sendall(frame)
            if self.config.transport == "tcp":
                head = self._recv_exact(9)
                length = struct.unpack("<H", head[7:9])[0]
                return head + self._recv_exact(length)
            return self._socket.recv(65535)
        except socket.timeout as exc:
            raise MCProtocolError(
                "timeout after " + str(self.config.timeout) + "s waiting for the PLC"
            ) from exc

    def _recv_exact(self, size: int) -> bytes:
        assert self._socket is not None
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = self._socket.recv(remaining)
            if not chunk:
                raise MCProtocolError("connection closed by the PLC")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def request(self, command: int, subcommand: int, data: bytes = b"") -> bytes:
        frame = build_request(self.config, command, subcommand, data)
        response = self._exchange(frame)
        end_code, payload = parse_response(response)
        if end_code != 0:
            raise MCProtocolError(
                "PLC returned end code 0x"
                + format(end_code, "04X")
                + " ("
                + END_CODES.get(end_code, "unknown")
                + ")",
                end_code=end_code,
                raw=response,
            )
        return payload

    # -- reads -------------------------------------------------------------

    def read_words(self, device: str, count: int | None = None) -> list[int]:
        """Read 16-bit words from a word device (D, W, R, ZR, TN, CN, ...)."""

        ref, points = _resolve(device, count)
        if points > MAX_WORDS:
            raise MCProtocolError("batch read limited to " + str(MAX_WORDS) + " words, requested " + str(points))
        payload = self.request(
            COMMANDS["batch_read"],
            SUBCMD_WORD,
            _le24(ref.index) + bytes([device_code(ref)]) + _le16(points),
        )
        if len(payload) < points * 2:
            raise MCProtocolError("short word payload: " + str(len(payload)) + " bytes")
        return list(struct.unpack("<" + "H" * points, payload[: points * 2]))

    def read_bits(self, device: str, count: int | None = None, *, word_packed: bool = False) -> list[int]:
        """Read bits: two points per byte (sub-command 1) or one bit per word (sub-command 2)."""

        ref, points = _resolve(device, count)
        if points > MAX_BITS:
            raise MCProtocolError("batch read limited to " + str(MAX_BITS) + " points, requested " + str(points))
        sub = SUBCMD_BIT_WORD if word_packed else SUBCMD_BIT
        payload = self.request(
            COMMANDS["batch_read"],
            sub,
            _le24(ref.index) + bytes([device_code(ref)]) + _le16(points),
        )
        return unpack_bits(payload, points, word_packed)

    def read_typed(self, device: str, count: int | None = None) -> list[int]:
        """Read bits or words depending on the device family."""

        ref, points = _resolve(device, count)
        if ref.bit is not None:
            words = self.read_words(str(DeviceRef(ref.device, ref.index)), 1)
            return [(words[0] >> ref.bit) & 1]
        if ref.kind == "bit":
            return self.read_bits(str(ref), points)
        return self.read_words(str(ref), points)

    # -- writes ------------------------------------------------------------

    def write_words(self, device: str, values: Iterable[int]) -> int:
        values = [value & 0xFFFF for value in values]
        ref = parse_device(device)
        if not values:
            raise MCProtocolError("no values to write")
        if len(values) > MAX_WORDS:
            raise MCProtocolError("batch write limited to " + str(MAX_WORDS) + " words")
        body = _le24(ref.index) + bytes([device_code(ref)]) + _le16(len(values))
        body += struct.pack("<" + "H" * len(values), *values)
        self.request(COMMANDS["batch_write"], SUBCMD_WORD, body)
        return len(values)

    def write_bits(self, device: str, values: Iterable[int]) -> int:
        bits = [1 if value else 0 for value in values]
        ref = parse_device(device)
        if not bits:
            raise MCProtocolError("no values to write")
        if len(bits) > MAX_BITS:
            raise MCProtocolError("batch write limited to " + str(MAX_BITS) + " points")
        body = _le24(ref.index) + bytes([device_code(ref)]) + _le16(len(bits)) + pack_bits(bits)
        self.request(COMMANDS["batch_write"], SUBCMD_BIT, body)
        return len(bits)

    # -- remote control -----------------------------------------------------

    def remote(self, action: RemoteAction = "run") -> dict[str, object]:
        """Remote RUN / STOP / PAUSE / latch clear.

        RUN carries two words (clear mode, force execution); STOP and PAUSE carry the
        clear-mode word only; latch clear carries a single word as well.
        """

        if action == "run":
            command, data = COMMANDS["remote_run"], _le16(1) + _le16(1)
        elif action == "stop":
            command, data = COMMANDS["remote_stop"], _le16(1)
        elif action == "pause":
            command, data = COMMANDS["remote_pause"], _le16(1)
        elif action == "latch_clear":
            command, data = COMMANDS["remote_latch_clear"], _le16(1)
        else:  # pragma: no cover - guarded by the annotation
            raise MCProtocolError("unknown remote action " + repr(action))
        payload = self.request(command, 0x0000, data)
        return {"action": action, "end_code": 0, "payload": payload.hex()}

    def cpu_type(self) -> str:
        payload = self.request(COMMANDS["cpu_type_read"], 0x0000, b"")
        return payload.decode("ascii", errors="replace").strip("\x00 ")

    def cpu_run_state(self) -> dict[str, object]:
        """Read SM0, the CPU operation-status relay, to confirm RUN/STOP.

        The reference manual notes that a remote RUN returns normal completion even when
        the CPU's physical RUN/STOP switch is in STOP, so the end code alone does not prove
        the CPU actually started. Reading SM0 afterwards is what distinguishes the two.
        Confirm the special-relay number against your CPU manual if it disagrees.
        """

        bits = self.read_bits("SM0", 1)
        running = bool(bits and bits[0])
        return {
            "sm0": bits[0] if bits else None,
            "state": "RUN" if running else "STOP or switch in STOP",
            "note": "SM0 is the CPU operation-status relay; remote RUN can complete normally "
            "while the physical RUN/STOP switch keeps the CPU stopped",
        }


def _resolve(device: str, count: int | None) -> tuple[DeviceRef, int]:
    if count is not None:
        ref = parse_device(device)
        if count <= 0:
            raise MCProtocolError("count must be positive")
        return ref, int(count)
    rng: DeviceRange = parse_range(device)
    return rng.start, rng.count


def unpack_bits(payload: bytes, points: int, word_packed: bool = False) -> list[int]:
    """Decode the data area of a bit-unit batch read.

    Sub-command 0x0001 packs two points per byte with the lower nibble holding the
    even-numbered point; sub-command 0x0002 returns one bit per word.
    """

    bits: list[int] = []
    for index in range(points):
        if word_packed:
            byte_index = index // 2
            value = payload[byte_index] if byte_index < len(payload) else 0
            bits.append((value >> (index % 8)) & 1)
        else:
            # Even-numbered points live in bit 4 of the byte, odd ones in bit 0.
            byte_index = index // 2
            value = payload[byte_index] if byte_index < len(payload) else 0
            bits.append(1 if (value & 0x10 if index % 2 == 0 else value & 0x01) else 0)
    return bits


def pack_bits(bits: Iterable[int]) -> bytes:
    """Encode bits for a bit-unit batch write (two points per byte)."""

    values = [1 if bit else 0 for bit in bits]
    packed = bytearray((len(values) + 1) // 2)
    for index, bit in enumerate(values):
        if bit:
            # Even-numbered points occupy bit 4 of their byte, odd ones bit 0.
            packed[index // 2] |= 0x10 if index % 2 == 0 else 0x01
    return bytes(packed)


def probe(
    host: str,
    port: int = 5000,
    timeout: float = 2.0,
    transport: Transport = "tcp",
) -> dict[str, object]:
    """Check whether something MC-protocol-shaped answers at host:port."""

    config = MCConfig(host=host, port=port, timeout=timeout, transport=transport)
    try:
        with MCClient(config) as client:
            cpu = client.cpu_type()
        return {"reachable": True, "host": host, "port": port, "transport": transport, "cpu_type": cpu}
    except MCProtocolError as exc:
        return {
            "reachable": False,
            "host": host,
            "port": port,
            "transport": transport,
            "error": str(exc),
            "end_code": None if exc.end_code is None else "0x" + format(exc.end_code, "04X"),
            "end_code_text": exc.description,
        }
