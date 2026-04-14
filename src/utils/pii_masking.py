"""PII masking utilities for structlog processor chain."""

import re
from typing import Any

# Keys (case-insensitive) whose values should be masked
_EMAIL_KEYS = frozenset({"email", "to_email", "from_email", "email_address", "recipient", "sender"})
_PHONE_KEYS = frozenset({"phone", "phone_number"})
_ALL_PII_KEYS = _EMAIL_KEYS | _PHONE_KEYS


def mask_email(email: str) -> str:
    """Mask an email address: first 2 chars + ***@ + domain.

    Examples:
        ti***@example.com  (from tilak@example.com)
        fo***@bar.com      (from foo@bar.com)
        a***@x.com         (from ab@x.com — local part <= 2 chars uses all chars)
    """
    if not isinstance(email, str) or "@" not in email:
        return email
    local, domain = email.split("@", 1)
    prefix = local[:2] if len(local) > 2 else local
    return f"{prefix}***@{domain}"


def mask_phone(phone: str) -> str:
    """Mask a phone number: first 3 digits + *** + last 2 digits.

    Examples:
        555***67  (from 5551234567)
        +15***67  (digits only: 15551234567 → 155***67, but we keep original format prefix)
    """
    if not isinstance(phone, str):
        return phone
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 5:
        return phone  # Too short to mask meaningfully
    return f"{digits[:3]}***{digits[-2:]}"


def _mask_value(key: str, value: Any) -> Any:
    """Return masked version of value based on the key name."""
    if value is None:
        return value
    key_lower = key.lower()
    if key_lower in _EMAIL_KEYS:
        return mask_email(str(value))
    if key_lower in _PHONE_KEYS:
        return mask_phone(str(value))
    return value


def _walk(obj: Any, *, in_pii_key: bool = False) -> Any:
    """Recursively walk dicts and lists, masking PII values."""
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            k_lower = k.lower() if isinstance(k, str) else k
            if k_lower in _ALL_PII_KEYS:
                result[k] = _mask_value(k, v) if not isinstance(v, (dict, list)) else _walk(v, in_pii_key=True)
            else:
                result[k] = _walk(v)
        return result
    if isinstance(obj, list):
        return [_walk(item, in_pii_key=in_pii_key) for item in obj]
    return obj


def mask_dict_pii(logger: Any, method: str, event_dict: dict) -> dict:
    """Structlog processor: walk event_dict and mask any PII fields in-place."""
    return _walk(event_dict)
