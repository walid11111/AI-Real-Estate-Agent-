from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Get the backend directory path (3 levels up from config.py)
# config.py location: backend/app/core/config.py
# We want: backend/.env
BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE = BASE_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=True,
        env_prefix="",
    )

    # Application
    APP_NAME: str = "AI Real Estate Agent"
    DEBUG: bool = False

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000"

    # Database
    DATABASE_URL: str = "postgresql://realestate:realestate@localhost:5432/realestate"

    # Phase 4: AI Provider Configuration
    AI_PROVIDER: str = "gemini"  # Default provider: gemini, groq, or openai

    # Phase 4: AI Provider API Keys (optional - can be set in admin settings too)
    GROQ_API_KEY: str | None = None
    GEMINI_API_KEY: str | None = None
    OPENAI_API_KEY: str | None = None

    # Phase 4: Encryption key for storing sensitive settings
    ENCRYPTION_KEY: str | None = None

    # Phase 5: Google Calendar Integration
    GOOGLE_SERVICE_ACCOUNT_FILE: str | None = None  # Path to service account JSON key file
    GOOGLE_CALENDAR_ID: str | None = None  # Sales agent's calendar ID (email address)
    GOOGLE_CALENDAR_TIMEZONE: str = "Asia/Dubai"  # Default timezone for appointments
    GOOGLE_CALENDAR_RETRY_MAX: int = 3  # Max retries for failed sync
    GOOGLE_CALENDAR_RETRY_DELAY: int = 300  # Seconds between retries (5 minutes)

    # Phase 6: Email System (Resend API)
    RESEND_API_KEY: str | None = None  # Resend API key (secret, never log)
    EMAIL_FROM: str = "onboarding@resend.dev"  # Verified sender address
    SALES_TEAM_EMAILS: str = ""  # Comma-separated list of sales team emails
    EMAIL_TEST_MODE: bool = True  # Redirect customer emails to test recipients
    EMAIL_TEST_RECIPIENTS: str = ""  # Comma-separated test emails (for EMAIL_TEST_MODE=true)
    EMAIL_WORKER_ENABLED: bool = True  # Enable background email worker
    EMAIL_WORKER_INTERVAL_SECONDS: int = 10  # How often worker checks for pending emails

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]

    @property
    def sales_team_list(self) -> list[str]:
        """Parse SALES_TEAM_EMAILS into list of email addresses"""
        return [e.strip() for e in self.SALES_TEAM_EMAILS.split(",") if e.strip()]

    @property
    def email_test_recipients_list(self) -> list[str]:
        """Parse EMAIL_TEST_RECIPIENTS into list of email addresses"""
        return [e.strip() for e in self.EMAIL_TEST_RECIPIENTS.split(",") if e.strip()]


settings = Settings()
