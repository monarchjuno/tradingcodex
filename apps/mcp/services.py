from __future__ import annotations

import re
from typing import Any

from tradingcodex_service.log_safety import redact_log_text


REDACTED = "<redacted>"
SAFE_REFERENCE_RE = re.compile(r"^env:[A-Za-z_][A-Za-z0-9_]*$")


def redact_sensitive_data(value: Any, *, secret_values: tuple[str, ...] = ()) -> Any:
    """Redact credentials from MCP requests, responses, audit data, and errors."""

    secrets = tuple(secret for secret in secret_values if secret)
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if key.lower() == "env":
                redacted[key] = _redact_environment(item)
            elif key.lower() == "credential_ref" and _is_safe_reference(item):
                redacted[key] = str(item)
            elif _is_sensitive_field(key):
                redacted[key] = REDACTED
            else:
                redacted[key] = redact_sensitive_data(item, secret_values=secrets)
        return redacted
    if isinstance(value, list):
        redacted_items: list[Any] = []
        redact_next = False
        for item in value:
            if redact_next:
                redacted_items.append(REDACTED)
                redact_next = False
                continue
            redacted_items.append(redact_sensitive_data(item, secret_values=secrets))
            redact_next = isinstance(item, str) and bool(
                re.fullmatch(
                    r"--?(?:api[_-]?key|token|secret|password|credential|authorization)",
                    item,
                    flags=re.I,
                )
            )
        return redacted_items
    if isinstance(value, tuple):
        return [redact_sensitive_data(item, secret_values=secrets) for item in value]
    if isinstance(value, str):
        return _redact_sensitive_text(value, secrets)
    return value


def _redact_environment(value: Any) -> dict[str, str] | str:
    if not isinstance(value, dict):
        return REDACTED
    result: dict[str, str] = {}
    for key, item in value.items():
        text = str(item or "")
        result[str(key)] = text if SAFE_REFERENCE_RE.fullmatch(text) else REDACTED
    return result


def _is_safe_reference(value: Any) -> bool:
    text = str(value or "")
    return bool(
        SAFE_REFERENCE_RE.fullmatch(text)
        or re.fullmatch(r"(?:os-keychain|keyring|secret)://[A-Za-z0-9._~:/@+-]+", text)
    )


def _is_sensitive_field(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return normalized.endswith(
        (
            "secret",
            "secrets",
            "password",
            "passphrase",
            "credential",
            "credentials",
            "apikey",
            "accesstoken",
            "refreshtoken",
            "token",
            "authorization",
            "cookie",
        )
    )


def _redact_sensitive_text(value: str, secret_values: tuple[str, ...] = ()) -> str:
    text = str(value or "")
    for secret in sorted((item for item in secret_values if item), key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    text = re.sub(r"(?i)(bearer\s+)[^\s,;]+", rf"\1{REDACTED}", text)
    text = re.sub(
        r"(?i)((?:api[_-]?key|token|secret|password|credential|authorization)\s*[:=]\s*)([^\s,;&]+)",
        rf"\1{REDACTED}",
        text,
    )
    text = re.sub(r"(?i)([a-z][a-z0-9+.-]*://)[^/@\s]+@", rf"\1{REDACTED}@", text)
    return redact_log_text(text)
