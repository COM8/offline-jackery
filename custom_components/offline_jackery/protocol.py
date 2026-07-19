"""Jackery encrypted BLE framing and paging codec."""

from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

JACKERY_SERVICE_UUID = "0000bdee-0000-1000-8000-00805f9b34fb"
WRITE_CHARACTERISTIC_UUID = "0000ee01-0000-1000-8000-00805f9b34fb"
NOTIFY_CHARACTERISTIC_UUID = "0000ee02-0000-1000-8000-00805f9b34fb"
AES_BLOCK_BYTES = 16
ENCRYPTED_PACKET_OVERHEAD = 32
MIN_PLAINTEXT_BYTES = 20
RESPONSE_HEADER_BYTES = 16


class ProtocolError(RuntimeError):
    """A key or packet is invalid, corrupt, or incompatible."""


def crc16_modbus(data: bytes) -> bytes:
    """Return CRC16/MODBUS in Jackery's low-byte-first wire order."""
    crc = 0xFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc.to_bytes(2, "little")


def decode_bluetooth_key(encoded_key: str) -> bytes:
    """Decode and validate a Base64 AES key returned by Jackery."""
    try:
        key = base64.b64decode(encoded_key.strip(), validate=True)
    except (binascii.Error, ValueError) as err:
        raise ProtocolError("Bluetooth key is not valid Base64") from err
    if len(key) not in {16, 24, 32}:
        raise ProtocolError("Bluetooth key must decode to 16, 24, or 32 bytes")
    return key


def encrypt_packet(packet: bytes, key: bytes, *, iv: bytes | None = None) -> bytes:
    """Encrypt one authenticated logical page with AES-CBC."""
    iv = os.urandom(AES_BLOCK_BYTES) if iv is None else iv
    if len(iv) != AES_BLOCK_BYTES:
        raise ValueError("AES IV must contain 16 bytes")
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(packet) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return iv + encryptor.update(padded) + encryptor.finalize()


def decrypt_packet(notification: bytes, key: bytes) -> bytes:
    """Decrypt a notification and validate padding, magic, and CRC."""
    if len(notification) < ENCRYPTED_PACKET_OVERHEAD or (
        len(notification) - AES_BLOCK_BYTES
    ) % AES_BLOCK_BYTES:
        raise ProtocolError("Invalid encrypted notification length")
    iv, ciphertext = notification[:AES_BLOCK_BYTES], notification[AES_BLOCK_BYTES:]
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    try:
        unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
        plaintext = unpadder.update(padded) + unpadder.finalize()
    except ValueError as err:
        raise ProtocolError(
            "Invalid AES padding; the Bluetooth key may be wrong"
        ) from err
    if len(plaintext) < MIN_PLAINTEXT_BYTES or plaintext[:2] != b"\xdf\xed":
        raise ProtocolError("Notification does not contain Jackery framing")
    if crc16_modbus(plaintext[:-2]) != plaintext[-2:]:
        raise ProtocolError("Notification CRC check failed")
    return plaintext[:-4]


@dataclass(frozen=True, slots=True)
class ResponsePage:
    """One decoded page from a BLE response."""

    page_number: int
    page_count: int
    action_id: int
    message_type: int
    code: int
    body: bytes


def parse_response_page(frame: bytes) -> ResponsePage:
    """Parse a decrypted response frame without random and CRC fields."""
    if len(frame) < RESPONSE_HEADER_BYTES or frame[:2] != b"\xdf\xed":
        raise ProtocolError("Response frame is too short or has invalid magic")
    page_number = int.from_bytes(frame[4:6], "big")
    page_count = int.from_bytes(frame[6:8], "big")
    length = int.from_bytes(frame[14:16], "big")
    if page_number < 1 or page_count < 1 or page_number > page_count:
        raise ProtocolError("Response page numbers are invalid")
    if len(frame) != RESPONSE_HEADER_BYTES + length:
        raise ProtocolError("Response body length does not match frame")
    return ResponsePage(
        page_number=page_number,
        page_count=page_count,
        action_id=int.from_bytes(frame[8:10], "big"),
        message_type=int.from_bytes(frame[10:12], "big"),
        code=int.from_bytes(frame[12:13], "big", signed=True),
        body=frame[RESPONSE_HEADER_BYTES:],
    )


def build_command_pages(
    *,
    action_id: int,
    message_type: int,
    body: dict[str, Any],
    key: bytes,
    max_body_bytes: int = 100,
) -> list[bytes]:
    """Serialize, page, authenticate, and encrypt one command."""
    payload = dict(body)
    if message_type > 0:
        payload.setdefault("cmd", message_type)
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    chunks = [
        encoded[index : index + max_body_bytes]
        for index in range(0, len(encoded), max_body_bytes)
    ] or [b""]
    pages: list[bytes] = []
    for page_number, chunk in enumerate(chunks, 1):
        frame = b"".join(
            (
                b"\xdf\xed\x00\x01",
                page_number.to_bytes(2, "big"),
                len(chunks).to_bytes(2, "big"),
                action_id.to_bytes(2, "big"),
                message_type.to_bytes(2, "big"),
                b"\x00\x01",
                len(chunk).to_bytes(2, "big"),
                chunk,
            )
        )
        authenticated = frame + (secrets.randbelow(0xFFFF) + 1).to_bytes(2, "big")
        pages.append(encrypt_packet(authenticated + crc16_modbus(authenticated), key))
    return pages


class PageAssembler:
    """Reassemble response pages and decode their JSON object."""

    def __init__(self) -> None:
        self._pages: dict[tuple[int, int], dict[int, bytes]] = {}

    def add(self, page: ResponsePage) -> dict[str, Any] | None:
        key = (page.action_id, page.message_type)
        pages = self._pages.setdefault(key, {})
        pages[page.page_number] = page.body
        if len(pages) < page.page_count or not all(
            index in pages for index in range(1, page.page_count + 1)
        ):
            return None
        raw = b"".join(pages[index] for index in range(1, page.page_count + 1))
        self._pages.pop(key, None)
        try:
            value = json.loads(raw.decode()) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise ProtocolError("Response body is not valid JSON") from err
        if not isinstance(value, dict):
            raise ProtocolError("Response JSON body is not an object")
        return value
