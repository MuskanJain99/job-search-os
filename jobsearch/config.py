"""Runtime settings, read from environment variables (GitHub Actions secrets in production).

Nothing personal lives in the repository: profile answers, evidence and contacts are read
from the database at run time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    # Storage
    database_url: str = field(default_factory=lambda: _str("DATABASE_URL"))

    # LLM (provider-agnostic)
    llm_provider: str = field(default_factory=lambda: _str("LLM_PROVIDER", "openai_compatible"))  # openai_compatible (default, free Gemini) | openai | anthropic
    llm_model: str = field(default_factory=lambda: _str("LLM_MODEL"))
    llm_model_fast: str = field(default_factory=lambda: _str("LLM_MODEL_FAST"))
    llm_base_url: str = field(default_factory=lambda: _str("LLM_BASE_URL"))  # for openai_compatible (OpenRouter, Groq, Ollama…)
    llm_api_key: str = field(default_factory=lambda: _str("LLM_API_KEY"))  # one key, whichever provider
    llm_min_interval_s: float = field(default_factory=lambda: float(_str("LLM_MIN_INTERVAL_S", "0") or 0))  # throttle for free-tier rate limits

    # Optional integrations
    firecrawl_api_key: str = field(default_factory=lambda: _str("FIRECRAWL_API_KEY"))
    notion_token: str = field(default_factory=lambda: _str("NOTION_TOKEN"))
    notion_applications_db: str = field(default_factory=lambda: _str("NOTION_APPLICATIONS_DB"))
    notion_contacts_db: str = field(default_factory=lambda: _str("NOTION_CONTACTS_DB"))
    google_client_id: str = field(default_factory=lambda: _str("GOOGLE_CLIENT_ID"))
    google_client_secret: str = field(default_factory=lambda: _str("GOOGLE_CLIENT_SECRET"))
    google_refresh_token: str = field(default_factory=lambda: _str("GOOGLE_REFRESH_TOKEN"))
    owner_email: str = field(default_factory=lambda: _str("OWNER_EMAIL"))
    ntfy_topic: str = field(default_factory=lambda: _str("NTFY_TOPIC"))  # optional phone push via ntfy.sh
    twilio_sid: str = field(default_factory=lambda: _str("TWILIO_ACCOUNT_SID"))  # optional SMS for urgent alerts
    twilio_token: str = field(default_factory=lambda: _str("TWILIO_AUTH_TOKEN"))
    twilio_from: str = field(default_factory=lambda: _str("TWILIO_FROM"))
    owner_phone: str = field(default_factory=lambda: _str("OWNER_PHONE"))  # E.164, e.g. +91XXXXXXXXXX
    # How long to wait for Muskan on ANY human step (CAPTCHA, code, sign-in, question) before moving on.
    captcha_wait_s: int = field(default_factory=lambda: int(_str("HUMAN_WAIT_SECONDS", "120") or 120))
    timezone: str = field(default_factory=lambda: _str("OWNER_TIMEZONE", "Asia/Kolkata"))

    # Safety switches. Everything outbound is OFF until explicitly enabled.
    dry_run: bool = field(default_factory=lambda: _bool("DRY_RUN", True))
    submit_enabled: bool = field(default_factory=lambda: _bool("SUBMIT_ENABLED", False))
    email_enabled: bool = field(default_factory=lambda: _bool("EMAIL_ENABLED", False))

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.google_refresh_token)

    @property
    def notion_configured(self) -> bool:
        return bool(self.notion_token and self.notion_applications_db)

    def can_write_external(self) -> bool:
        """External side effects (emails, submissions, Notion, Calendar) only when not a dry run."""
        return not self.dry_run


settings = Settings()
