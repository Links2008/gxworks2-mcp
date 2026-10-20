"""MELSEC device (软元件) addresses: parsing, canonical rendering, MC-protocol codes.

Device numbers are not all decimal: on MELSEC, X/Y/B/W/SB/SW/DX/DY and ZR are
hexadecimal devices, while M/L/F/V/D/T/C are decimal. That distinction is preserved here
because it decides both the value encoded in an MC protocol frame and how a device is
written back into IL. The bases below follow the QnA-compatible 3E frame:

* X, Y, B, W, SB, SW, DX, DY, ZR -> base 16
* everything else               -> base 10

This matches the convention used by reference MC-protocol implementations and by
GX Works2 when device display is set to hexadecimal (the default for X/Y).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal

Kind = Literal["bit", "word"]

#: Device codes of the binary MC protocol. On Q/L/QnA/iQ-L the code is sent as ONE byte;
#: only iQ-R uses a two-byte code, which this client does not target.
#: T0 / C10 are the timer and counter current-value words (TN / CN); the ST family is the
#: retentive timer (SH-080008 device code table).
ALIASES = {"T": "TN", "C": "CN"}
ST_RETENTIVE = {"STN": 0xC8, "STS": 0xC7, "STC": 0xC6}

DEVICE_CODES: dict[str, int] = {
    # bit devices
    "SM": 0x91,
    "X": 0x9C,
    "Y": 0x9D,
    "M": 0x90,
    "L": 0x92,
    "F": 0x93,
    "V": 0x94,
    "B": 0xA0,
    "SB": 0xA1,
    "DX": 0xA2,
    "DY": 0xA3,
    "S": 0x98,
    # word devices
    "SD": 0xA9,
    "D": 0xA8,
    "W": 0xB4,
    "SW": 0xB5,
    "TN": 0xC2,
    "CN": 0xC5,
    "Z": 0xCC,
    "R": 0xAF,
    "ZR": 0xB0,
    # timer / counter contact and coil
    "TS": 0xC1,
    "TC": 0xC0,
    "CS": 0xC4,
    "CC": 0xC3,
}

#: Devices whose number is hexadecimal when encoded in a frame and shown in GX Works2.
#: NOTE: on FX-family CPUs an X/Y number with a leading zero (X010) may be octal
#: depending on the CPU parameter; MC frame encoding is per-CPU-family and this module
#: applies the Q/L convention. Parse straight from a GX Works2 export when in doubt.
HEX_DEVICES = {"X", "Y", "B", "W", "SB", "SW", "DX", "DY", "ZR"}

WORD_ONLY = {"D", "W", "SW", "TN", "CN", "Z", "R", "ZR", "SD"}
BIT_ONLY = {"SM", "X", "Y", "M", "L", "F", "V", "B", "SB", "DX", "DY", "S", "TS", "TC", "CS", "CC"}

#: Longest names first so ZR wins over Z, SM over S and STN over S.
_DEVICE_NAMES = sorted(set(DEVICE_CODES) | set(ST_RETENTIVE) | {"T", "C"}, key=len, reverse=True)

_DEVICE_RE = re.compile(
    r"^(?P<dev>" + "|".join(_DEVICE_NAMES) + r")(?P<index>[0-9A-Fa-f]+)(?:[.](?P<bit>[0-9A-Fa-f]))?$",
    re.IGNORECASE,
)

#: Devices the reference manual marks as write-protected for external access.
#: S (step relay) is documented "Read only"; the rest are CPU-internal.
READ_ONLY_DEVICES = {"S"}


class DeviceError(ValueError):
    """Raised when a device expression cannot be interpreted."""


@dataclass(frozen=True)
class DeviceRef:
    """One concrete MELSEC device point.

    device: canonical family name (D, M, X, ...).
    index: numeric device number, already decoded from its base.
    bit: bit position inside a word device (D100.3), else None.
    raw: the spelling the caller used.
    """

    device: str
    index: int
    bit: int | None = None
    raw: str = ""

    @property
    def base(self) -> int:
        return 16 if self.device in HEX_DEVICES else 10

    @property
    def kind(self) -> Kind:
        if self.bit is not None:
            return "bit"
        return "bit" if self.device in BIT_ONLY or self.device in ST_RETENTIVE else "word"

    @property
    def is_timer_counter_contact(self) -> bool:
        return self.device in {"TS", "TC", "CS", "CC", "STS", "STC"}

    @property
    def is_read_only(self) -> bool:
        """S (step relay) is documented as read-only for external devices."""
        return self.device in READ_ONLY_DEVICES

    def __str__(self) -> str:
        base = self.device + (format(self.index, "X") if self.base == 16 else str(self.index))
        return base if self.bit is None else base + "." + format(self.bit, "X")

    def canonical(self) -> str:
        return str(self)


def _parse_index(text: str, base: int, raw: str) -> int:
    try:
        return int(text, base)
    except ValueError as exc:
        raise DeviceError(
            "device number " + repr(text) + " in " + repr(raw) + " is not valid base-" + str(base)
        ) from exc


def parse_device(text: str) -> DeviceRef:
    """Parse one device expression: M100, D100.3, X1A, ZR512, T0."""

    if not isinstance(text, str):
        raise DeviceError("device must be a string, got " + type(text).__name__)
    cleaned = text.strip().upper().replace(" ", "")
    if not cleaned:
        raise DeviceError("empty device expression")
    match = _DEVICE_RE.match(cleaned)
    if not match:
        raise DeviceError(
            "cannot parse device " + repr(text) + "; expected a family name plus a device "
            "number, optionally with a bit index such as D100.3"
        )
    dev = match.group("dev")
    if dev in ALIASES:
        dev = ALIASES[dev]
    if dev not in DEVICE_CODES and dev not in ST_RETENTIVE:
        raise DeviceError("unsupported device family " + repr(dev))
    base = 16 if dev in HEX_DEVICES else 10
    index = _parse_index(match.group("index"), base, text)
    bit_text = match.group("bit")
    bit = int(bit_text, 16) if bit_text is not None else None
    return DeviceRef(device=dev, index=index, bit=bit, raw=text.strip())


@dataclass(frozen=True)
class DeviceRange:
    """An inclusive device range such as D100-D119 or M0-M31."""

    start: DeviceRef
    count: int

    @property
    def end(self) -> DeviceRef:
        return DeviceRef(self.start.device, self.start.index + self.count - 1, self.start.bit)

    def __str__(self) -> str:
        return str(self.start) + ".." + str(self.end) + " (" + str(self.count) + " points)"


_RANGE_SEP = re.compile(r"\s*(?:\.\.|-|~|:)\s*")


def parse_range(text: str) -> DeviceRange:
    """Parse D100-D119, D100..D119, D100~D119, "D100 5", or a bare device."""

    if not isinstance(text, str) or not text.strip():
        raise DeviceError("empty device range")
    cleaned = text.strip()
    spaced = re.match(r"^(?P<dev>[A-Za-z]{1,3}[0-9A-Fa-f]+(?:[.][0-9A-Fa-f])?)\s+(?P<count>\d+)$", cleaned)
    if spaced:
        start = parse_device(spaced.group("dev"))
        count = int(spaced.group("count"))
        if count <= 0:
            raise DeviceError("range length must be positive, got " + str(count))
        return DeviceRange(start, count)
    parts = _RANGE_SEP.split(cleaned)
    if len(parts) == 2:
        start = parse_device(parts[0])
        end = parse_device(parts[1])
        if start.device != end.device:
            raise DeviceError("range endpoints differ in device family: " + repr(cleaned))
        if end.index < start.index:
            raise DeviceError("range end precedes start: " + repr(cleaned))
        return DeviceRange(start, end.index - start.index + 1)
    return DeviceRange(parse_device(cleaned), 1)


def expand_range(rng: DeviceRange) -> list[DeviceRef]:
    """List every point of a range (guarding against absurd sizes)."""

    if rng.count > 1_000_000:
        raise DeviceError("range too large: " + str(rng.count) + " points")
    return [DeviceRef(rng.start.device, rng.start.index + i, rng.start.bit) for i in range(rng.count)]


def device_code(ref: DeviceRef | str) -> int:
    """MC-protocol device code (one byte on Q/L/QnA/iQ-L)."""

    ref = parse_device(ref) if isinstance(ref, str) else ref
    if ref.device in ST_RETENTIVE:
        return ST_RETENTIVE[ref.device]
    try:
        return DEVICE_CODES[ref.device]
    except KeyError as exc:  # pragma: no cover - guarded by parse_device
        raise DeviceError("no MC-protocol device code for " + repr(ref.device)) from exc


def describe(text: str) -> dict[str, object]:
    """Human-readable summary used by the MCP tools."""

    ref = parse_device(text)
    return {
        "input": text,
        "canonical": str(ref),
        "family": ref.device,
        "index": ref.index,
        "index_base": ref.base,
        "bit": ref.bit,
        "kind": ref.kind,
        "mc_device_code": "0x" + format(device_code(ref), "02X"),
        "word_only": ref.device in WORD_ONLY,
        "bit_only": ref.device in BIT_ONLY,
        "read_only": ref.is_read_only,
        "s_is_step_relay": ref.device == "S",
    }


def group_by_family(refs: Iterable[DeviceRef]) -> dict[str, list[DeviceRef]]:
    """Group devices per family, preserving input order."""

    grouped: dict[str, list[DeviceRef]] = {}
    for ref in refs:
        grouped.setdefault(ref.device, []).append(ref)
    return grouped
