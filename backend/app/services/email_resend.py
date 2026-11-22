"""
Email service using Resend API (Phase 6).

Sends transactional emails via Resend.com API.
Uses httpx for HTTP requests (already in dependencies).
"""

import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
RESEND_TIMEOUT = 10.0  # 10 seconds


class EmailError(Exception):
    """Raised when email sending fails"""

    pass


def is_configured() -> bool:
    """
    Check if email service is configured.

    Returns:
        bool: True if RESEND_API_KEY is set, False otherwise
    """
    return settings.RESEND_API_KEY is not None and settings.RESEND_API_KEY.strip() != ""


def send_email(
    to: str,
    subject: str,
    body_html: str | None = None,
    body_text: str | None = None,
) -> str:
    """
    Send an email via Resend API.

    Args:
        to: Recipient email address
        subject: Email subject line
        body_html: HTML email body (optional)
        body_text: Plain text email body (optional)

    Returns:
        str: Resend message ID (e.g., "re_abc123xyz")

    Raises:
        EmailError: If email sending fails or API key not configured
    """
    if not is_configured():
        raise EmailError("Email service not configured: RESEND_API_KEY is missing")

    if not to or not to.strip():
        raise EmailError("Recipient email address is required")

    if not subject or not subject.strip():
        raise EmailError("Email subject is required")

    if not body_html and not body_text:
        raise EmailError("Email must have either HTML or plain text body")

    # Prepare request payload
    payload: dict[str, Any] = {
        "from": settings.EMAIL_FROM,
        "to": [to.strip()],
        "subject": subject.strip(),
    }

    if body_html:
        payload["html"] = body_html

    if body_text:
        payload["text"] = body_text

    # Prepare headers
    headers = {
        "Authorization": f"Bearer {settings.RESEND_API_KEY}",
        "Content-Type": "application/json",
    }

    # Send request
    try:
        logger.info(f"Sending email to {to} via Resend API")

        response = httpx.post(
            RESEND_API_URL,
            json=payload,
            headers=headers,
            timeout=RESEND_TIMEOUT,
        )

        # Check for errors
        if response.status_code not in (200, 201):
            error_detail = response.text[:200]  # Limit error message length
            logger.error(f"Resend API error ({response.status_code}): {error_detail}")
            raise EmailError(f"Resend API returned status {response.status_code}: {error_detail}")

        # Parse response
        data = response.json()
        message_id = data.get("id")

        if not message_id:
            logger.error(f"Resend API response missing 'id' field: {data}")
            raise EmailError("Resend API response missing message ID")

        logger.info(f"Email sent successfully: message_id={message_id}")
        return message_id

    except httpx.TimeoutException as e:
        logger.error(f"Resend API timeout after {RESEND_TIMEOUT}s: {e}")
        raise EmailError(f"Email sending timed out after {RESEND_TIMEOUT} seconds")

    except httpx.RequestError as e:
        logger.error(f"Resend API request error: {e}")
        raise EmailError(f"Failed to connect to Resend API: {str(e)}")

    except Exception as e:
        logger.error(f"Unexpected error sending email: {e}")
        raise EmailError(f"Unexpected error: {str(e)}")
