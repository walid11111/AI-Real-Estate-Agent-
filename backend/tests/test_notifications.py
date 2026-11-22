"""
Tests for notification service (Phase 6).

Tests email queueing, processing, deduplication, and retry logic.
"""

from datetime import datetime, timedelta

import pytest

from app.models.customer import Customer
from app.models.email_outbox import EmailOutbox, EmailStatus
from app.models.handoff import Handoff
from app.models.lead import Lead, LeadStatus
from app.services.notifications import (
    process_outbox,
    queue_email,
    queue_handoff_email,
    queue_qualified_lead_email,
    retry_email,
)


def test_queue_email_creates_row(db_session):
    """Test that queue_email creates outbox record"""
    outbox = queue_email(
        db=db_session,
        to_email="test@example.com",
        subject="Test Subject",
        body_html="<p>Test</p>",
        body_text="Test",
        dedupe_key="test:123",
        related_type="test",
        related_id=123,
    )

    assert outbox is not None
    assert outbox.to_email == "test@example.com"
    assert outbox.subject == "Test Subject"
    assert outbox.status == EmailStatus.PENDING
    assert outbox.dedupe_key == "test:123"


def test_queue_email_dedupe_prevents_duplicates(db_session):
    """Test that dedupe_key prevents duplicate emails"""
    # First email
    outbox1 = queue_email(
        db=db_session,
        to_email="test@example.com",
        subject="Test",
        body_html="<p>Test</p>",
        body_text="Test",
        dedupe_key="duplicate:test",
    )

    assert outbox1 is not None

    # Second email with same dedupe_key
    outbox2 = queue_email(
        db=db_session,
        to_email="test@example.com",
        subject="Test",
        body_html="<p>Test</p>",
        body_text="Test",
        dedupe_key="duplicate:test",
    )

    # Should return None (duplicate)
    assert outbox2 is None

    # Only one email in database
    count = db_session.query(EmailOutbox).count()
    assert count == 1


def test_queue_email_skips_empty_recipient(db_session):
    """Test that empty recipient creates skipped record"""
    outbox = queue_email(
        db=db_session,
        to_email="",
        subject="Test",
        body_html="<p>Test</p>",
        body_text="Test",
    )

    assert outbox is not None
    assert outbox.status == EmailStatus.SKIPPED
    assert "no valid recipient" in outbox.last_error.lower()


def test_process_outbox_sends_pending_emails(db_session, mock_email_provider):
    """Test that process_outbox sends pending emails"""
    # Create pending email
    outbox = queue_email(
        db=db_session,
        to_email="test@example.com",
        subject="Test",
        body_html="<p>Test</p>",
        body_text="Test",
    )

    # Process outbox
    stats = process_outbox(db_session, limit=10)

    assert stats["sent"] == 1
    assert stats["failed"] == 0

    # Check email was marked sent
    db_session.refresh(outbox)
    assert outbox.status == EmailStatus.SENT
    assert outbox.sent_at is not None


def test_process_outbox_handles_send_failure(db_session, mock_email_provider):
    """Test that process_outbox handles email send failures"""
    # Mock email provider to raise error
    from app.services.email_resend import EmailError
    mock_email_provider.side_effect = EmailError("Send failed")

    # Create pending email
    outbox = queue_email(
        db=db_session,
        to_email="test@example.com",
        subject="Test",
        body_html="<p>Test</p>",
        body_text="Test",
    )

    # Process outbox
    stats = process_outbox(db_session, limit=10)

    assert stats["sent"] == 0
    assert stats["skipped"] == 1  # Scheduled for retry

    # Check email was marked for retry
    db_session.refresh(outbox)
    assert outbox.status == EmailStatus.PENDING
    assert outbox.attempts == 1
    assert outbox.last_error == "Send failed"
    assert outbox.next_attempt_at > datetime.utcnow()


def test_process_outbox_max_attempts_marks_failed(db_session, mock_email_provider):
    """Test that max attempts marks email as failed"""
    from app.services.email_resend import EmailError
    mock_email_provider.side_effect = EmailError("Always fails")

    # Create pending email with max attempts already reached
    outbox = EmailOutbox(
        to_email="test@example.com",
        subject="Test",
        body_html="<p>Test</p>",
        body_text="Test",
        status=EmailStatus.PENDING,
        attempts=4,  # Already 4 attempts
        max_attempts=5,
        next_attempt_at=datetime.utcnow(),
    )
    db_session.add(outbox)
    db_session.commit()

    # Process outbox (will fail on 5th attempt)
    stats = process_outbox(db_session, limit=10)

    assert stats["failed"] == 1

    # Check email marked as permanently failed
    db_session.refresh(outbox)
    assert outbox.status == EmailStatus.FAILED
    assert outbox.attempts == 5


def test_retry_backoff_schedule(db_session, mock_email_provider):
    """Test exponential backoff retry schedule"""
    from app.services.email_resend import EmailError
    mock_email_provider.side_effect = EmailError("Retry test")

    outbox = queue_email(
        db=db_session,
        to_email="test@example.com",
        subject="Test",
        body_html="<p>Test</p>",
        body_text="Test",
    )

    # Process multiple times to test backoff. Advance the scheduled time
    # between runs so process_outbox selects the row again.
    for attempt in range(1, 5):
        process_outbox(db_session, limit=10)
        db_session.refresh(outbox)

        assert outbox.attempts == attempt
        assert outbox.next_attempt_at > datetime.utcnow()

        outbox.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
        db_session.commit()


