"""Cross-check our MC frames and device codes against pymcprotocol (optional dev tool).

Run with pymcprotocol importable:

    py -m pip install pymcprotocol
    py tests/crosscheck_pymcprotocol.py

This is not part of the unit suite because pymcprotocol is not a runtime dependency.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from pymcprotocol import Type3E
except ImportError:
    raise SystemExit("pymcprotocol is not installed: py -m pip install pymcprotocol")

from gxworks2_mcp.devices import DEVICE_CODES, device_code, parse_device  # noqa: E402
from gxworks2_mcp.mcprotocol import MCConfig, build_request  # noqa: E402

CAPTURED: list[bytes] = []


class _Sock:
    def send(self, data: bytes) -> int:
        CAPTURED.append(bytes(data))
        return len(data)

    def recv(self, size: int) -> bytes:
        return bytes.fromhex("D00000FF03FF0000020000") + b"\x12\x34"

    def settimeout(self, timeout: float) -> None:
        pass

    def close(self) -> None:
        pass


def reference_frame(call) -> bytes:
    client = Type3E(plctype="Q")
    client._sock = _Sock()
    client._is_connected = True
    client.connect = lambda *a, **k: None  # type: ignore[assignment]
    CAPTURED.clear()
    call(client)
    return CAPTURED[0]


CASES = [
    (
        "batch read D100 x3",
        lambda c: c.batchread_wordunits(headdevice="D100", readsize=3),
        lambda: build_request(MCConfig(), 0x0401, 0x0000, bytes([0x64, 0, 0]) + bytes([0xA8]) + bytes([3, 0])),
    ),
    (
        "batch read M0 x8",
        lambda c: c.batchread_bitunits(headdevice="M0", readsize=8),
        lambda: build_request(MCConfig(), 0x0401, 0x0001, bytes([0, 0, 0]) + bytes([0x90]) + bytes([8, 0])),
    ),
    (
        "batch read X1A x4 (hex device)",
        lambda c: c.batchread_bitunits(headdevice="X1A", readsize=4),
        lambda: build_request(MCConfig(), 0x0401, 0x0001, bytes([0x1A, 0, 0]) + bytes([0x9C]) + bytes([4, 0])),
    ),
    (
        "batch write D100 [1,2]",
        lambda c: c.batchwrite_wordunits(headdevice="D100", values=[1, 2]),
        lambda: build_request(
            MCConfig(), 0x1401, 0x0000, bytes([0x64, 0, 0]) + bytes([0xA8]) + bytes([2, 0]) + bytes([1, 0, 2, 0])
        ),
    ),
    (
        "batch write Y0-Y7",
        lambda c: c.batchwrite_bitunits(headdevice="Y0", values=[1, 0, 1, 0, 0, 0, 1, 1]),
        lambda: build_request(
            MCConfig(), 0x1401, 0x0001, bytes([0, 0, 0]) + bytes([0x9D]) + bytes([8, 0]) + bytes.fromhex("10100011")
        ),
    ),
    (
        "remote run",
        lambda c: c.remote_run(clear_mode=1),
        lambda: build_request(MCConfig(), 0x1001, 0x0000, bytes([1, 0, 1, 0])),
    ),
    (
        "remote stop",
        lambda c: c.remote_stop(),
        lambda: build_request(MCConfig(), 0x1002, 0x0000, bytes([1, 0])),
    ),
    (
        "remote pause",
        lambda c: c.remote_pause(),
        lambda: build_request(MCConfig(), 0x1003, 0x0000, bytes([1, 0])),
    ),
    (
        "remote latch clear",
        lambda c: c.remote_latchclear(),
        lambda: build_request(MCConfig(), 0x1005, 0x0000, bytes([1, 0])),
    ),
    (
        "cpu type",
        lambda c: c.read_cputype(),
        lambda: build_request(MCConfig(), 0x0101, 0x0000),
    ),
]


def main() -> int:
    failures = 0
    for label, reference, ours in CASES:
        expected = reference_frame(reference)
        actual = ours()
        ok = expected == actual
        print(("OK   " if ok else "FAIL ") + label)
        if not ok:
            failures += 1
            print("  reference:", expected.hex(" "))
            print("  ours     :", actual.hex(" "))

    # Device codes: compare against the reference package's own table.
    from pymcprotocol.mcprotocolconst import DeviceConstants

    pairs = {
        "SM": DeviceConstants.SM_DEVICE,
        "SD": DeviceConstants.SD_DEVICE,
        "X": DeviceConstants.X_DEVICE,
        "Y": DeviceConstants.Y_DEVICE,
        "M": DeviceConstants.M_DEVICE,
        "L": DeviceConstants.L_DEVICE,
        "F": DeviceConstants.F_DEVICE,
        "V": DeviceConstants.V_DEVICE,
        "B": DeviceConstants.B_DEVICE,
        "D": DeviceConstants.D_DEVICE,
        "W": DeviceConstants.W_DEVICE,
        "TS": DeviceConstants.TS_DEVICE,
        "TC": DeviceConstants.TC_DEVICE,
        "TN": DeviceConstants.TN_DEVICE,
        "CS": DeviceConstants.CS_DEVICE,
        "CC": DeviceConstants.CC_DEVICE,
        "CN": DeviceConstants.CN_DEVICE,
        "SB": DeviceConstants.SB_DEVICE,
        "SW": DeviceConstants.SW_DEVICE,
        "R": DeviceConstants.R_DEVICE,
        "ZR": DeviceConstants.ZR_DEVICE,
    }
    for name, reference_code in pairs.items():
        ours_code = DEVICE_CODES.get(name)
        if ours_code != reference_code:
            failures += 1
            print("FAIL device code " + name + ": ours 0x%02X reference 0x%02X" % (ours_code or 0, reference_code))
        else:
            print("OK   device code " + name + " = 0x%02X" % ours_code)

    print("device parse check:", parse_device("X1A"), "code 0x%02X" % device_code("X1A"))
    print("FAILURES:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
