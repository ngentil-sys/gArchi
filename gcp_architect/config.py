"""Runtime configuration for the GCP Architect agent.

All settings come from environment variables (a `.env` file next to this
package is loaded automatically by `adk web` and by `load_settings()`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
ASSETS_DIR = PROJECT_DIR / "assets"
ICONS_DIR = ASSETS_DIR / "icons"

# Scopes needed by the agent when using Application Default Credentials.
SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/drive.file",
]


@dataclass
class Settings:
    project: str | None
    location: str
    architect_model: str
    researcher_model: str
    icon_host: str  # "gcs" | "url" | "none"
    icon_bucket: str | None
    icon_prefix: str
    icon_base_url: str | None
    icon_signer_sa: str | None
    icon_url_ttl_seconds: int
    share_with: list[str] = field(default_factory=list)

    @property
    def icons_enabled(self) -> bool:
        if self.icon_host == "gcs":
            return bool(self.icon_bucket)
        if self.icon_host == "url":
            return bool(self.icon_base_url)
        return False


def load_settings() -> Settings:
    load_dotenv(PACKAGE_DIR / ".env", override=False)
    load_dotenv(PROJECT_DIR / ".env", override=False)
    share = [s.strip() for s in os.getenv("SHARE_DECKS_WITH", "").split(",") if s.strip()]
    return Settings(
        project=os.getenv("GOOGLE_CLOUD_PROJECT"),
        location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        architect_model=os.getenv("ARCHITECT_MODEL", "gemini-2.5-pro"),
        researcher_model=os.getenv("RESEARCHER_MODEL", "gemini-2.5-flash"),
        icon_host=os.getenv("ICON_HOST", "gcs").lower(),
        icon_bucket=os.getenv("ICON_BUCKET"),
        icon_prefix=os.getenv("ICON_PREFIX", "icons/"),
        icon_base_url=os.getenv("ICON_BASE_URL"),
        icon_signer_sa=os.getenv("ICON_SIGNER_SA"),
        icon_url_ttl_seconds=int(os.getenv("ICON_URL_TTL_SECONDS", "3600")),
        share_with=share,
    )
