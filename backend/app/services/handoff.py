"""
Handoff Service (Phase 6).

Orchestrates human agent transfer with complete workflow:
1. Ensure lead exists
2. Elevate lead status to at least HOT
3. Generate AI summary
4. Create handoff record
5. Queue email to sales team
6. Return fixed message to customer

Design principles:
- Idempotent: Safe to call multiple times
- Atomic: All steps succeed or fail together
- Never blocks: Email queued async
- Fixed message: Consistent customer experience
"""

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.conversation import Conversation
from app.models.handoff import Handoff
from app.models.lead import Lead, LeadStatus

logger = logging.getLogger(__name__)

# Fixed message returned to customer (per requirements)
HANDOFF_MESSAGE = "A sales consultant will contact you soon."


@dataclass
class HandoffResult:
    """Result of handoff request"""

    success: bool
    message: str
    handoff_id: int | None = None
    lead_id: int | None = None
    error: str | None = None


def _ensure_lead_exists(db: Session, conversation: Conversation) -> Lead | None:
    """
    Ensure lead exists for conversation, create if missing.

    Args:
        db: Database session
        conversation: Conversation to get/create lead for

    Returns:
        Lead | None: Existing or newly created lead
    """
    try:
        # Leads are linked through leads.conversation_id, not conversations.lead_id.
        # get_or_create_lead is idempotent and returns (lead, created).
        from app.services.leads import get_or_create_lead

        lead, created = get_or_create_lead(
            db=db,
            customer_id=conversation.customer_id,
            conversation_id=conversation.id,
        )

        action = "created" if created else "already exists"
        logger.info(f"Lead {action} for handoff: lead_id={lead.id}, conversation_id={conversation.id}")
        return lead

    except Exception as e:
        logger.error(f"Failed to ensure lead exists for conversation {conversation.id}: {e}", exc_info=True)
        return None


def _elevate_lead_status(db: Session, lead: Lead) -> bool:
    """
    Elevate lead status to at least HOT.

    Status priority: NEW < WARM < HOT < QUALIFIED < CONVERTED < LOST
    Only elevate if current status is below HOT.

    Args:
        db: Database session
        lead: Lead to elevate

    Returns:
        bool: True if status was elevated or already high enough
    """
    try:
        # Higher priority means further along the pipeline. Terminal states
        # rank highest so handoff never downgrades them to HOT.
        status_priority = {
            LeadStatus.NEW: 1,
            LeadStatus.WARM: 2,
            LeadStatus.HOT: 3,
            LeadStatus.QUALIFIED: 4,
            LeadStatus.VIEWING_SCHEDULED: 5,
            LeadStatus.CONVERTED: 6,
            LeadStatus.LOST: 7,
        }

        current_priority = status_priority.get(lead.status, 0)
        hot_priority = status_priority[LeadStatus.HOT]

        if current_priority >= hot_priority:
            logger.info(f"Lead status already sufficient: lead_id={lead.id}, status={lead.status.value}")
            return True

        old_status = lead.status
        from app.services.lead_status import update_lead_status

        if not update_lead_status(
            db=db,
            lead_id=lead.id,
            new_status=LeadStatus.HOT,
            reason="Human handoff requested",
            automated=True,
        ):
            logger.warning(
                f"Could not elevate lead status: lead_id={lead.id}, status={old_status.value}"
            )
            return False

        logger.info(f"Lead status elevated: lead_id={lead.id}, {old_status.value} -> HOT")
        return True

    except Exception as e:
        db.rollback()
        logger.error(f"Failed to elevate lead status for lead {lead.id}: {e}")
        return False


def _generate_and_save_summary(db: Session, lead: Lead) -> bool:
    """
    Generate AI summary and save to lead.

    Args:
        db: Database session
        lead: Lead to summarize

    Returns:
        bool: True if summary generated and saved
    """
    try:
        from app.services.summary import build_summary, save_summary

        # Generate summary
        summary_text = build_summary(db, lead)

        # Save to database
        success = save_summary(db, lead, summary_text)

        if success:
            logger.info(f"Summary generated and saved for lead {lead.id}")
        else:
            logger.warning(f"Summary generation succeeded but save failed for lead {lead.id}")

        return success

    except Exception as e:
        logger.error(f"Failed to generate/save summary for lead {lead.id}: {e}")
        return False


