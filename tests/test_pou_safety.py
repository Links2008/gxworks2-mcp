"""Offline fail-closed tests for structured POU network decoding."""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gxworks2_mcp.gxw import pou_stream

LD_X0 = bytes.fromhex("030003049c0004")
OUT_Y0 = bytes.fromhex("032003049d0004")
END = bytes.fromhex("04340204")
RUNG = LD_X0 + OUT_Y0


def record(tokens, declared_size=None):
    size = len(tokens) if declared_size is None else declared_size
    return struct.pack("<7I", 1, size, size, 1, 12, 0, 0xFFFFFFFF) + tokens


def assert_incomplete(result):
    assert result["decoded"] is False
    assert result["coverage"] < 1.0
    assert "il" not in result
    assert result["stopReason"]
    assert result["failedNetworks"]


def test_complete_network_without_end_is_accepted():
    result = pou_stream(record(RUNG))
    assert result["decoded"] is True
    assert result["coverage"] == 1.0
    assert result["il"].splitlines() == ["LD X0", "OUT Y0", "END"]


def test_unknown_token_after_valid_prefix_is_not_success():
    result = pou_stream(record(RUNG + b"\xff\xaa"))
    assert_incomplete(result)
    assert result["networks"] == 0
    assert result["partialIl"].splitlines() == ["LD X0", "OUT Y0"]
    assert "unknown record" in result["stopReason"]
    assert result["failedNetworks"][0]["recordOffset"] == 0


def test_truncated_operand_is_not_a_complete_network():
    result = pou_stream(record(LD_X0 + bytes.fromhex("032003049d")))
    assert_incomplete(result)
    assert result["partialIl"] == "LD X0"
    assert "operand unreadable" in result["stopReason"]


def test_truncated_record_length_does_not_fall_back_to_embedded_program():
    body = RUNG + END
    result = pou_stream(record(body, len(body) + 100))
    assert_incomplete(result)
    assert "payload truncated" in result["stopReason"]


def test_mixed_complete_and_failed_network_reports_position_and_prefix():
    first = record(RUNG)
    result = pou_stream(first + record(LD_X0 + b"\xff") + record(RUNG))
    assert_incomplete(result)
    assert result["networks"] == 2
    assert result["coverage"] == 0.667
    assert result["failedNetworks"][0]["networkIndex"] == 1
    assert result["failedNetworks"][0]["recordOffset"] == len(first)
    # Do not splice the later network across the unrecognized region.
    assert result["partialIl"].splitlines() == ["LD X0", "OUT Y0", "LD X0"]


def test_unknown_record_without_valid_prefix_is_not_skipped():
    result = pou_stream(record(b"\xff\xff") + record(RUNG))
    assert_incomplete(result)
    assert result["partialIl"] == ""
    assert result["skippedNetworks"] == 0


def test_multiple_operands_are_preserved_for_device_inventory():
    mov = bytes.fromhex("054c05000504e8010404a80004")
    result = pou_stream(record(LD_X0 + mov))
    assert result["decoded"] is True
    assert result["il"].splitlines() == ["LD X0", "MOV K1 D0", "END"]
    assert "D0" in result["devices"]
    assert "K1 D0" not in result["devices"]


def test_metadata_trailer_and_output_comment_are_preserved_correctly():
    comment = b"\x07\x82\x03abc\x07"
    trailer = bytes.fromhex("0100000021000000") + "tag".encode("utf-16-le")
    result = pou_stream(record(RUNG + comment + trailer))
    assert result["decoded"] is True
    assert result["il"].splitlines() == ["LD X0", "OUT Y0  // abc", "END"]


def test_recognized_metadata_only_record_is_not_a_failure():
    metadata = bytes.fromhex("0100000021000000") + "tag".encode("utf-16-le")
    result = pou_stream(record(metadata) + record(RUNG))
    assert result["decoded"] is True
    assert result["networks"] == 1
    assert result["skippedNetworks"] == 1


def test_existing_continuous_stream_fallback_is_preserved():
    result = pou_stream(RUNG + END)
    assert result["decoded"] is True
    assert result["framing"] == "continuous-token-stream"
    assert result["il"].splitlines() == ["LD X0", "OUT Y0", "END"]


def test_continuous_stream_with_unknown_wrapper_is_not_success():
    for payload in (b"\xff" + RUNG + END, RUNG + END + b"\xff"):
        result = pou_stream(payload)
        assert result["decoded"] is False
        assert "il" not in result


def test_trailer_pattern_inside_incomplete_operand_is_not_accepted():
    trailer = bytes.fromhex("0100000021000000") + "tag".encode("utf-16-le")
    result = pou_stream(record(LD_X0 + bytes.fromhex("032003049d") + trailer))
    assert_incomplete(result)
    assert "operand unreadable" in result["stopReason"]


def test_failure_coverage_never_rounds_to_full_success():
    result = pou_stream(record(RUNG) * 2000 + record(b"\xff"))
    assert_incomplete(result)
