"""Async client for the small Jackery cloud surface needed during setup."""

from __future__ import annotations

import base64
import json
import os
import platform
import uuid
from dataclasses import dataclass
from typing import Any

import aiohttp
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import load_der_public_key

API_BASE = "https://iot.jackeryapp.com/v1/"
LOGIN_PUBLIC_KEY_B64 = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCVmzgJy/4XolxPnkfu32YtJqYG"
    "FLYqf9/rnVgURJED+8J9J3Pccd6+9L97/+7COZE5OkejsgOkqeLNC9C3r5mhpE4"
    "zk/HStss7Q8/5DqkGD1annQ+eoICo3oi0dITZ0Qll56Dowb8lXi6WHViVDdih/oe"
    "UwVJY89uJNtTWrz7t7QIDAQAB"
)


class JackeryApiError(RuntimeError):
    """Base exception for safe-to-display Jackery API failures."""


class JackeryAuthenticationError(JackeryApiError):
    """The account credentials were rejected."""


class JackeryConnectionError(JackeryApiError):
    """The Jackery service could not be reached."""


@dataclass(frozen=True, slots=True)
class LoginDetails:
    """Credentials used only to create one encrypted login request."""

    account: str | None
    phone: str | None
    password: str
    region_code: str | None
    installation_id: str


@dataclass(frozen=True, slots=True)
class JackerySystem:
    """Normalized system returned by the Jackery account API."""

    name: str
    serial_number: str
    model_code: int | None
    device_id: str | None
    bluetooth_key: str | None


def build_login_form(
    details: LoginDetails, *, random_bytes: bytes | None = None
) -> dict[str, str]:
    """Build the Android app's RSA-wrapped AES password-login form."""

    seed = os.urandom(16) if random_bytes is None else random_bytes
    if len(seed) != 16:
        raise ValueError("random_bytes must contain exactly 16 bytes")
    aes_key = base64.b64encode(seed)  # The app uses these 24 ASCII bytes directly.
    payload: dict[str, object] = {
        "loginType": 2,
        "password": details.password,
        "registerAppId": "com.hbxn.jackery",
        "macId": details.installation_id,
    }
    if details.account is not None:
        payload["account"] = details.account
        payload["regionCode"] = details.region_code or ""
    elif details.phone is not None:
        payload["phone"] = details.phone
    else:
        raise ValueError("Either account or phone must be supplied")

    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(raw) + padder.finalize()
    encryptor = Cipher(algorithms.AES(aes_key), modes.ECB()).encryptor()
    encrypted_payload = encryptor.update(padded) + encryptor.finalize()
    public_key = load_der_public_key(base64.b64decode(LOGIN_PUBLIC_KEY_B64))
    encrypted_key = public_key.encrypt(aes_key, rsa_padding.PKCS1v15())
    return {
        "aesEncryptData": base64.b64encode(encrypted_payload).decode(),
        "rsaForAesKey": base64.b64encode(encrypted_key).decode(),
    }


def _device_id(system: dict[str, Any]) -> str | None:
    direct = system.get("deviceId")
    if isinstance(direct, str) and direct:
        return direct
    devices = system.get("devices")
    if not isinstance(devices, list):
        return None
    serial = system.get("deviceSn")
    candidates = [item for item in devices if isinstance(item, dict)]
    candidates.sort(
        key=lambda item: (item.get("deviceSn") != serial, item.get("modelCode") != 3001)
    )
    for item in candidates:
        value = item.get("deviceId")
        if isinstance(value, str) and value:
            return value
    return None


def normalize_systems(data: object) -> list[JackerySystem]:
    """Normalize the account API's known system-list layouts."""

    if not isinstance(data, list):
        raise JackeryApiError("Jackery returned an invalid system list")
    result: list[JackerySystem] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        devices = raw.get("devices")
        model_codes = [
            item.get("modelCode")
            for item in devices
            if isinstance(devices, list)
            and isinstance(item, dict)
            and isinstance(item.get("modelCode"), int)
        ] if isinstance(devices, list) else []
        serial = raw.get("deviceSn") or raw.get("systemSn")
        if not isinstance(serial, str) or not serial:
            continue
        key = raw.get("bluetoothKey")
        result.append(
            JackerySystem(
                name=str(raw.get("systemName") or "Jackery device"),
                serial_number=serial,
                model_code=(
                    3001
                    if 3001 in model_codes
                    else (model_codes[0] if model_codes else None)
                ),
                device_id=_device_id(raw),
                bluetooth_key=key if isinstance(key, str) and key else None,
            )
        )
    return result


class JackeryCloudClient:
    """Short-lived account client used by the configuration flow."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._token = ""

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Accept-Language": "en-US",
            "token": self._token,
            "platform": "2",
            "app_version": "2.1.1",
            "app_version_code": "93",
            "sys_version": f"Android compatibility client/{platform.system()}",
            "model": "Home Assistant/Offline Jackery",
            "network": "wifi",
        }

    async def _request(
        self, method: str, path: str, fields: dict[str, str] | None = None
    ) -> Any:
        try:
            async with self._session.request(
                method,
                API_BASE + path,
                headers=self._headers(),
                params=fields if method == "GET" else None,
                data=fields if method == "POST" else None,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                if response.status in {401, 403}:
                    raise JackeryAuthenticationError("Invalid Jackery credentials")
                response.raise_for_status()
                envelope = await response.json(content_type=None)
        except JackeryApiError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise JackeryConnectionError("Could not reach the Jackery service") from err
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            raise JackeryApiError("Jackery returned an invalid response") from err
        if not isinstance(envelope, dict):
            raise JackeryApiError("Jackery returned an invalid response")
        try:
            code = int(envelope.get("code", -1))
        except (TypeError, ValueError):
            code = -1
        if code != 0:
            message = envelope.get("msg")
            if code in {10402, 10403}:
                raise JackeryAuthenticationError("Invalid Jackery credentials")
            safe = message if isinstance(message, str) and len(message) <= 200 else "request failed"
            raise JackeryApiError(f"Jackery API error {code}: {safe}")
        token = envelope.get("token")
        if isinstance(token, str) and token:
            self._token = token
        return envelope.get("data")

    async def async_login(
        self,
        *,
        account: str | None,
        phone: str | None,
        password: str,
        region_code: str | None,
    ) -> list[JackerySystem]:
        """Log in, discard the password form, and return account systems."""

        details = LoginDetails(
            account=account,
            phone=phone,
            password=password,
            region_code=region_code,
            installation_id=uuid.uuid4().hex,
        )
        await self._request("POST", "auth/login", build_login_form(details))
        if not self._token:
            raise JackeryAuthenticationError("Login did not return a session token")
        return normalize_systems(await self._request("GET", "device/system/list"))

    async def async_bluetooth_key(self, system: JackerySystem) -> str:
        """Fetch the selected system key, falling back to the system-list value."""

        if system.device_id:
            try:
                data = await self._request(
                    "GET",
                    "device/bluetoothKey",
                    {"deviceSn": system.serial_number, "guid": system.device_id},
                )
                if isinstance(data, dict) and isinstance(data.get("bluetoothKey"), str):
                    return data["bluetoothKey"]
            except JackeryApiError:
                if not system.bluetooth_key:
                    raise
        if system.bluetooth_key:
            return system.bluetooth_key
        raise JackeryApiError("The selected system did not provide a Bluetooth key")
