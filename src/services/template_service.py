"""Email template rendering service (NIF-224).

Renders Jinja2 HTML email templates with personalisation context and
auto-generates a plain-text version by stripping HTML tags.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.utils.logging import get_logger

logger = get_logger("template_service")

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "email"

_env: Environment | None = None


def _get_env() -> Environment:
    global _env
    if _env is None:
        _env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
    return _env


# ── Default subjects per template ────────────────────────────────────────────

_DEFAULT_SUBJECTS: dict[str, str] = {
    "initial_outreach": "A partnership opportunity for {{ restaurant_name }}",
    "follow_up": "Following up — {{ restaurant_name }}",
    "re_engagement": "Still thinking of you — {{ restaurant_name }}",
}


# ── HTML → plain text conversion ─────────────────────────────────────────────

_BLOCK_TAGS = re.compile(
    r"<(br|p|div|h[1-6]|li|tr|td|blockquote)[^>]*>",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_NEWLINE = re.compile(r"\n{3,}")
_NBSP = re.compile(r"&nbsp;", re.IGNORECASE)


def _html_to_text(html_content: str) -> str:
    """Convert HTML to plain text by stripping tags.

    Block-level tags are replaced with newlines; inline tags are dropped.
    HTML entities are decoded.
    """
    # Replace block tags with newlines
    text = _BLOCK_TAGS.sub("\n", html_content)
    # Strip all remaining tags
    text = _TAG_RE.sub("", text)
    # Decode &nbsp; and other entities
    text = _NBSP.sub(" ", text)
    text = html.unescape(text)
    # Normalise whitespace
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = _MULTI_NEWLINE.sub("\n\n", text)
    return text.strip()


# ── Public API ────────────────────────────────────────────────────────────────


def render_email_template(
    template_name: str,
    context: dict,
) -> tuple[str, str, str]:
    """Render an email template and return ``(subject, html, text)``.

    Args:
        template_name: Base name without extension, e.g. ``"initial_outreach"``.
        context: Personalisation variables passed to the Jinja2 template.
            Common keys: ``restaurant_name``, ``owner_name``, ``cuisine_type``,
            ``sender_name``, ``sender_title``, ``cta_url``, ``unsubscribe_url``.

    Returns:
        A ``(subject, html_body, text_body)`` tuple. The plain-text body is
        auto-generated from the rendered HTML.

    Raises:
        ValueError: If the template file does not exist.
        jinja2.TemplateNotFound: Propagated from Jinja2 if the file is missing.
    """
    template_file = f"{template_name}.html"
    env = _get_env()

    # Render HTML body
    template = env.get_template(template_file)
    html_body = template.render(**context)

    # Build subject: prefer explicit context["subject"], then default template
    subject_template_str = context.get("subject") or _DEFAULT_SUBJECTS.get(
        template_name, "N4Cluster — reaching out"
    )
    subject = Environment(autoescape=False).from_string(subject_template_str).render(**context)

    # Auto-generate plain-text version
    text_body = _html_to_text(html_body)

    logger.debug(
        "email_template_rendered",
        template=template_name,
        subject=subject,
        html_len=len(html_body),
        text_len=len(text_body),
    )

    return subject, html_body, text_body


def list_templates() -> list[str]:
    """Return available template names (without .html extension)."""
    return [p.stem for p in _TEMPLATES_DIR.glob("*.html")]
