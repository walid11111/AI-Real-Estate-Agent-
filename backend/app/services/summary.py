"""
Summary Service (Phase 6).

Generates AI-powered lead summaries for sales team.

CRITICAL: NO PII sent to LLM
- PII includes: name, email, phone, whatsapp_id
- Only business facts (budget, location, preferences) sent to LLM
- PII added back to final summary AFTER AI generation

Architecture:
1. build_facts() - Extract lead data (NO PII)
2. generate_ai_fields() - LLM generates interest + next_action
3. build_summary() - Combine facts + AI analysis into ASCII text
4. save_summary() - Persist to leads.summary column
"""

import json
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.lead import Lead, LeadProperty

logger = logging.getLogger(__name__)

# LLM timeout (seconds) - fail fast to avoid blocking
LLM_TIMEOUT = 10


def build_facts(db: Session, lead: Lead) -> dict:
    """
    Extract lead facts for summary generation.

    CRITICAL: NO PII included in facts dict.
    PII = name, email, phone, whatsapp_id

    Args:
        db: Database session
        lead: Lead to extract facts from

    Returns:
        dict: Lead facts WITHOUT PII
    """
    # Parse requirements JSON if available
    requirements = {}
    if lead.requirements:
        try:
            requirements = json.loads(lead.requirements)
        except Exception:
            requirements = {}

    facts = {
        # Lead metadata (no PII)
        "lead_id": lead.id,
        "lead_status": lead.status.value.upper(),
        "lead_source": lead.source.value if lead.source else "unknown",
        "created_at": lead.created_at.strftime("%Y-%m-%d") if lead.created_at else "unknown",

        # Search criteria from requirements JSON (no PII)
        "listing_type": requirements.get("listing_type"),
        "budget": requirements.get("budget"),
        "budget_min": requirements.get("budget_min"),
        "budget_max": requirements.get("budget_max"),
        "location": requirements.get("location"),
        "bedrooms": requirements.get("bedrooms"),
        "property_type": requirements.get("property_type"),
        "timeline": lead.expected_timeline,

        # Properties shown
        "properties_count": 0,
        "properties": [],

        # Appointments
        "appointments_count": 0,
        "appointments": [],

        # Engagement metrics
        "messages_count": 0,
        "last_activity": None,
    }

    # Get associated properties (via LeadProperty)
    try:
        lead_properties = (
            db.query(LeadProperty)
            .filter(LeadProperty.lead_id == lead.id)
            .all()
        )

        facts["properties_count"] = len(lead_properties)

        for lp in lead_properties[:5]:  # Limit to first 5 properties
            if lp.property:
                prop = lp.property
                facts["properties"].append({
                    "title": prop.title,
                    "type": prop.property_type.value,
                    "location": f"{prop.location}, {prop.city}",
                    "price": prop.price,
                    "bedrooms": prop.bedrooms,
                    "interested": bool(lp.interested),
                    "viewed": lp.viewed_at is not None,
                })
    except Exception as e:
        logger.warning(f"Failed to load properties for lead {lead.id}: {e}")

    # Get appointments
    try:
        if hasattr(lead, "appointments") and lead.appointments:
            facts["appointments_count"] = len(lead.appointments)

            for apt in lead.appointments[:3]:  # Limit to first 3 appointments
                facts["appointments"].append({
                    "property_title": apt.property.title if apt.property else "Unknown",
                    "datetime": apt.scheduled_at.strftime("%Y-%m-%d %H:%M") if apt.scheduled_at else "Unknown",
                    "status": apt.status.value.upper(),
                })

            # Last appointment date as last activity
            if lead.appointments:
                last_apt = max(lead.appointments, key=lambda a: a.scheduled_at or datetime.min)
                facts["last_activity"] = last_apt.scheduled_at.strftime("%Y-%m-%d") if last_apt.scheduled_at else None
    except Exception as e:
        logger.warning(f"Failed to load appointments for lead {lead.id}: {e}")

    # Get conversation message count
    try:
        if lead.conversation and hasattr(lead.conversation, "messages"):
            facts["messages_count"] = len(lead.conversation.messages)

            # If no appointment activity, use last message as activity
            if not facts["last_activity"] and lead.conversation.messages:
                last_msg = max(lead.conversation.messages, key=lambda m: m.created_at or datetime.min)
                facts["last_activity"] = last_msg.created_at.strftime("%Y-%m-%d") if last_msg.created_at else None
    except Exception as e:
        logger.warning(f"Failed to load messages for lead {lead.id}: {e}")

    return facts


def _format_aed(value) -> str:
    """Format a numeric AED value without failing on missing/malformed data."""
    if value is None or value == "":
        return "N/A"
    try:
        return f"AED {float(value):,.0f}"
    except (TypeError, ValueError):
        return f"AED {value}"


