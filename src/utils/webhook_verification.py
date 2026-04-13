"""Webhook signature verification utilities (NIF-219).

Provides ECDSA-based verification for SendGrid Event Webhook v3 signatures,
and a provider-dispatching helper for multi-provider webhook handling.
"""

import base64
import hashlib
import hmac

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

    return False
