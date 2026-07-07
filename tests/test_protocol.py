"""Tests for encrypted Jackery BLE framing."""

import json

import pytest

from custom_components.offline_jackery.protocol import (
    PageAssembler,
    ProtocolError,
    ResponsePage,
    build_command_pages,
    crc16_modbus,
    decode_bluetooth_key,
    decrypt_packet,
    parse_response_page,
)

KEY = bytes(range(16))


def test_crc_known_vector() -> None:
    assert crc16_modbus(b"123456789") == b"7K"


def test_key_validation() -> None:
    assert decode_bluetooth_key("AAECAwQFBgcICQoLDA0ODw==") == KEY
    with pytest.raises(ProtocolError):
        decode_bluetooth_key("not base64")


def test_command_frame_contains_cmd_and_valid_crc() -> None:
    encrypted = build_command_pages(
        action_id=3011, message_type=106, body={}, key=KEY
    )[0]
    frame = decrypt_packet(encrypted, KEY)
    assert frame[:2] == b"\xdf\xed"
    assert int.from_bytes(frame[8:10], "big") == 3011
    assert json.loads(frame[16:]) == {"cmd": 106}


def test_response_code_uses_status_byte_not_marker() -> None:
    body = b"{}"
    frame = b"".join(
        (
            b"\xdf\xed",
            (100).to_bytes(2, "big"),
            (1).to_bytes(2, "big"),
            (1).to_bytes(2, "big"),
            (3019).to_bytes(2, "big"),
            (120).to_bytes(2, "big"),
            b"\x00\x01",
            len(body).to_bytes(2, "big"),
            body,
        )
    )
    parsed = parse_response_page(frame)
    assert parsed.code == 0
    assert PageAssembler().add(parsed) == {}


def test_page_assembler_out_of_order() -> None:
    assembler = PageAssembler()
    second = ResponsePage(2, 2, 3019, 120, 0, b"true}")
    first = ResponsePage(1, 2, 3019, 120, 0, b'{"ok":')
    assert assembler.add(second) is None
    assert assembler.add(first) == {"ok": True}
