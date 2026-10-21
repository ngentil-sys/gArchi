"""Resolve icon ids to URLs that the Slides API can fetch.

The Slides API only accepts publicly fetchable PNG/JPEG/GIF URLs (no auth, no
SVG), so the official icons are mirrored to a bucket (or any static host) by
`scripts/setup_icons.py`. Supported hosting modes (`ICON_HOST`):

* ``gcs`` - objects at ``gs://ICON_BUCKET/ICON_PREFIX<icon>.png``. If
  ``ICON_SIGNER_SA`` is set a V4 signed URL is minted through IAM signBlob
  (works for private buckets); otherwise the public object URL is used.
* ``url`` - ``ICON_BASE_URL/<icon>.png`` on any public static host.
* ``none`` - draw cards without icons.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from functools import lru_cache
from pathlib import Path

from ..config import ICONS_DIR, Settings

log = logging.getLogger(__name__)

MANIFEST_PATH = ICONS_DIR / "manifest.json"


@lru_cache(maxsize=1)
def local_manifest() -> dict:
    """Manifest written by setup_icons.py: {"icons": {id: {"file":..., "source":...}}}."""
    if MANIFEST_PATH.exists():
        with MANIFEST_PATH.open() as f:
            return json.load(f)
    return {"icons": {}}


def known_icon_ids() -> set[str]:
    return set(local_manifest().get("icons", {}).keys())


class IconResolver:
    def __init__(self, settings: Settings, credentials=None):
        self.settings = settings
        self._credentials = credentials
        self._cache: dict[str, str | None] = {}
        self._bucket = None

    @property
    def enabled(self) -> bool:
        return self.settings.icons_enabled

    def has_icon(self, icon_id: str) -> bool:
        ids = known_icon_ids()
        return icon_id in ids if ids else True

    def url_for(self, icon_id: str | None) -> str | None:
        if not icon_id or not self.enabled or not self.has_icon(icon_id):
            return None
        if icon_id in self._cache:
            return self._cache[icon_id]
        try:
            url = self._resolve(icon_id)
        except Exception as exc:  # noqa: BLE001 - never fail the whole deck on an icon
            log.warning("icon %s could not be resolved: %s", icon_id, exc)
            url = None
        self._cache[icon_id] = url
        return url

    # -- internals -----------------------------------------------------------

    def _object_name(self, icon_id: str) -> str:
        return f"{self.settings.icon_prefix}{icon_id}.png"

    def _resolve(self, icon_id: str) -> str | None:
        s = self.settings
        if s.icon_host == "url":
            return f"{s.icon_base_url.rstrip('/')}/{icon_id}.png"
        if s.icon_host == "gcs":
            if s.icon_signer_sa:
                return self._signed_url(icon_id)
            return f"https://storage.googleapis.com/{s.icon_bucket}/{self._object_name(icon_id)}"
        return None

    def _signed_url(self, icon_id: str) -> str:
        from google.auth import impersonated_credentials
        from google.cloud import storage

        s = self.settings
        if self._credentials is None:
            from ..gapi.auth import get_credentials

            self._credentials = get_credentials()
        if self._bucket is None:
            client = storage.Client(project=s.project, credentials=self._credentials)
            self._bucket = client.bucket(s.icon_bucket)
        signer = impersonated_credentials.Credentials(
            source_credentials=self._credentials,
            target_principal=s.icon_signer_sa,
            target_scopes=["https://www.googleapis.com/auth/devstorage.read_only"],
            lifetime=300,
        )
        blob = self._bucket.blob(self._object_name(icon_id))
        return blob.generate_signed_url(
            version="v4",
            expiration=dt.timedelta(seconds=s.icon_url_ttl_seconds),
            method="GET",
            credentials=signer,
        )


def local_icon_path(icon_id: str) -> Path | None:
    entry = local_manifest().get("icons", {}).get(icon_id)
    if not entry:
        return None
    p = ICONS_DIR / entry["file"]
    return p if p.exists() else None
