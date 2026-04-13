"""SendGrid email delivery client (NIF-219).

Wraps the ``sendgrid`` Python SDK to provide a consistent interface for
sending transactional emails with open/click tracking, automatic pixel
injection, URL wrapping, and List-Unsubscribe header support.
"""

import re
from typing import Optional

import redis as redis_lib
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Category,
    ClickTracking,
    DynamicTemplateData,
    From,
    Header,
    Mail,
    OpenTracking,
    Personalization,
    Subject,
    TemplateId,
    To,
    TrackingSettings,
)

from src.utils.url_wrapper import generate_pixel_url, wrap_url

_LINK_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)


class SendGridClient:
    """Thin wrapper around the SendGrid Python SDK.

    All emails are sent with:
    - SendGrid open tracking enabled (plus our own custom tracking pixel)
    - SendGrid click tracking enabled (plus our own URL-wrapping)
    - ``List-Unsubscribe`` header pointing to our unsubscribe endpoint
    """

    def __init__(
        self,
        api_key: str,
        from_email: str,
        from_name: str,
        *,
        tracking_base_url: str = "https://n4cluster.com",
        categories: list[str] | None = None,
        redis_client: redis_lib.Redis | None = None,
    ) -> None:
        self._client = SendGridAPIClient(api_key=api_key)
        self.from_email = from_email
        self.from_name = from_name
        self.tracking_base_url = tracking_base_url
        self.categories = categories or ["outreach"]
        self._redis = redis_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
        tracking_data: Optional[dict] = None,
    ) -> tuple[bool, Optional[str], Optional[str]]:
        """Send a plain-content email.

        Args:
            to_email: Recipient email address.
            subject: Email subject line.
            html_content: HTML body.
            text_content: Optional plain-text alternative.
            tracking_data: Dict with keys ``lead_id``, ``campaign_id``,
                ``target_id`` used for pixel / URL-wrapping tokens.

        Returns:
            ``(success, message_id, error)`` tuple.
            On success: ``(True, "<message-id>", None)``.
            On failure: ``(False, None, "<error message>")``.
        """
        tracking_data = tracking_data or {}
        html_content = self._process_html(html_content, tracking_data)

        message = Mail(
            from_email=From(self.from_email, self.from_name),
            to_emails=To(to_email),
            subject=Subject(subject),
            html_content=html_content,
        )
        if text_content:
            message.plain_text_content = text_content

        self._apply_tracking_settings(message)
        self._apply_categories(message)
        self._apply_unsubscribe_header(message, to_email)

        return self._send(message)

    def send_email_with_template(
        self,
        to_email: str,
        template_id: str,
        dynamic_data: dict,
        tracking_data: Optional[dict] = None,
    ) -> tuple[bool, Optional[str], Optional[str]]:
        """Send an email using a SendGrid dynamic template.

        Args:
            to_email: Recipient email address.
            template_id: SendGrid dynamic template ID (``d-...``).
            dynamic_data: Template substitution variables.
            tracking_data: Dict with keys ``lead_id``, ``campaign_id``,
                ``target_id``.

        Returns:
            ``(success, message_id, error)`` — same contract as
            :meth:`send_email`.
        """
        message = Mail()
        message.from_email = From(self.from_email, self.from_name)
        message.template_id = TemplateId(template_id)

        personalization = Personalization()
        personalization.add_to(To(to_email))
        personalization.dynamic_template_data = dynamic_data
        message.add_personalization(personalization)

        self._apply_tracking_settings(message)
        self._apply_categories(message)
        self._apply_unsubscribe_header(message, to_email)

        return self._send(message)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _process_html(self, html: str, tracking_data: dict) -> str:
        """Wrap links and inject pixel into HTML body."""
        html = self._wrap_links(html, tracking_data)
        html = self._inject_pixel(html, tracking_data)
        return html

    def _wrap_links(self, html: str, tracking_data: dict) -> str:
        """Replace every ``href`` value with a click-tracking URL."""
        lead_id = tracking_data.get("lead_id", "")
        campaign_id = tracking_data.get("campaign_id", "")
        target_id = tracking_data.get("target_id", "")

        if not (lead_id and campaign_id):
            return html

        def _replace(match: re.Match) -> str:
            original_url = match.group(1)
            # Don't wrap mailto / tel / anchor links
            if original_url.startswith(("mailto:", "tel:", "#")):
                return match.group(0)
            wrapped = wrap_url(
                original_url=original_url,
                lead_id=lead_id,
                campaign_id=campaign_id,
                target_id=target_id,
                channel="email",
                base_url=self.tracking_base_url,
                redis_client=self._redis,
            )
            # Preserve original quote style
            quote = match.group(0)[5]  # char after 'href='
            return f'href={quote}{wrapped}{quote}'

        return _LINK_RE.sub(_replace, html)

    def _inject_pixel(self, html: str, tracking_data: dict) -> str:
        """Insert a 1×1 tracking pixel before the closing ``</body>`` tag."""
        lead_id = tracking_data.get("lead_id", "")
        campaign_id = tracking_data.get("campaign_id", "")
        target_id = tracking_data.get("target_id", "")

        if not (lead_id and campaign_id):
            return html

        pixel_url = generate_pixel_url(
            lead_id=lead_id,
            campaign_id=campaign_id,
            target_id=target_id,
            base_url=self.tracking_base_url,
            redis_client=self._redis,
        )
        pixel_tag = f'<img src="{pixel_url}" width="1" height="1" alt="" style="display:none" />'
        closing = re.search(r"</body>", html, re.IGNORECASE)
        if closing:
            return html[: closing.start()] + pixel_tag + html[closing.start():]
        return html + pixel_tag

    def _apply_tracking_settings(self, message: Mail) -> None:
        tracking = TrackingSettings()
        tracking.click_tracking = ClickTracking(enable=True, enable_text=False)
        tracking.open_tracking = OpenTracking(enable=True)
        message.tracking_settings = tracking

    def _apply_categories(self, message: Mail) -> None:
        for cat in self.categories:
            message.add_category(Category(cat))

    def _apply_unsubscribe_header(self, message: Mail, to_email: str) -> None:
        unsubscribe_url = f"{self.tracking_base_url}/unsubscribe?email={to_email}"
        message.add_header(Header("List-Unsubscribe", f"<{unsubscribe_url}>"))
        message.add_header(Header("List-Unsubscribe-Post", "List-Unsubscribe=One-Click"))

    def _send(self, message: Mail) -> tuple[bool, Optional[str], Optional[str]]:
        try:
            response = self._client.send(message)
            # SendGrid returns the message-id in X-Message-Id header
            message_id = None
            if hasattr(response, "headers") and response.headers:
                message_id = response.headers.get("X-Message-Id")
            return True, message_id, None
        except Exception as exc:
            return False, None, str(exc)
