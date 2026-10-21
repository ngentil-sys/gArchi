"""High-level entry point: render an ArchitectureSpec into a Google Slides deck."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

from ..config import load_settings
from ..gapi.slides import SlidesClient, url_for
from .icons import IconResolver
from .slides_builder import DeckBuilder
from .spec import ArchitectureSpec

log = logging.getLogger(__name__)


@dataclass
class RenderResult:
    presentation_id: str
    url: str
    diagram_url: str
    diagram_slide_id: str
    slide_count: int
    thumbnail_url: str | None
    warnings: list[str] = field(default_factory=list)
    icon_misses: list[str] = field(default_factory=list)
    shared_with: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def render_deck(spec: ArchitectureSpec, presentation_id: str | None = None, client: SlidesClient | None = None) -> RenderResult:
    """Create a new deck (or rebuild an existing one in place) from the spec."""
    settings = load_settings()
    client = client or SlidesClient()
    icons = IconResolver(settings, credentials=client.creds)

    plan = DeckBuilder(spec, icons).build()
    warnings = list(plan.warnings)
    if not icons.enabled:
        warnings.append("Icon hosting is not configured (ICON_HOST/ICON_BUCKET); cards were drawn without official icons. "
                        "Run scripts/setup_icons.py.")
    elif plan.icon_misses:
        warnings.append(f"Icons not available on the icon host: {plan.icon_misses}")

    if presentation_id:
        pres = client.get(presentation_id)
        old_slides = [s["objectId"] for s in pres.get("slides", [])]
        requests = plan.requests + [{"deleteObject": {"objectId": sid}} for sid in old_slides]
        if pres.get("title") != spec.title:
            try:
                client.drive.files().update(fileId=presentation_id, body={"name": spec.title}).execute()
            except Exception as exc:  # noqa: BLE001 - cosmetic
                log.warning("could not rename presentation: %s", exc)
    else:
        pres = client.create(spec.title)
        presentation_id = pres["presentationId"]
        default_slides = [s["objectId"] for s in pres.get("slides", [])]
        requests = plan.requests + [{"deleteObject": {"objectId": sid}} for sid in default_slides]

    client.batch_update(presentation_id, requests)

    shared: list[str] = []
    if settings.share_with:
        shared = client.share(presentation_id, settings.share_with)

    thumb = client.thumbnail_url(presentation_id, plan.diagram_slide_id)
    return RenderResult(
        presentation_id=presentation_id,
        url=url_for(presentation_id),
        diagram_url=url_for(presentation_id, plan.diagram_slide_id),
        diagram_slide_id=plan.diagram_slide_id,
        slide_count=len(plan.slide_ids),
        thumbnail_url=thumb,
        warnings=warnings,
        icon_misses=plan.icon_misses,
        shared_with=shared,
    )