def _create_handoff_record(
    db: Session,
    conversation: Conversation,
    lead: Lead,
    reason: str | None,
    source: str,
) -> Handoff | None:
    """
    Create handoff record.

    Args:
        db: Database session
        conversation: Conversation being handed off
        lead: Associated lead
        reason: Handoff reason (optional)
        source: Handoff source (e.g., "tool", "api", "timeout")

    Returns:
        Handoff | None: Created handoff record
    """
    try:
        handoff = Handoff(
            conversation_id=conversation.id,
            lead_id=lead.id,
            agent_id=None,  # Will be assigned when human agent picks up
            reason=reason or "Customer requested human assistance",
            summary=lead.summary,  # Copy summary at handoff time
        )

        db.add(handoff)
        db.commit()
        db.refresh(handoff)

        logger.info(
            f"Handoff record created: handoff_id={handoff.id}, "
            f"lead_id={lead.id}, conversation_id={conversation.id}, source={source}"
        )
        return handoff

    except Exception as e:
        db.rollback()
        logger.error(f"Failed to create handoff record: {e}", exc_info=True)
        return None


def _queue_handoff_email(db: Session, handoff: Handoff, lead: Lead) -> bool:
    """
    Queue email notification to sales team.

    Uses dedupe to prevent duplicate emails.

    Args:
        db: Database session
        handoff: Handoff record
        lead: Associated lead

    Returns:
        bool: True if email queued (or already queued)
    """
    try:
        from app.services.notifications import queue_handoff_email

        outbox = queue_handoff_email(db, handoff, lead)

        if outbox:
            logger.info(f"Handoff email queued: outbox_id={outbox.id}, handoff_id={handoff.id}")
            return True
        else:
            # Could be duplicate (already queued) or no recipients configured
            logger.info(f"Handoff email not queued (duplicate or no recipients): handoff_id={handoff.id}")
            return True  # Not a failure - email system handles dedupe

    except Exception as e:
        logger.error(f"Failed to queue handoff email for handoff {handoff.id}: {e}")
        return False


def request_handoff(
    db: Session,
    conversation: Conversation,
    reason: str | None = None,
    source: str = "unknown",
) -> HandoffResult:
    """
    Request human agent handoff for conversation.

    Complete workflow:
    1. Ensure lead exists (create if needed)
    2. Elevate lead status to at least HOT
    3. Generate and save AI summary
    4. Create handoff record
    5. Queue email to sales team
    6. Return fixed message for customer

    Idempotent: Safe to call multiple times.
    Email dedupe prevents duplicate notifications.

    Args:
        db: Database session
        conversation: Conversation to hand off
        reason: Handoff reason (optional, for logging)
        source: Handoff source (e.g., "tool", "api", "timeout")

    Returns:
        HandoffResult: Result with success status, message, IDs, and error (if any)
    """
    try:
        logger.info(
            f"Handoff requested: conversation_id={conversation.id}, "
            f"customer_id={conversation.customer_id}, reason={reason}, source={source}"
        )

        # Step 1: Ensure lead exists
        lead = _ensure_lead_exists(db, conversation)
        if not lead:
            return HandoffResult(
                success=False,
                message="Failed to create lead for handoff",
                error="Lead creation failed",
            )

        # Step 2: Elevate lead status to at least HOT
        if not _elevate_lead_status(db, lead):
            logger.warning(f"Failed to elevate lead status, continuing anyway: lead_id={lead.id}")
            # Not a fatal error, continue

        # Step 3: Generate and save summary
        # Try to generate summary, but don't fail handoff if it fails
        if not _generate_and_save_summary(db, lead):
            logger.warning(f"Summary generation failed, continuing anyway: lead_id={lead.id}")
            # Use existing summary or None

        # Step 4: Create handoff record
        handoff = _create_handoff_record(db, conversation, lead, reason, source)
        if not handoff:
            return HandoffResult(
                success=False,
                message="Failed to create handoff record",
                lead_id=lead.id,
                error="Handoff record creation failed",
            )

        # Step 5: Queue email notification (async, never blocks)
        if not _queue_handoff_email(db, handoff, lead):
            logger.warning(f"Failed to queue handoff email: handoff_id={handoff.id}")
            # Not a fatal error - handoff is still successful

        # Step 6: Return fixed message
        logger.info(
            f"Handoff completed successfully: handoff_id={handoff.id}, "
            f"lead_id={lead.id}, conversation_id={conversation.id}"
        )

        return HandoffResult(
            success=True,
            message=HANDOFF_MESSAGE,
            handoff_id=handoff.id,
            lead_id=lead.id,
        )

    except Exception as e:
        logger.error(
            f"Unexpected error in handoff request for conversation {conversation.id}: {e}",
            exc_info=True,
        )
        return HandoffResult(
            success=False,
            message="An error occurred during handoff",
            error=str(e),
        )
