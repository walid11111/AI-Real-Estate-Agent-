"""
Tests for AI-powered lead summary generation with PII protection.

Phase 6: Email Outbox + Summary + Handoff
"""

import json
from unittest.mock import patch

from app.models.customer import Customer
from app.models.lead import Lead, LeadStatus
from app.services.summary import (
    build_facts,
    build_summary,
    generate_ai_fields,
    save_summary,
)


def test_build_facts_excludes_pii(db_session):
    """Test that facts dict NEVER includes PII"""
    customer = Customer(
        name="John Doe",  # PII
        email="john@example.com",  # PII
        phone="+971501234567",  # PII
        whatsapp_id="971501234567",  # PII
    )
    db_session.add(customer)
    db_session.commit()

    # Create lead
    lead = Lead(
        customer_id=customer.id,
        status=LeadStatus.HOT,
        requirements=json.dumps({
            "listing_type": "rent",
            "location": "Dubai Marina",
            "budget_min": 80000,
            "budget_max": 120000,
            "bedrooms": 2,
        }),
    )
    db_session.add(lead)
    db_session.commit()

    # Build facts
    facts = build_facts(db_session, lead)

    # Assert PII is NOT in facts
    facts_str = str(facts).lower()
    assert "john" not in facts_str
    assert "doe" not in facts_str
    assert "john@example.com" not in facts_str
    assert "+971501234567" not in facts_str
    assert "971501234567" not in facts_str

    # Assert business data IS in facts
    assert facts["location"] == "Dubai Marina"
    assert facts["budget_min"] == 80000
    assert facts["bedrooms"] == 2
    assert facts["listing_type"] == "rent"


def test_build_facts_includes_business_data(db_session):
    """Test that facts include all necessary business data"""
    customer = Customer(name="Test", email="test@example.com", phone="+971501234567")
    db_session.add(customer)
    db_session.commit()

    lead = Lead(
        customer_id=customer.id,
        status=LeadStatus.QUALIFIED,
        requirements=json.dumps({
            "listing_type": "buy",
            "location": "Downtown Dubai",
            "budget_min": 1000000,
            "budget_max": 1500000,
            "bedrooms": 3,
            "property_type": "apartment",
        }),
        expected_timeline="1-3_months",
    )
    db_session.add(lead)
    db_session.commit()

    facts = build_facts(db_session, lead)

    # Check all business data is included
    assert facts["lead_id"] == lead.id
    assert facts["lead_status"] == "QUALIFIED"
    assert facts["listing_type"] == "buy"
    assert facts["location"] == "Downtown Dubai"
    assert facts["budget_min"] == 1000000
    assert facts["budget_max"] == 1500000
    assert facts["bedrooms"] == 3
    assert facts["property_type"] == "apartment"
    assert facts["timeline"] == "1-3_months"


def test_generate_ai_fields_returns_interest_and_action(db_session):
    """Test that AI generation returns interest and next action"""
    facts = {
        "lead_status": "HOT",
        "listing_type": "buy",
        "location": "Dubai Marina",
        "budget_min": 1000000,
        "budget_max": 1500000,
    }

    # Mock is already in place from conftest.py
    interest, action = generate_ai_fields(facts, db_session)

    assert isinstance(interest, str)
    assert isinstance(action, str)
    assert len(interest) > 0
    assert len(action) > 0


def test_generate_ai_fields_fallback_on_error(db_session):
    """Test that AI generation falls back gracefully on error"""
    facts = {"lead_status": "NEW"}

    # Mock LLM to raise exception
    with patch("app.agent.lm_client.LLMClient") as mock_client:
        mock_client.return_value.generate.side_effect = Exception("LLM timeout")

        interest, action = generate_ai_fields(facts, db_session)

        # Should return fallback values
        assert "based on conversation" in interest.lower()
        assert "follow up" in action.lower() or "contact" in action.lower()