def _format_budget(facts: dict) -> str:
    """Render whichever budget fields are present."""
    budget_min = facts.get("budget_min")
    budget_max = facts.get("budget_max")

    if budget_min is not None and budget_max is not None:
        return f"{_format_aed(budget_min)} - {_format_aed(budget_max)}"
    if budget_max is not None:
        return f"up to {_format_aed(budget_max)}"
    if budget_min is not None:
        return f"from {_format_aed(budget_min)}"
    if facts.get("budget") is not None:
        return _format_aed(facts.get("budget"))
    return "Not specified"


def generate_ai_fields(facts: dict, db: Session | None = None) -> tuple[str, str]:
    """
    Generate AI-powered customer interest and next action fields.

    Uses existing LLMClient to analyze lead facts.
    Falls back to generic text on timeout/error.

    Args:
        facts: Lead facts (NO PII)
        db: Database session used to resolve the configured AI provider/key

    Returns:
        tuple[str, str]: (customer_interest, next_action)
    """
    try:
        from app.agent.lm_client import LLMClient

        properties = facts.get("properties") or []
        appointments = facts.get("appointments") or []

        property_lines = "\n".join(
            f"- {p.get('title', 'Property')} ({p.get('location', 'Unknown')}) - "
            f"{_format_aed(p.get('price'))} - "
            f"{'Interested' if p.get('interested') else 'Not interested'}"
            for p in properties[:3]
        ) or "None"
        appointment_lines = "\n".join(
            f"- {a.get('property_title', 'Property')} on "
            f"{a.get('datetime', 'Unknown')} ({a.get('status', 'UNKNOWN')})"
            for a in appointments[:3]
        ) or "None"

        # Build prompt for LLM
        prompt = f"""Analyze this real estate lead and provide:
1. Customer interest summary (2-3 sentences, what they're looking for)
2. Recommended next action (1 sentence, what sales should do)

Lead Facts:
- Status: {facts.get('lead_status', 'UNKNOWN')}
- Looking for: {facts.get('listing_type') or 'property'} in {facts.get('location') or 'any location'}
- Budget: {_format_budget(facts)}
- Bedrooms: {facts.get('bedrooms') or 'flexible'}
- Property type: {facts.get('property_type') or 'any'}
- Timeline: {facts.get('timeline') or 'not specified'}
- Properties shown: {facts.get('properties_count', len(properties))}
- Appointments: {facts.get('appointments_count', len(appointments))}
- Messages exchanged: {facts.get('messages_count', 0)}
- Last activity: {facts.get('last_activity') or 'unknown'}

Properties shown:
{property_lines}

Appointments:
{appointment_lines}

Respond in this format:
INTEREST: <2-3 sentence summary>
ACTION: <1 sentence recommendation>"""

        # Call LLM with timeout
        if db is None:
            raise ValueError("Database session required to initialize the LLM client")

        client = LLMClient(db)
        response = client.generate(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a real estate sales assistant analyzing lead quality. "
                        "Be concise and actionable."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=200,
            temperature=0.3,
        )

        content = response.get("content", "") if isinstance(response, dict) else (response or "")

        # Parse response
        interest = "Customer shows interest in properties."
        action = "Follow up to understand requirements better."

        if content and "INTEREST:" in content:
            parts = content.split("ACTION:")
            if len(parts) == 2:
                interest = parts[0].replace("INTEREST:", "").strip()
                action = parts[1].strip()
            else:
                interest = content.replace("INTEREST:", "").strip()

        return interest, action

    except Exception as e:
        logger.warning(f"LLM generation failed, using fallback: {e}")

        # Fallback logic based on facts
        appointments_count = facts.get("appointments_count") or len(facts.get("appointments") or [])
        properties_count = facts.get("properties_count") or len(facts.get("properties") or [])
        listing_type = facts.get("listing_type") or "property"
        location = facts.get("location") or "various locations"
        bedrooms = facts.get("bedrooms") or "flexible"
        budget_text = _format_budget(facts)

        if appointments_count > 0:
            interest = f"Customer has booked {appointments_count} viewing(s) for {listing_type} properties in {location}."
            action = "Follow up after viewing to gather feedback and show additional properties."
        elif properties_count > 0:
            interest = f"Customer reviewed {properties_count} {listing_type} properties in {location} with budget {budget_text}."
            action = "Contact customer to schedule property viewings based on their preferences."
        else:
            interest = (
                f"Based on conversation, customer is looking for {listing_type} "
                f"in {location} with {bedrooms} bedrooms."
            )
            action = "Share curated property listings matching their requirements and follow up."

        return interest, action


