"""Webhook signature verification utilities (NIF-219, NIF-257).

Provides ECDSA-based verification for SendGrid Event Webhook v3 signatures,
HMAC-SHA256 verification for HubSpot webhook signatures,
and a provider-dispatching helper for multi-provider webhook handling.
"""

import base64
import hashlib
import hmac
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ec import ECDSA
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import load_pem_public_key


def verify_sendgrid_signature(
    payload: bytes,
    signature: str,
    timestamp: str,
    signing_key: str,
) -> bool:
    """Verify a SendGrid Event Webhook v3 ECDSA signature.

    SendGrid signs the concatenation of ``timestamp + payload`` using an
    ECDSA P-256 key.  The public key is the ``sendgrid_webhook_signing_key``
    from the SendGrid app settings (PEM-encoded).

    Args:
        payload: Raw request body bytes.
        signature: Base64-encoded DER signature from the
            ``X-Twilio-Email-Event-Webhook-Signature`` header.
        timestamp: Timestamp string from the
            ``X-Twilio-Email-Event-Webhook-Timestamp`` header.
        signing_key: PEM-encoded EC public key from SendGrid dashboard.

    Returns:
        True if the signature is valid, False otherwise.
    """
    if not signing_key or not signature or not timestamp:
        return False

    try:
        public_key = load_pem_public_key(signing_key.encode())
        signed_data = (timestamp + payload.decode("utf-8", errors="replace")).encode()
        sig_bytes = base64.b64decode(signature)
        public_key.verify(sig_bytes, signed_data, ECDSA(SHA256()))
        return True
    except (InvalidSignature, Exception):
        return False


def verify_hubspot_signature(
    request_body: bytes,
    signature: str,
    client_secret: str,
    source_string: str,
    timestamp: str,
    max_age_seconds: int = 300,
) -> bool:
    """Verify a HubSpot webhook v3 HMAC-SHA256 signature.

    HubSpot signs the concatenation of ``{HTTP_METHOD}{url}{request_body}``
    (the ``source_string``) using the app's client secret.  The resulting
    HMAC-SHA256 digest is Base64-encoded and sent in the
    ``X-HubSpot-Signature-v3`` header.

    Args:
        request_body: Raw request body bytes (unused in computation but kept
            for interface symmetry — already incorporated into source_string).
        signature: Base64-encoded HMAC-SHA256 from ``X-HubSpot-Signature-v3``.
        client_secret: HubSpot app client secret.
        source_string: Pre-built string ``{HTTP_METHOD}{url}{request_body}``.
        timestamp: Unix-millisecond timestamp from
            ``X-HubSpot-Request-Timestamp`` header.
        max_age_seconds: Maximum allowed age of the request (default 300 s).

    Returns:
        True if the signature is valid and the timestamp is fresh.
    """
    if not client_secret or not signature or not timestamp:
        return False

    # Reject stale requests to prevent replay attacks
    try:
        ts_ms = int(timestamp)
        age_seconds = abs(time.time() - ts_ms / 1000)
        if age_seconds > max_age_seconds:
            return False
    except (ValueError, TypeError):
        return False

    try:
        expected = hmac.new(
            client_secret.encode("utf-8"),
            source_string.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        expected_b64 = base64.b64encode(expected).decode("utf-8")
        return hmac.compare_digest(expected_b64, signature)
    except Exception:
        return False


def verify_webhook_request(request_body: bytes, headers: dict, provider: str, signing_key: str) -> bool:
    """Dispatch webhook verification to the appropriate provider.

    Args:
        request_body: Raw request body bytes.
        headers: Dict of HTTP headers (case-insensitive lookup attempted).
        provider: Provider name, e.g. ``"sendgrid"``.
        signing_key: Provider-specific signing/verification key.

    Returns:
        True if verification succeeds, False otherwise.
    """
    # Normalise header keys to lower-case for consistent lookup
    lowered = {k.lower(): v for k, v in headers.items()}

    if provider == "sendgrid":
        signature = lowered.get("x-twilio-email-event-webhook-signature", "")
        timestamp = lowered.get("x-twilio-email-event-webhook-timestamp", "")
        return verify_sendgrid_signature(request_body, signature, timestamp, signing_key)

    if provider == "hubspot":
        signature = lowered.get("x-hubspot-signature-v3", "")
        timestamp = lowered.get("x-hubspot-request-timestamp", "")
        # source_string must be pre-built by the caller (requires HTTP method + URL)
        source_string = headers.get("_hubspot_source_string", "")
        return verify_hubspot_signature(request_body, signature, signing_key, source_string, timestamp)

    return False