def test_build_summary_includes_pii_in_final_output(db_session):
    """Test that final summary includes PII for sales team"""
    customer = Customer(
        name="Jane Smith",
        email="jane@example.com",
        phone="+971501234567",
    )
    db_session.add(customer)
    db_session.commit()

    lead = Lead(
        customer_id=customer.id,
        status=LeadStatus.HOT,
        requirements=json.dumps({
            "listing_type": "buy",
            "location": "Palm Jumeirah",
            "budget": 2000000,
        }),
    )
    db_session.add(lead)
    db_session.commit()

    summary = build_summary(db_session, lead)

    # Summary should include PII (it's for sales team, not external LLM)
    assert "Jane Smith" in summary or customer.name in summary
    assert "+971501234567" in summary or customer.phone in summary
    # Email might be optional
    assert isinstance(summary, str)
    assert len(summary) > 100  # Should be detailed


def test_build_summary_fallback_on_error(db_session):
    """Test that summary generation falls back on total failure"""
    customer = Customer(name="Test", email="test@example.com", phone="+971501234567")
    db_session.add(customer)
    db_session.commit()

    lead = Lead(
        customer_id=customer.id,
        status=LeadStatus.NEW,
    )
    db_session.add(lead)
    db_session.commit()

    # Mock LLM to fail
    with patch("app.agent.lm_client.LLMClient") as mock_client:
        mock_client.return_value.generate.side_effect = Exception("LLM error")

        summary = build_summary(db_session, lead)

        # Should still return a summary (fallback)
        assert isinstance(summary, str)
        assert len(summary) > 50
        assert "Test" in summary  # Customer name should be there


def test_save_summary_persists_to_database(db_session):
    """Test that summary is saved to leads.summary column"""
    customer = Customer(name="Test", email="test@example.com", phone="+971501234567")
    db_session.add(customer)
    db_session.commit()

    lead = Lead(customer_id=customer.id, status=LeadStatus.WARM)
    db_session.add(lead)
    db_session.commit()

    summary_text = "This is a test summary for lead"
    save_summary(db_session, lead, summary_text)

    # Refresh from database
    db_session.refresh(lead)

    assert lead.summary == summary_text


def test_summary_format_is_ascii_text(db_session):
    """Test that summary is plain ASCII text, not HTML"""
    customer = Customer(name="Test", email="test@example.com", phone="+971501234567")
    db_session.add(customer)
    db_session.commit()

    lead = Lead(
        customer_id=customer.id,
        status=LeadStatus.HOT,
        requirements=json.dumps({"listing_type": "buy", "location": "Dubai"}),
    )
    db_session.add(lead)
    db_session.commit()

    summary = build_summary(db_session, lead)

    # Should not contain HTML tags
    assert "<html>" not in summary.lower()
    assert "<body>" not in summary.lower()
    assert "<p>" not in summary.lower()
    assert "<div>" not in summary.lower()

    # Should be plain text with separators
    assert "=" in summary or "-" in summary or "#" in summary


def test_facts_never_sent_to_external_llm(db_session):
    """Test that PII is never included in data sent to LLM"""
    from unittest.mock import MagicMock

    customer = Customer(
        name="Secret Name",
        email="secret@example.com",
        phone="+971501234567",
    )
    db_session.add(customer)
    db_session.commit()

    lead = Lead(
        customer_id=customer.id,
        status=LeadStatus.HOT,
        requirements=json.dumps({"listing_type": "buy"}),
    )
    db_session.add(lead)
    db_session.commit()

    # Mock LLM and capture what's sent to it
    with patch("app.agent.lm_client.LLMClient") as mock_client:
        mock_instance = MagicMock()
        mock_instance.generate.return_value = "INTEREST: Test\nACTION: Test"
        mock_client.return_value = mock_instance

        build_summary(db_session, lead)

        # Check that LLM was called
        assert mock_instance.generate.called

        # Get the prompt sent to LLM
        call_args = mock_instance.generate.call_args
        prompt = str(call_args)

        # PII must NOT be in prompt
        assert "Secret Name" not in prompt
        assert "secret@example.com" not in prompt
        assert "+971501234567" not in prompt
