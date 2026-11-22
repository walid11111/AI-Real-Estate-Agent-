"""
Notify sales team tool - Human agent handoff (Phase 6).
AI agent uses this to transfer conversation to human agent.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.schemas.tools import NotifySalesTeamInput, NotifySalesTeamOutput

# ============================================================================
# TOOL DEFINITION
# ============================================================================

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "notify_sales_team",
        "description": (
            "Transfer conversation to a human sales agent. "
            "Use this when customer requests human contact, has complex requirements "
            "beyond your capabilities, or when immediate human attention is needed. "
            "After calling this, inform customer: 'A sales consultant will contact you soon.'"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "lead_id": {
                    "type": "integer",
                    "description": "Lead ID (optional - will be created if missing)",
                },
                "conversation_id": {
                    "type": "integer",
                    "description": "Conversation ID (auto-injected by system)",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["low", "normal", "high", "urgent"],
                    "description": "Urgency level of the handoff",
                    "default": "normal",
                },
                "message": {
                    "type": "string",
                    "description": "Reason for handoff or context for human agent",
                },
            },
            "required": ["message"],
        },
    },
}


# ============================================================================
# TOOL EXECUTION
# ============================================================================

def notify_sales_team_tool(
    db: Session,
    params: dict[str, Any],
) -> NotifySalesTeamOutput:
    """
    Execute human agent handoff.

    Args:
        db: Database session
        params: Parameters from AI (includes conversation_id injected by loop)

    Returns:
        NotifySalesTeamOutput with handoff confirmation
    """
    # Validate input
    input_data = NotifySalesTeamInput(**params)

    # Get conversation (required for handoff)
    conversation_id = input_data.conversation_id
    if not conversation_id:
        raise ValueError("conversation_id is required for handoff")

    from app.models.conversation import Conversation
    conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()

    if not conversation:
        raise ValueError(f"Conversation ID {conversation_id} not found")

    # Request handoff (creates lead if needed, queues email, etc.)
    from app.services.handoff import request_handoff

    result = request_handoff(
        db=db,
        conversation=conversation,
        reason=input_data.message,
        source="tool",
    )

    if not result.success:
        raise RuntimeError(f"Handoff failed: {result.error}")

    # Return success with handoff details
    return NotifySalesTeamOutput(
        notification_sent=True,
        notified_agents=["Sales Team"],  # Generic - actual emails from config
        notification_id=f"handoff_{result.handoff_id}",
        handoff_id=result.handoff_id,
        lead_id=result.lead_id,
    )


# ============================================================================
# TOOL METADATA
# ============================================================================

def get_tool_name() -> str:
    """Get tool name for registry."""
    return "notify_sales_team"


def get_tool_definition() -> dict:
    """Get tool definition for LLM."""
    return TOOL_DEFINITION
