"""Credentials for Google APIs (Application Default Credentials with Workspace scopes)."""

from __future__ import annotations

import functools

import google.auth
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request

from ..config import SCOPES, load_settings


class AuthError(RuntimeError):
    pass


SETUP_HINT = (
    "Run `scripts/setup.sh` or:\n"
    "  gcloud auth application-default login --scopes=" + ",".join(SCOPES) + "\n"
    "  gcloud auth application-default set-quota-project $GOOGLE_CLOUD_PROJECT"
)


@functools.lru_cache(maxsize=1)
def get_credentials() -> Credentials:
    settings = load_settings()
    try:
        creds, _ = google.auth.default(scopes=SCOPES, quota_project_id=settings.project)
    except google.auth.exceptions.DefaultCredentialsError as exc:
        raise AuthError(f"No Application Default Credentials found. {SETUP_HINT}") from exc
    if not creds.valid:
        creds.refresh(Request())
    return creds


def credential_scopes_ok() -> tuple[bool, str]:
    """Best-effort check that the ADC token carries the Slides scope."""
    creds = get_credentials()
    scopes = set(getattr(creds, "scopes", None) or getattr(creds, "granted_scopes", None) or [])
    if not scopes:
        return True, "scopes unknown (non-user credentials); will be validated on first Slides call"
    missing = [s for s in SCOPES if s not in scopes]
    if missing:
        return False, f"ADC token is missing scopes {missing}. {SETUP_HINT}"
    return True, "ok"