def test_send_email_missing_api_key_raises(monkeypatch):
    """Test that the provider reports a clear error when unconfigured."""
    from app.core import config
    from app.services.email_resend import EmailError, send_email

    monkeypatch.setattr(config.settings, "RESEND_API_KEY", None)

    with pytest.raises(EmailError, match="not configured"):
        send_email(
            to="test@example.com",
            subject="Test",
            body_text="Test",
        )


def test_qualified_lead_email_queues_correctly(db_session):
    """Test queue_qualified_lead_email creates email"""
    # Create customer and lead
    customer = Customer(name="Test User", email="test@example.com", phone="+971501234567")
    db_session.add(customer)
    db_session.commit()

    lead = Lead(
        customer_id=customer.id,
        status=LeadStatus.QUALIFIED,
    )
    lead.summary = "Test summary"  # Set after creation
    db_session.add(lead)
    db_session.commit()

    # Queue email
    outbox = queue_qualified_lead_email(db_session, lead)

    assert outbox is not None
    assert "qualified" in outbox.subject.lower()
    assert outbox.dedupe_key == f"qualified:{lead.id}"
    assert outbox.related_type == "lead"
    assert outbox.related_id == lead.id


def test_handoff_email_queues_correctly(db_session):
    """Test queue_handoff_email creates email"""
    # Create customer, lead, handoff
    customer = Customer(name="Test User", email="test@example.com", phone="+971501234567")
    db_session.add(customer)
    db_session.commit()

    lead = Lead(customer_id=customer.id, status=LeadStatus.HOT)
    db_session.add(lead)
    db_session.commit()

    handoff = Handoff(
        conversation_id=1,
        lead_id=lead.id,
        reason="Test handoff",
        summary="Test summary",
    )
    db_session.add(handoff)
    db_session.commit()

    # Queue email
    outbox = queue_handoff_email(db_session, handoff, lead)

    assert outbox is not None
    assert "human agent" in outbox.subject.lower() or "required" in outbox.subject.lower()
    assert outbox.dedupe_key == f"handoff:{handoff.id}"


def test_retry_email_resets_status(db_session):
    """Test retry_email resets failed email"""
    # Create failed email
    outbox = EmailOutbox(
        to_email="test@example.com",
        subject="Test",
        body_html="<p>Test</p>",
        body_text="Test",
        status=EmailStatus.FAILED,
        attempts=5,
        next_attempt_at=datetime.utcnow() + timedelta(hours=1),
    )
    db_session.add(outbox)
    db_session.commit()

    # Retry
    success = retry_email(db_session, outbox.id)

    assert success is True

    # Check email reset
    db_session.refresh(outbox)
    assert outbox.status == EmailStatus.PENDING
    assert outbox.attempts == 0
    assert outbox.next_attempt_at <= datetime.utcnow()


def test_test_mode_redirects_customer_emails(db_session, monkeypatch):
    """Test that EMAIL_TEST_MODE redirects customer emails"""
    # Set test mode
    from app.core import config
    monkeypatch.setattr(config.settings, "EMAIL_TEST_MODE", True)
    monkeypatch.setattr(config.settings, "EMAIL_TEST_RECIPIENTS", "testrecipient@example.com")

    # Queue customer email
    outbox = queue_email(
        db=db_session,
        to_email="customer@example.com",
        subject="Test",
        body_html="<p>Test</p>",
        body_text="Test",
        is_customer_email=True,
    )

    # Should be redirected to test recipient
    assert outbox.to_email == "testrecipient@example.com"


def test_sales_emails_not_redirected_in_test_mode(db_session, monkeypatch):
    """Test that sales team emails are NOT redirected in test mode"""
    from app.core import config
    monkeypatch.setattr(config.settings, "EMAIL_TEST_MODE", True)
    monkeypatch.setattr(config.settings, "EMAIL_TEST_RECIPIENTS", "testrecipient@example.com")

    # Queue sales email
    outbox = queue_email(
        db=db_session,
        to_email="sales@company.com",
        subject="Test",
        body_html="<p>Test</p>",
        body_text="Test",
        is_customer_email=False,  # Sales email
    )

    # Should NOT be redirected
    assert outbox.to_email == "sales@company.com"


def test_qualified_lead_email_reaches_every_sales_recipient(db_session, monkeypatch):
    """SALES_TEAM_EMAILS is a list: every recipient gets its own outbox row."""
    from app.core import config

    monkeypatch.setattr(config.settings, "SALES_TEAM_EMAILS", "one@example.com, two@example.com")

    customer = Customer(name="Multi Recipient", email="multi@example.com", phone="+971500000001")
    db_session.add(customer)
    db_session.commit()

    lead = Lead(customer_id=customer.id, status=LeadStatus.QUALIFIED)
    db_session.add(lead)
    db_session.commit()

    queue_qualified_lead_email(db_session, lead)

    rows = db_session.query(EmailOutbox).all()
    assert {row.to_email for row in rows} == {"one@example.com", "two@example.com"}
    assert {row.dedupe_key for row in rows} == {
        f"qualified:{lead.id}:one@example.com",
        f"qualified:{lead.id}:two@example.com",
    }
