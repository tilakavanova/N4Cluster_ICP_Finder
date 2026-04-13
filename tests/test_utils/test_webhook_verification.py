"""Tests for NIF-219: Webhook signature verification.

Covers:
- verify_sendgrid_signature: valid ECDSA signature passes
- verify_sendgrid_signature: invalid/tampered signature rejected
- verify_sendgrid_signature: missing signing_key returns False
- verify_sendgrid_signature: missing timestamp returns False
- verify_sendgrid_signature: missing signature returns False
- verify_webhook_request: dispatches to sendgrid provider
- verify_webhook_request: unknown provider returns False
- verify_webhook_request: case-insensitive header lookup
"""

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ec import (
    ECDSA,
    generate_private_key,
    SECP256R1,
)
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

from src.utils.webhook_verification import verify_sendgrid_signature, verify_webhook_request


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_key_pair():
    """Return (private_key, pem_public_key_str)."""
    private_key = generate_private_key(SECP256R1())
    public_pem = private_key.public_key().public_bytes(
        encoding=Encoding.PEM,
        format=PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_key, public_pem


def _sign(private_key, timestamp: str, payload: bytes) -> str:
    """Return base64-encoded DER signature over timestamp+payload."""
    signed_data = (timestamp + payload.decode("utf-8", errors="replace")).encode()
    sig = private_key.sign(signed_data, ECDSA(SHA256()))
    return base64.b64encode(sig).decode()


# ---------------------------------------------------------------------------
# verify_sendgrid_signature
# ---------------------------------------------------------------------------

class TestVerifySendgridSignature:
    def setup_method(self):
        self.private_key, self.public_pem = _generate_key_pair()

    def test_valid_signature_returns_true(self):
        payload = b'[{"event":"open"}]'
        ts = "1681000000"
        sig = _sign(self.private_key, ts, payload)
        assert verify_sendgrid_signature(payload, sig, ts, self.public_pem) is True

    def test_tampered_payload_returns_false(self):
        payload = b'[{"event":"open"}]'
        ts = "1681000000"
        sig = _sign(self.private_key, ts, payload)
        tampered = b'[{"event":"click"}]'
        assert verify_sendgrid_signature(tampered, sig, ts, self.public_pem) is False

    def test_wrong_timestamp_returns_false(self):
        payload = b'[{"event":"open"}]'
        ts = "1681000000"
        sig = _sign(self.private_key, ts, payload)
        assert verify_sendgrid_signature(payload, sig, "9999999999", self.public_pem) is False

    def test_invalid_base64_signature_returns_false(self):
        payload = b'test'
        assert verify_sendgrid_signature(payload, "NOT_VALID_BASE64!!!", "12345", self.public_pem) is False

    def test_empty_signing_key_returns_false(self):
        payload = b'test'
        assert verify_sendgrid_signature(payload, "somesig", "12345", "") is False

    def test_empty_signature_returns_false(self):
        payload = b'test'
        assert verify_sendgrid_signature(payload, "", "12345", self.public_pem) is False

    def test_empty_timestamp_returns_false(self):
        payload = b'test'
        sig = _sign(self.private_key, "12345", payload)
        assert verify_sendgrid_signature(payload, sig, "", self.public_pem) is False

    def test_wrong_key_returns_false(self):
        payload = b'[{"event":"open"}]'
        ts = "1681000000"
        sig = _sign(self.private_key, ts, payload)
        # Generate a different key
        _, other_public_pem = _generate_key_pair()
        assert verify_sendgrid_signature(payload, sig, ts, other_public_pem) is False

    def test_garbage_signing_key_returns_false(self):
        payload = b'test'
        ts = "12345"
        sig = _sign(self.private_key, ts, payload)
        assert verify_sendgrid_signature(payload, sig, ts, "-----GARBAGE KEY-----") is False


# ---------------------------------------------------------------------------
# verify_webhook_request
# ---------------------------------------------------------------------------

class TestVerifyWebhookRequest:
    def setup_method(self):
        self.private_key, self.public_pem = _generate_key_pair()

    def _make_headers(self, ts: str, sig: str) -> dict:
        return {
            "X-Twilio-Email-Event-Webhook-Signature": sig,
            "X-Twilio-Email-Event-Webhook-Timestamp": ts,
        }

    def test_valid_sendgrid_request_returns_true(self):
        payload = b'[{"event":"delivered"}]'
        ts = "1681000001"
        sig = _sign(self.private_key, ts, payload)
        headers = self._make_headers(ts, sig)
        assert verify_webhook_request(payload, headers, "sendgrid", self.public_pem) is True

    def test_invalid_sendgrid_request_returns_false(self):
        payload = b'[{"event":"delivered"}]'
        ts = "1681000001"
        sig = _sign(self.private_key, ts, payload)
        headers = self._make_headers(ts, sig)
        assert verify_webhook_request(b"tampered", headers, "sendgrid", self.public_pem) is False

    def test_unknown_provider_returns_false(self):
        payload = b'test'
        headers = {}
        assert verify_webhook_request(payload, headers, "mailgun", "key") is False

    def test_case_insensitive_header_lookup(self):
        payload = b'[{"event":"open"}]'
        ts = "1681000002"
        sig = _sign(self.private_key, ts, payload)
        # Lowercase header keys
        headers = {
            "x-twilio-email-event-webhook-signature": sig,
            "x-twilio-email-event-webhook-timestamp": ts,
        }
        assert verify_webhook_request(payload, headers, "sendgrid", self.public_pem) is True

    def test_missing_headers_returns_false(self):
        payload = b'test'
        assert verify_webhook_request(payload, {}, "sendgrid", self.public_pem) is False
