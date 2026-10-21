"""Thin wrapper around the Google Slides REST API."""

from __future__ import annotations

import logging
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .auth import SETUP_HINT, get_credentials

log = logging.getLogger(__name__)

MAX_REQUESTS_PER_BATCH = 450  # API limit is 500 per batchUpdate; keep headroom.


class SlidesError(RuntimeError):
    pass


class SlidesClient:
    def __init__(self, credentials=None):
        self.creds = credentials or get_credentials()
        self.svc = build("slides", "v1", credentials=self.creds, cache_discovery=False)
        self.drive = build("drive", "v3", credentials=self.creds, cache_discovery=False)

    def create(self, title: str) -> dict:
        try:
            return self.svc.presentations().create(body={"title": title}).execute()
        except HttpError as exc:
            raise SlidesError(_explain(exc)) from exc

    def get(self, presentation_id: str) -> dict:
        try:
            return self.svc.presentations().get(presentationId=presentation_id).execute()
        except HttpError as exc:
            raise SlidesError(_explain(exc)) from exc

    def batch_update(self, presentation_id: str, requests: list[dict]) -> list[dict]:
        replies: list[dict] = []
        for i in range(0, len(requests), MAX_REQUESTS_PER_BATCH):
            chunk = requests[i : i + MAX_REQUESTS_PER_BATCH]
            try:
                resp = self.svc.presentations().batchUpdate(
                    presentationId=presentation_id, body={"requests": chunk}
                ).execute()
            except HttpError as exc:
                raise SlidesError(_explain(exc, chunk, offset=i)) from exc
            replies.extend(resp.get("replies", []))
        return replies

    def thumbnail_url(self, presentation_id: str, page_id: str) -> str | None:
        try:
            resp = self.svc.presentations().pages().getThumbnail(
                presentationId=presentation_id,
                pageObjectId=page_id,
                thumbnailProperties_mimeType="PNG",
                thumbnailProperties_thumbnailSize="LARGE",
            ).execute()
            return resp.get("contentUrl")
        except HttpError as exc:  # thumbnails are optional
            log.warning("thumbnail failed: %s", exc)
            return None

    def share(self, file_id: str, emails: list[str], role: str = "writer") -> list[str]:
        shared = []
        for email in emails:
            try:
                self.drive.permissions().create(
                    fileId=file_id, sendNotificationEmail=False,
                    body={"type": "user", "role": role, "emailAddress": email},
                ).execute()
                shared.append(email)
            except HttpError as exc:
                log.warning("share with %s failed: %s", email, exc)
        return shared


def url_for(presentation_id: str, slide_id: str | None = None) -> str:
    url = f"https://docs.google.com/presentation/d/{presentation_id}/edit"
    return f"{url}#slide=id.{slide_id}" if slide_id else url


def _explain(exc: HttpError, requests: list[dict] | None = None, offset: int = 0) -> str:
    status = exc.resp.status if exc.resp else "?"
    msg = str(exc)
    try:
        import json

        payload = json.loads(exc.content.decode())
        msg = payload.get("error", {}).get("message", msg)
    except Exception:  # noqa: BLE001
        pass
    if status in (401, 403):
        return f"Slides API authorization failed ({status}): {msg}\n{SETUP_HINT}\nAlso make sure the Slides API is enabled: gcloud services enable slides.googleapis.com drive.googleapis.com"
    hint = ""
    # The API reports the failing request index: "Invalid requests[12].createImage: ..."
    if requests:
        import re

        m = re.search(r"requests\[(\d+)\]", msg)
        if m:
            idx = int(m.group(1))
            if 0 <= idx < len(requests):
                hint = f"\nFailing request #{idx + offset}: {json.dumps(requests[idx])[:600]}"
    return f"Slides API error ({status}): {msg}{hint}"