def build_summary(db: Session, lead: Lead) -> str:
    """
    Build complete lead summary with AI analysis.

    Combines facts + AI-generated insights into plain text summary.

    Args:
        db: Database session
        lead: Lead to summarize

    Returns:
        str: ASCII plain text summary
    """
    try:
        # Step 1: Extract facts (NO PII)
        facts = build_facts(db, lead)

        # Step 2: Generate AI fields
        customer_interest, next_action = generate_ai_fields(facts, db)

        # Step 3: Build summary text (NOW we can include PII from lead object)
        customer = lead.customer
        customer_name = customer.name if customer else "Unknown"
        customer_email = customer.email if customer else "N/A"
        customer_phone = customer.phone if customer else "N/A"

        summary_lines = [
            "=" * 70,
            f"LEAD SUMMARY - #{facts['lead_id']}",
            "=" * 70,
            "",
            "CUSTOMER INFORMATION",
            "-" * 70,
            f"Name:    {customer_name}",
            f"Email:   {customer_email}",
            f"Phone:   {customer_phone}",
            f"Status:  {facts['lead_status']}",
            f"Source:  {facts['lead_source']}",
            f"Created: {facts['created_at']}",
            "",
            "REQUIREMENTS",
            "-" * 70,
            f"Type:      {facts['listing_type'] or 'Any'}",
            f"Location:  {facts['location'] or 'Any'}",
            f"Budget:    AED {facts['budget_min']:,.0f} - {facts['budget_max']:,.0f}" if facts['budget_min'] and facts['budget_max'] else "Budget:    Not specified",
            f"Bedrooms:  {facts['bedrooms'] or 'Flexible'}",
            f"Property:  {facts['property_type'] or 'Any type'}",
            f"Timeline:  {facts['timeline'] or 'Not specified'}",
            "",
            "ENGAGEMENT",
            "-" * 70,
            f"Properties shown:  {facts['properties_count']}",
            f"Appointments:      {facts['appointments_count']}",
            f"Messages:          {facts['messages_count']}",
            f"Last activity:     {facts['last_activity'] or 'Unknown'}",
            "",
        ]

        # Add properties if any
        if facts["properties"]:
            summary_lines.append("PROPERTIES SHOWN")
            summary_lines.append("-" * 70)
            for i, prop in enumerate(facts["properties"][:5], 1):
                interest_flag = "✓ INTERESTED" if prop["interested"] else "○ Shown"
                summary_lines.append(
                    f"{i}. {prop['title']}"
                )
                summary_lines.append(
                    f"   {prop['location']} - AED {prop['price']:,.0f} - {prop['bedrooms']}BR - {interest_flag}"
                )
            summary_lines.append("")

        # Add appointments if any
        if facts["appointments"]:
            summary_lines.append("APPOINTMENTS")
            summary_lines.append("-" * 70)
            for i, apt in enumerate(facts["appointments"][:3], 1):
                summary_lines.append(
                    f"{i}. {apt['property_title']} - {apt['datetime']} ({apt['status']})"
                )
            summary_lines.append("")

        # Add AI analysis
        summary_lines.extend([
            "AI ANALYSIS",
            "-" * 70,
            f"Interest: {customer_interest}",
            "",
            f"Next Action: {next_action}",
            "",
            "=" * 70,
        ])

        return "\n".join(summary_lines)

    except Exception as e:
        logger.error(f"Failed to build summary for lead {lead.id}: {e}", exc_info=True)

        # Minimal fallback summary
        customer = lead.customer
        requirements = {}
        if getattr(lead, "requirements", None):
            try:
                requirements = json.loads(lead.requirements)
            except Exception:
                requirements = {}

        listing_type = requirements.get("listing_type") or "property"
        location = requirements.get("location") or "Dubai"

        return f"""
==================================================
LEAD SUMMARY - #{lead.id}
==================================================

Customer: {customer.name if customer else 'Unknown'}
Status: {lead.status.value.upper() if lead.status else 'UNKNOWN'}
Created: {lead.created_at.strftime('%Y-%m-%d') if lead.created_at else 'Unknown'}

Looking for: {listing_type} in {location}

ERROR: Failed to generate detailed summary.
==================================================
"""


def save_summary(db: Session, lead: Lead, summary_text: str) -> bool:
    """
    Save summary to lead.summary column.

    Args:
        db: Database session
        lead: Lead to update
        summary_text: Summary text to save

    Returns:
        bool: True if saved successfully
    """
    try:
        lead.summary = summary_text
        db.commit()
        logger.info(f"Summary saved for lead {lead.id} ({len(summary_text)} chars)")
        return True

    except Exception as e:
        db.rollback()
        logger.error(f"Failed to save summary for lead {lead.id}: {e}")
        return False
