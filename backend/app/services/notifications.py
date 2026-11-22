"""
Notification Service (Phase 6).

Manages email queue with outbox pattern:
- Queue emails with deduplication
- Process pending emails with retry logic
- Background worker for async email delivery
- Test mode support for safe testing

Architecture:
1. queue_email() - Adds email to outbox (never blocks caller)
2. process_outbox() - Sends pending emails (called by worker)
3. run_email_worker() - Background loop that processes queue
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from string import Template
from zoneinfo import ZoneInfo

from sqlalchemy import and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.appointment import Appointment
from app.models.email_outbox import EmailOutbox, EmailStatus
from app.models.handoff import Handoff
from app.models.lead import Lead
from app.services.email_resend import EmailError, send_email

logger = logging.getLogger(__name__)

# Email retry backoff schedule (in seconds)
RETRY_BACKOFF = {
    1: 60,      # 1st retry: 1 minute
    2: 300,     # 2nd retry: 5 minutes
    3: 900,     # 3rd retry: 15 minutes
    4: 3600,    # 4th retry: 1 hour
}


def _get_next_retry_time(attempts: int) -> datetime:
    """
    Calculate next retry time based on attempts.

    Args:
        attempts: Number of previous attempts

    Returns:
        datetime: Next attempt time with exponential backoff
    """
    delay_seconds = RETRY_BACKOFF.get(attempts, 3600)  # Default to 1 hour
    return datetime.utcnow() + timedelta(seconds=delay_seconds)


def _resolve_recipient(
    to_email: str | None,
    is_customer_email: bool = False
) -> str | None:
    """
    Resolve final recipient based on test mode and email validity.

    Args:
        to_email: Original recipient email
        is_customer_email: True if this is a customer email (not sales team)

    Returns:
        str | None: Final recipient email, or None if should skip
    """
    if not to_email or not to_email.strip():
        return None

    # If test mode and customer email, redirect to test recipients
    if is_customer_email and settings.EMAIL_TEST_MODE:
        test_recipients = settings.email_test_recipients_list
        if not test_recipients:
            logger.warning("EMAIL_TEST_MODE enabled but EMAIL_TEST_RECIPIENTS not set")
            return None
        # Use first test recipient
        return test_recipients[0]

    return to_email.strip()


def queue_email(
    db: Session,
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str,
    dedupe_key: str | None = None,
    related_type: str | None = None,
    related_id: int | None = None,
    is_customer_email: bool = False,
) -> EmailOutbox | None:
    """
    Queue an email for delivery.

    Uses outbox pattern - email is persisted first, then sent asynchronously.
    Prevents duplicate sends via dedupe_key.

    Args:
        db: Database session
        to_email: Recipient email address
        subject: Email subject
        body_html: HTML email body
        body_text: Plain text email body
        dedupe_key: Unique key to prevent duplicates (optional)
        related_type: Entity type (e.g., "lead", "appointment", "handoff")
        related_id: Entity ID
        is_customer_email: True if sending to customer (applies test mode)

    Returns:
        EmailOutbox | None: Created outbox record, or None if skipped/duplicate
    """
    try:
        # Resolve final recipient (handles test mode redirect)
        final_recipient = _resolve_recipient(to_email, is_customer_email)

        if not final_recipient:
            logger.info(f"Skipping email (no recipient): subject={subject}")
            # Create skipped record for tracking
            outbox = EmailOutbox(
                to_email=to_email or "none@example.com",
                subject=subject,
                body_html=body_html,
                body_text=body_text,
                status=EmailStatus.SKIPPED,
                dedupe_key=dedupe_key,
                related_type=related_type,
                related_id=related_id,
                last_error="No valid recipient email address",
            )
            db.add(outbox)
            db.commit()
            return outbox

        # Create outbox entry
        outbox = EmailOutbox(
            to_email=final_recipient,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            status=EmailStatus.PENDING,
            attempts=0,
            max_attempts=5,
            next_attempt_at=datetime.utcnow(),
            dedupe_key=dedupe_key,
            related_type=related_type,
            related_id=related_id,
        )

        db.add(outbox)
        db.commit()
        db.refresh(outbox)

        logger.info(
            f"Email queued: id={outbox.id}, to={final_recipient}, "
            f"subject={subject}, dedupe_key={dedupe_key}"
        )
        return outbox

    except IntegrityError as e:
        db.rollback()
        # Dedupe key collision - email already queued
        if dedupe_key and "dedupe_key" in str(e).lower():
            logger.info(f"Email already queued (duplicate): dedupe_key={dedupe_key}")
            return None
        logger.error(f"Database integrity error queuing email: {e}")
        raise

    except Exception as e:
        db.rollback()
        logger.error(f"Failed to queue email: {e}", exc_info=True)
        raise


def _load_template(template_name: str) -> str:
    """
    Load email template from file.

    Args:
        template_name: Template filename (e.g., "qualified_lead.html")

    Returns:
        str: Template content
    """
    from pathlib import Path

    template_path = Path(__file__).parent.parent / "templates" / "email" / template_name
    return template_path.read_text(encoding="utf-8")


def _queue_sales_email(
    db: Session,
    subject: str,
    body_html: str,
    body_text: str,
    dedupe_key: str,
    related_type: str,
    related_id: int,
) -> EmailOutbox | None:
    """Queue the same email to every configured sales recipient."""
    sales_emails = settings.sales_team_list
    if not sales_emails:
        logger.warning(f"SALES_TEAM_EMAILS not configured, skipping {related_type} email")
        return None

    first_outbox = None
    for recipient in sales_emails:
        key = dedupe_key if len(sales_emails) == 1 else f"{dedupe_key}:{recipient}"
        outbox = queue_email(
            db=db,
            to_email=recipient,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            dedupe_key=key,
            related_type=related_type,
            related_id=related_id,
            is_customer_email=False,
        )
        if first_outbox is None:
            first_outbox = outbox

    return first_outbox


def queue_qualified_lead_email(db: Session, lead: Lead) -> EmailOutbox | None:
    """
    Queue email to sales team when lead becomes qualified.

    Args:
        db: Database session
        lead: Qualified lead

    Returns:
        EmailOutbox | None: Created outbox record
    """
    try:
        # Load templates
        html_template = Template(_load_template("qualified_lead.html"))
        text_template = Template(_load_template("qualified_lead.txt"))

        # Prepare template data
        customer = lead.customer
        display_name = customer.name if customer and customer.name else f"Lead #{lead.id}"
        data = {
            "lead_id": lead.id,
            "customer_name": customer.name if customer else "Unknown",
            "customer_email": customer.email if customer else "N/A",
            "customer_phone": customer.phone if customer else "N/A",
            "lead_status": lead.status.value.upper(),
            "summary": lead.summary or "No summary available yet.",
            "admin_url": f"http://localhost:3000/admin/leads/{lead.id}",
            "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        }

        # Render templates
        body_html = html_template.safe_substitute(data)
        body_text = text_template.safe_substitute(data)

        return _queue_sales_email(
            db=db,
            subject=f"New Qualified Lead: {display_name}",
            body_html=body_html,
            body_text=body_text,
            dedupe_key=f"qualified:{lead.id}",
            related_type="lead",
            related_id=lead.id,
        )

    except Exception as e:
        logger.error(f"Failed to queue qualified lead email for lead {lead.id}: {e}")
        return None


def queue_viewing_booked_emails(db: Session, appointment: Appointment) -> tuple[EmailOutbox | None, EmailOutbox | None]:
    """
    Queue emails when viewing appointment is booked.

    Sends 2 emails:
    1. Sales team notification
    2. Customer confirmation

    Args:
        db: Database session
        appointment: Booked appointment

    Returns:
        tuple[EmailOutbox | None, EmailOutbox | None]: (sales_email, customer_email)
    """
    sales_email = None
    customer_email = None

    try:
        # Load templates
        html_template = Template(_load_template("viewing_booked.html"))
        text_template = Template(_load_template("viewing_booked.txt"))

        # Prepare common data. Appointment links to Customer through Lead.
        property_obj = appointment.property
        lead = appointment.lead
        customer = lead.customer if lead else None

        scheduled_at = appointment.scheduled_at
        if scheduled_at and scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=UTC)

        business_tz = ZoneInfo(settings.GOOGLE_CALENDAR_TIMEZONE)
        appointment_datetime = (
            scheduled_at.astimezone(business_tz).strftime("%A, %B %d, %Y at %I:%M %p %Z")
            if scheduled_at
            else "N/A"
        )

        property_price = "N/A"
        if property_obj:
            property_price = f"AED {property_obj.price:,.0f}"
            if property_obj.rent_period:
                property_price = f"{property_price}/{property_obj.rent_period.value}"

        common_data = {
            "appointment_id": appointment.id,
            "appointment_datetime": appointment_datetime,
            "appointment_duration": f"{appointment.duration_minutes} minutes",
            "appointment_status": appointment.status.value.upper(),
            "property_title": property_obj.title if property_obj else "Property",
            "property_type": property_obj.property_type.value if property_obj else "N/A",
            "property_location": f"{property_obj.location}, {property_obj.community}" if property_obj else "N/A",
            "property_price": property_price,
            "property_bedrooms": property_obj.bedrooms if property_obj else 0,
            "property_bathrooms": property_obj.bathrooms if property_obj else 0,
            "property_size": f"{property_obj.size_sqft:,.0f} sq ft" if property_obj and property_obj.size_sqft else "N/A",
            "customer_name": customer.name if customer else "Unknown",
            "customer_email": customer.email if customer else "N/A",
            "customer_phone": customer.phone if customer else "N/A",
            "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        }

        # 1. Sales team email
        sales_data = {
            **common_data,
            "recipient_type": "Sales Team Notification",
            "greeting": "Hi Team,",
            "note_text": "Please ensure the property is ready and contact the customer to confirm attendance.",
            "footer_text": "Review the appointment details and prepare for the viewing.",
        }

        body_html_sales = html_template.safe_substitute(sales_data)
        body_text_sales = text_template.safe_substitute(sales_data)

        sales_emails = settings.sales_team_list
        if sales_emails:
            sales_email = _queue_sales_email(
                db=db,
                subject=f"New Viewing Booked: {common_data['property_title']}",
                body_html=body_html_sales,
                body_text=body_text_sales,
                dedupe_key=f"viewing:{appointment.id}:sales",
                related_type="appointment",
                related_id=appointment.id,
            )

        # 2. Customer confirmation email
        if customer and customer.email:
            # Load customer-specific template
            html_template_customer = Template(_load_template("appointment_confirmation.html"))
            text_template_customer = Template(_load_template("appointment_confirmation.txt"))

            customer_data = {
                **common_data,
                "customer_name": customer.name,
            }

            body_html_customer = html_template_customer.safe_substitute(customer_data)
            body_text_customer = text_template_customer.safe_substitute(customer_data)

            customer_email = queue_email(
                db=db,
                to_email=customer.email,
                subject=f"Viewing Confirmed: {common_data['property_title']}",
                body_html=body_html_customer,
                body_text=body_text_customer,
                dedupe_key=f"viewing:{appointment.id}:customer",
                related_type="appointment",
                related_id=appointment.id,
                is_customer_email=True,
            )

    except Exception as e:
        logger.error(f"Failed to queue viewing booked emails for appointment {appointment.id}: {e}")

    return sales_email, customer_email


def queue_handoff_email(db: Session, handoff: Handoff, lead: Lead) -> EmailOutbox | None:
    """
    Queue email to sales team when human handoff is requested.

    Args:
        db: Database session
        handoff: Handoff record
        lead: Associated lead

    Returns:
        EmailOutbox | None: Created outbox record
    """
    try:
        # Load templates
        html_template = Template(_load_template("handoff.html"))
        text_template = Template(_load_template("handoff.txt"))

        # Prepare template data
        customer = lead.customer
        display_name = customer.name if customer and customer.name else f"Lead #{lead.id}"
        data = {
            "reason": handoff.reason or "Customer requested human assistance",
            "customer_name": customer.name if customer else "Unknown",
            "customer_email": customer.email if customer else "N/A",
            "customer_phone": customer.phone if customer else "N/A",
            "lead_id": lead.id,
            "lead_status": lead.status.value.upper(),
            "summary": lead.summary or handoff.summary or "No summary available.",
            "admin_url": f"http://localhost:3000/admin/leads/{lead.id}",
            "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        }

        # Render templates
        body_html = html_template.safe_substitute(data)
        body_text = text_template.safe_substitute(data)

        return _queue_sales_email(
            db=db,
            subject=f"Human Agent Required: {display_name}",
            body_html=body_html,
            body_text=body_text,
            dedupe_key=f"handoff:{handoff.id}",
            related_type="handoff",
            related_id=handoff.id,
        )

    except Exception as e:
        logger.error(f"Failed to queue handoff email for handoff {handoff.id}: {e}")
        return None


def process_outbox(db: Session, limit: int = 20) -> dict[str, int]:
    """
    Process pending emails in the outbox.

    Sends emails that are due for sending (next_attempt_at <= now).
    Updates status based on send result.

    Args:
        db: Database session
        limit: Maximum number of emails to process

    Returns:
        dict: Statistics {"sent": n, "failed": n, "skipped": n}
    """
    stats = {"sent": 0, "failed": 0, "skipped": 0}

    try:
        # Find pending emails ready to send
        pending_emails = (
            db.query(EmailOutbox)
            .filter(
                and_(
                    EmailOutbox.status == EmailStatus.PENDING,
                    EmailOutbox.next_attempt_at <= datetime.utcnow(),
                    EmailOutbox.attempts < EmailOutbox.max_attempts,
                )
            )
            .order_by(EmailOutbox.next_attempt_at)
            .limit(limit)
            .all()
        )

        logger.info(f"Processing {len(pending_emails)} pending emails")

        for email in pending_emails:
            try:
                # Attempt to send
                message_id = send_email(
                    to=email.to_email,
                    subject=email.subject,
                    body_html=email.body_html,
                    body_text=email.body_text,
                )

                # Mark as sent
                email.status = EmailStatus.SENT
                email.sent_at = datetime.utcnow()
                email.last_error = None
                db.commit()

                stats["sent"] += 1
                logger.info(f"Email sent successfully: id={email.id}, message_id={message_id}")

            except EmailError as e:
                # Email sending failed
                email.attempts += 1
                email.last_error = str(e)[:500]  # Limit error message length

                if email.attempts >= email.max_attempts:
                    # Max attempts reached, mark as failed
                    email.status = EmailStatus.FAILED
                    stats["failed"] += 1
                    logger.error(f"Email failed permanently: id={email.id}, error={e}")
                else:
                    # Schedule retry with backoff
                    email.next_attempt_at = _get_next_retry_time(email.attempts)
                    stats["skipped"] += 1
                    logger.warning(
                        f"Email send failed, will retry: id={email.id}, "
                        f"attempt={email.attempts}/{email.max_attempts}, "
                        f"next_retry={email.next_attempt_at}, error={e}"
                    )

                db.commit()

            except Exception as e:
                # Unexpected error
                db.rollback()
                logger.error(f"Unexpected error processing email {email.id}: {e}", exc_info=True)
                stats["skipped"] += 1

    except Exception as e:
        logger.error(f"Error in process_outbox: {e}", exc_info=True)

    return stats


async def run_email_worker():
    """
    Background worker that processes email outbox.

    Runs continuously, checking for pending emails at regular intervals.
    Automatically stops if EMAIL_WORKER_ENABLED is disabled.
    """
    from app.core.database import SessionLocal

    logger.info("Email worker started")

    try:
        while settings.EMAIL_WORKER_ENABLED:
            try:
                db = SessionLocal()
                try:
                    stats = process_outbox(db, limit=20)
                    if stats["sent"] > 0 or stats["failed"] > 0:
                        logger.info(f"Email worker cycle complete: {stats}")
                finally:
                    db.close()

            except Exception as e:
                logger.error(f"Error in email worker cycle: {e}", exc_info=True)

            # Wait before next cycle
            await asyncio.sleep(settings.EMAIL_WORKER_INTERVAL_SECONDS)

    except asyncio.CancelledError:
        logger.info("Email worker cancelled")
    except Exception as e:
        logger.error(f"Email worker crashed: {e}", exc_info=True)
    finally:
        logger.info("Email worker stopped")


def retry_email(db: Session, outbox_id: int) -> bool:
    """
    Manually retry a failed email.

    Resets attempts and schedules for immediate retry.

    Args:
        db: Database session
        outbox_id: Email outbox ID

    Returns:
        bool: True if email was reset for retry
    """
    try:
        email = db.query(EmailOutbox).filter(EmailOutbox.id == outbox_id).first()

        if not email:
            logger.warning(f"Email not found for retry: id={outbox_id}")
            return False

        if email.status == EmailStatus.SENT:
            logger.warning(f"Cannot retry already sent email: id={outbox_id}")
            return False

        # Reset for retry
        email.status = EmailStatus.PENDING
        email.attempts = 0
        email.next_attempt_at = datetime.utcnow()
        email.last_error = None

        db.commit()
        logger.info(f"Email reset for retry: id={outbox_id}")
        return True

    except Exception as e:
        db.rollback()
        logger.error(f"Failed to retry email {outbox_id}: {e}")
        return False
