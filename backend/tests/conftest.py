"""
Test configuration and fixtures.

Phase 6: Disable email worker and mock email provider for tests.
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Phase 6: Disable email worker before any imports
os.environ["EMAIL_WORKER_ENABLED"] = "false"
os.environ["EMAIL_TEST_MODE"] = "true"
os.environ["RESEND_API_KEY"] = "test_key_for_testing"
os.environ["SALES_TEAM_EMAILS"] = "sales@softcr8ors.test"
os.environ["EMAIL_TEST_RECIPIENTS"] = "test-recipient@softcr8ors.test"

from app.core.database import Base

# ============================================================================
# DATABASE FIXTURES
# ============================================================================

@pytest.fixture(scope="function")
def db_session():
    """
    Create a fresh in-memory database for each test.

    Yields:
        Session: SQLAlchemy database session
    """
    # Create in-memory SQLite database
    engine = create_engine("sqlite:///:memory:", echo=False)

    # Create all tables
    Base.metadata.create_all(engine)

    # Create session
    session_local = sessionmaker(bind=engine)
    session = session_local()

    yield session

    # Cleanup
    session.close()
    Base.metadata.drop_all(engine)


# ============================================================================
# PHASE 6: EMAIL MOCKING
# ============================================================================

@pytest.fixture(autouse=True)
def mock_email_provider():
    """
    Mock email provider globally for all tests.

    Returns fake message_id, never makes network calls.
    """
    # notifications.py imports send_email directly, so patch it at the
    # point of use rather than the original module.
    with patch("app.services.notifications.send_email") as mock_send:
        # Return fake message ID
        mock_send.return_value = "test_message_id_123"
        yield mock_send


@pytest.fixture(autouse=True)
def mock_lm_client():
    """
    Mock LLM client for summary generation.

    Prevents network calls to AI providers during tests.
    """
    with patch("app.agent.lm_client.LLMClient") as mock_client:
        mock_instance = MagicMock()
        mock_instance.generate.return_value = "INTEREST: Test interest summary\nACTION: Test action"
        mock_client.return_value = mock_instance
        yield mock_client
