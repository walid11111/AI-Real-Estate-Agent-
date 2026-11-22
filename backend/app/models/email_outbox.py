"""
Email Outbox Model (Phase 6).

Transactional email queue with retry logic and deduplication.
All emails are queued here first, then processed by background worker.
"""

import enum

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.sql import func

from app.core.database import Base
from app.models._types import enum_values


class EmailStatus(str, enum.Enum):
    """Email delivery status"""

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"  # No recipient or test mode skip


class EmailOutbox(Base):
    """
    Email outbox for transactional emails.

    Emails are queued here and processed by a background worker.
    Failures are retried with exponential backoff.
    Dedupe prevents duplicate sends.
    """

    __tablename__ = "email_outbox"

    id = Column(Integer, primary_key=True, index=True)

    # Email content
    to_email = Column(String, nullable=False, index=True)
    subject = Column(String, nullable=False)
    body_text = Column(Text, nullable=True)  # Plain text version
    body_html = Column(Text, nullable=True)  # HTML version

    # Delivery tracking
    status = Column(
        SQLEnum(EmailStatus, name="emailstatus", create_constraint=False, values_callable=enum_values),
        nullable=False,
        default=EmailStatus.PENDING,
        index=True
    )
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)
    next_attempt_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)

    # Deduplication
    dedupe_key = Column(String, nullable=True, unique=True, index=True)

    # Related entity tracking
    related_type = Column(String, nullable=True, index=True)  # "lead", "appointment", "handoff"
    related_id = Column(Integer, nullable=True)

    # Error tracking
    last_error = Column(Text, nullable=True)

    # Timestamps
    sent_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
