"""Translate an ArchitectureSpec + Layout into Google Slides API batchUpdate requests.

Every element is a native Slides object (shapes, bound connectors, images,
tables), so the resulting deck is fully editable by hand afterwards.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any

from .. import catalog_data
from .icons import IconResolver
from .layout import AREA, PAGE_H, PAGE_W, Layout, Rect, build_layout
from .routing import label_rect, route
from .spec import ArchitectureSpec, Component, Node, Reference

FONT = "Google Sans"
MONO = "Google Sans Mono"

# Google Cloud palette
BLUE, RED, YELLOW, GREEN = "#4285F4", "#EA4335", "#FBBC04", "#34A853"
INK, INK_SOFT, INK_MUTED = "#202124", "#3C4043", "#5F6368"
LINE_GRAY, HAIRLINE, PAPER = "#80868B", "#DADCE0", "#F8F9FA"

ZONE_STYLE: dict[str, dict[str, Any]] = {
    "cloud": {"fill": "#FFFFFF", "stroke": BLUE, "label": "#1A73E8", "weight": 1.5, "dash": "SOLID"},
    "project": {"fill": PAPER, "stroke": HAIRLINE, "label": INK_MUTED, "weight": 1, "dash": "SOLID"},
    "region": {"fill": "#FFFFFF", "stroke": "#BDC1C6", "label": INK_MUTED, "weight": 1, "dash": "DASH"},
    "network": {"fill": "#E8F0FE", "stroke": BLUE, "label": "#1967D2", "weight": 1, "dash": "SOLID"},
    "data": {"fill": "#E6F4EA", "stroke": GREEN, "label": "#137333", "weight": 1, "dash": "SOLID"},
    "security": {"fill": "#FCE8E6", "stroke": RED, "label": "#C5221F", "weight": 1, "dash": "SOLID"},
    "operations": {"fill": "#FEF7E0", "stroke": "#F9AB00", "label": "#B06000", "weight": 1, "dash": "SOLID"},
    "external": {"fill": "#FFFFFF", "stroke": "#9AA0A6", "label": INK_MUTED, "weight": 1, "dash": "DASH"},
    "onprem": {"fill": "#F1F3F4", "stroke": LINE_GRAY, "label": INK_SOFT, "weight": 1, "dash": "DASH"},
    "generic": {"fill": PAPER, "stroke": HAIRLINE, "label": INK_MUTED, "weight": 1, "dash": "SOLID"},
}

PILLAR_COLORS = [BLUE, RED, YELLOW, GREEN, "#9334E6", "#12B5CB"]

TABLE_ROWS_PER_SLIDE = 8
CARDS_PER_SLIDE = 6


# --------------------------------------------------------------------------- helpers
def pt(v: float) -> dict:
    return {"magnitude": round(float(v), 2), "unit": "PT"}


def rgb(hex_color: str) -> dict:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return {"rgbColor": {"red": round(r, 4), "green": round(g, 4), "blue": round(b, 4)}}


def solid(hex_color: str, alpha: float | None = None) -> dict:
    fill: dict[str, Any] = {"color": rgb(hex_color)}
    if alpha is not None:
        fill["alpha"] = alpha
    return {"solidFill": fill}


def element_props(page_id: str, r: Rect) -> dict:
    return {
        "pageObjectId": page_id,
        "size": {"width": pt(max(r.w, 1)), "height": pt(max(r.h, 1))},
        "transform": {"scaleX": 1, "scaleY": 1, "translateX": round(r.x, 2), "translateY": round(r.y, 2), "unit": "PT"},
    }


@dataclass
class DeckPlan:
    """Result of building a deck: requests plus metadata for the caller."""

    requests: list[dict]
    slide_ids: list[str]
    diagram_slide_id: str
    warnings: list[str] = field(default_factory=list)
    icon_misses: list[str] = field(default_factory=list)


class DeckBuilder:
    def __init__(self, spec: ArchitectureSpec, icons: IconResolver, layout: Layout | None = None):
        self.spec = spec
        self.icons = icons
        self.layout = layout or build_layout(spec)
        self.prefix = f"g{uuid.uuid4().hex[:6]}"
        self._seq = 0
        self.requests: list[dict] = []
        self.slide_ids: list[str] = []
        self.warnings: list[str] = list(self.layout.warnings)
        self.icon_misses: list[str] = []

    # -- id management ---------------------------------------------------------
    def oid(self, kind: str, name: str | None = None) -> str:
        self._seq += 1
        base = f"{self.prefix}_{kind}{self._seq}"
        if name:
            base += "_" + "".join(ch for ch in name if ch.isalnum() or ch == "_")
        return base[:50]

    # -- primitive requests ----------------------------------------------------
    def add(self, req: dict) -> None:
        self.requests.append(req)

    def new_slide(self) -> str:
        sid = self.oid("slide")
        self.add({"createSlide": {"objectId": sid, "slideLayoutReference": {"predefinedLayout": "BLANK"}}})
        self.slide_ids.append(sid)
        return sid

    def shape(self, page: str, shape_type: str, r: Rect, *, fill: str | None = None, alpha: float | None = None,
              stroke: str | None = None, weight: float = 1.0, dash: str = "SOLID",
              content_alignment: str | None = None, name: str | None = None) -> str:
        sid = self.oid("s", name)
        self.add({"createShape": {"objectId": sid, "shapeType": shape_type, "elementProperties": element_props(page, r)}})
        props: dict[str, Any] = {}
        fields: list[str] = []
        if fill:
            props["shapeBackgroundFill"] = solid(fill, alpha)
            fields.append("shapeBackgroundFill.solidFill")
        else:
            props["shapeBackgroundFill"] = {"propertyState": "NOT_RENDERED"}
            fields.append("shapeBackgroundFill.propertyState")
        if stroke:
            props["outline"] = {"weight": pt(weight), "outlineFill": solid(stroke), "dashStyle": dash}
            fields.append("outline")
        else:
            props["outline"] = {"propertyState": "NOT_RENDERED"}
            fields.append("outline.propertyState")
        if content_alignment:
            props["contentAlignment"] = content_alignment
            fields.append("contentAlignment")
        self.add({"updateShapeProperties": {"objectId": sid, "shapeProperties": props, "fields": ",".join(fields)}})
        return sid

    def text(self, object_id: str, content: str, *, size: float = 10, color: str = INK, bold: bool = False,
             align: str = "START", font: str = FONT, cell: dict | None = None, line_spacing: float | None = None) -> None:
        if not content:
            return
        ins: dict[str, Any] = {"objectId": object_id, "text": content, "insertionIndex": 0}
        style_req: dict[str, Any] = {
            "objectId": object_id,
            "textRange": {"type": "ALL"},
            "style": {"fontFamily": font, "fontSize": pt(size), "bold": bold, "foregroundColor": {"opaqueColor": rgb(color)}},
            "fields": "fontFamily,fontSize,bold,foregroundColor",
        }
        para: dict[str, Any] = {"objectId": object_id, "textRange": {"type": "ALL"}, "style": {"alignment": align}, "fields": "alignment"}
        if line_spacing:
            para["style"]["lineSpacing"] = line_spacing
            para["fields"] += ",lineSpacing"
        if cell:
            ins["cellLocation"] = cell
            style_req["cellLocation"] = cell
            para["cellLocation"] = cell
        self.add({"insertText": ins})
        self.add({"updateTextStyle": style_req})
        self.add({"updateParagraphStyle": para})

    def style_range(self, object_id: str, start: int, end: int, *, size: float | None = None, color: str | None = None,
                    bold: bool | None = None, url: str | None = None, cell: dict | None = None) -> None:
        if end <= start:
            return
        style: dict[str, Any] = {}
        fields = []
        if size is not None:
            style["fontSize"] = pt(size)
            fields.append("fontSize")
        if color is not None:
            style["foregroundColor"] = {"opaqueColor": rgb(color)}
            fields.append("foregroundColor")
        if bold is not None:
            style["bold"] = bold
            fields.append("bold")
        if url:
            style["link"] = {"url": url}
            fields.append("link")
        req: dict[str, Any] = {
            "objectId": object_id,
            "textRange": {"type": "FIXED_RANGE", "startIndex": start, "endIndex": end},
            "style": style,
            "fields": ",".join(fields),
        }
        if cell:
            req["cellLocation"] = cell
        self.add({"updateTextStyle": req})

    def bullets(self, object_id: str, start: int, end: int) -> None:
        if end <= start:
            return
        self.add({"createParagraphBullets": {
            "objectId": object_id,
            "textRange": {"type": "FIXED_RANGE", "startIndex": start, "endIndex": end},
            "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
        }})

    def textbox(self, page: str, r: Rect, content: str, *, size: float = 10, color: str = INK, bold: bool = False,
                align: str = "START", fill: str | None = None, content_alignment: str = "TOP", name: str | None = None) -> str:
        sid = self.shape(page, "TEXT_BOX", r, fill=fill, content_alignment=content_alignment, name=name)
        self.text(sid, content, size=size, color=color, bold=bold, align=align)
        return sid

    def image(self, page: str, url: str, r: Rect, name: str | None = None) -> str:
        iid = self.oid("img", name)
        self.add({"createImage": {"objectId": iid, "url": url, "elementProperties": element_props(page, r)}})
        return iid

    def connector(self, page: str, a: Rect, b: Rect, start_id: str, end_id: str, *, style: str = "solid",
                  bidirectional: bool = False, color: str = LINE_GRAY, weight: float = 1.0, scale: float = 1.0) -> str:
        rt = route(a, b, scale)
        lid = self.oid("ln")
        xs = [p[0] for p in rt.points]
        ys = [p[1] for p in rt.points]
        box = Rect(min(xs), min(ys), max(max(xs) - min(xs), 1), max(max(ys) - min(ys), 1))
        self.add({"createLine": {"objectId": lid, "category": "BENT", "elementProperties": element_props(page, box)}})
        dash = {"solid": "SOLID", "dashed": "DASH", "dotted": "DOT"}[style]
        props = {
            "startConnection": {"connectedObjectId": start_id, "connectionSiteIndex": rt.start_site},
            "endConnection": {"connectedObjectId": end_id, "connectionSiteIndex": rt.end_site},
            "endArrow": "FILL_ARROW",
            "startArrow": "FILL_ARROW" if bidirectional else "NONE",
            "dashStyle": dash,
            "weight": pt(weight),
            "lineFill": solid(color),
        }
        self.add({"updateLineProperties": {
            "objectId": lid, "lineProperties": props,
            "fields": "startConnection,endConnection,endArrow,startArrow,dashStyle,weight,lineFill",
        }})
        return lid

    # -- slide chrome -----------------------------------------------------------
    def chrome(self, page: str, title: str, subtitle: str | None = None) -> None:
        self.textbox(page, Rect(16, 10, 600, 30), title, size=18, color=INK, bold=False, content_alignment="MIDDLE", name="title")
        self.shape(page, "RECTANGLE", Rect(16, 43, 36, 2.5), fill=BLUE, name="accent")
        if subtitle:
            self.textbox(page, Rect(620, 10, 84, 30), subtitle, size=8, color=INK_MUTED, align="END", content_alignment="MIDDLE")
        self.textbox(page, Rect(16, 388, 560, 14),
                     "Google Cloud reference architecture  ·  generated by GCP Architect Agent  ·  icons: cloud.google.com/icons",
                     size=7, color="#9AA0A6", content_alignment="MIDDLE", name="footer")
        self.textbox(page, Rect(640, 388, 64, 14), f"{len(self.slide_ids)}", size=7, color="#9AA0A6", align="END",
                     content_alignment="MIDDLE", name="pageno")

    def google_dots(self, page: str, x: float, y: float, size: float = 8) -> None:
        for i, c in enumerate((BLUE, RED, YELLOW, GREEN)):
            self.shape(page, "ELLIPSE", Rect(x + i * (size + 4), y, size, size), fill=c)

    # -- slides -----------------------------------------------------------------
    def build(self) -> DeckPlan:
        self.cover_slide()
        diagram_id = self.diagram_slide()
        self.component_slides()
        self.consideration_slides()
        self.reference_slide()
        return DeckPlan(self.requests, self.slide_ids, diagram_id, self.warnings, sorted(set(self.icon_misses)))

    def cover_slide(self) -> None:
        page = self.new_slide()
        self.shape(page, "RECTANGLE", Rect(0, 0, 10, PAGE_H), fill=BLUE)
        self.textbox(page, Rect(48, 110, 620, 80), self.spec.title, size=30, color=INK, content_alignment="BOTTOM")
        if self.spec.subtitle:
            self.textbox(page, Rect(48, 195, 620, 50), self.spec.subtitle, size=14, color=INK_MUTED)
        meta = []
        if self.spec.customer:
            meta.append(f"Prepared for {self.spec.customer}")
        meta.append(dt.date.today().strftime("%B %Y"))
        meta.append("Built on official Google Cloud documentation (cloud.google.com)")
        self.textbox(page, Rect(48, 330, 620, 40), "\n".join(meta), size=9, color=INK_MUTED, content_alignment="BOTTOM")
        self.google_dots(page, 48, 90)

    def diagram_slide(self) -> str:
        page = self.new_slide()
        self.chrome(page, "Architecture overview", "editable diagram")
        lay = self.layout
        s = lay.scale
        label_size = max(7.0, round(9.0 * s, 1))
        sub_size = max(6.5, round(7.5 * s, 1))
        zone_size = max(7.5, round(9.0 * s, 1))
        icon_px = 26 * s

        # 1. zones (parents first so children draw on top)
        for zb in lay.zones:
            st = ZONE_STYLE.get(zb.zone.kind, ZONE_STYLE["generic"])
            sid = self.shape(page, "RECTANGLE", zb.rect, fill=st["fill"], stroke=st["stroke"], weight=st["weight"],
                             dash=st["dash"], content_alignment="TOP", name=f"zone_{zb.zone.id}")
            self.text(sid, zb.zone.label, size=zone_size, color=st["label"], bold=True)

        # 2. node cards + icons
        card_ids: dict[str, str] = {}
        for nid, nb in lay.nodes.items():
            n = nb.node
            r = nb.rect
            cid = self.shape(page, "ROUND_RECTANGLE", r, fill="#FFFFFF", stroke=HAIRLINE, weight=0.75,
                             content_alignment="BOTTOM", name=f"node_{nid}")
            card_ids[nid] = cid
            label = n.label
            content = label + (f"\n{n.sublabel}" if n.sublabel else "")
            self.text(cid, content, size=label_size, color=INK, align="CENTER", line_spacing=90)
            if n.sublabel:
                self.style_range(cid, len(label) + 1, len(content), size=sub_size, color=INK_MUTED)
            icon_url = self._icon_url(n)
            if icon_url:
                self.image(page, icon_url, Rect(r.cx - icon_px / 2, r.y + 4 * s, icon_px, icon_px), name=f"icon_{nid}")
            else:
                # Fallback glyph: small coloured circle with the initial letter.
                g = self.shape(page, "ELLIPSE", Rect(r.cx - icon_px / 2, r.y + 4 * s, icon_px, icon_px), fill=BLUE,
                               content_alignment="MIDDLE")
                self.text(g, (label[:1] or "?").upper(), size=max(7, 11 * s), color="#FFFFFF", bold=True, align="CENTER")

        # 3. connectors (bound to cards so they follow when moved)
        for e in self.spec.edges:
            if e.source == e.target or e.source not in lay.nodes or e.target not in lay.nodes:
                continue
            a, b = lay.nodes[e.source].rect, lay.nodes[e.target].rect
            self.connector(page, a, b, card_ids[e.source], card_ids[e.target], style=e.style, bidirectional=e.bidirectional, scale=s)
            if e.label:
                lsize = max(6.5, 7 * s)
                # Text boxes have ~7.2pt insets on each side; size the box so short labels never wrap.
                w = len(e.label) * lsize * 0.56 + 18
                lr = label_rect(route(a, b, s), w, 14)
                self.textbox(page, lr, e.label, size=lsize, color=INK_MUTED, align="CENTER",
                             fill="#FFFFFF", content_alignment="MIDDLE", name="elabel")
        return page

    def _icon_url(self, n: Node) -> str | None:
        icon_id, approx = catalog_data.resolve_icon_id(n.product, n.icon)
        if n.product and icon_id is None:
            self.warnings.append(f"node '{n.id}': unknown product '{n.product}' (no icon/docs). Use list_gcp_products.")
        if approx:
            self.warnings.append(f"node '{n.id}': '{n.product}' has no dedicated official icon yet; using closest icon '{icon_id}'.")
        if not icon_id:
            return None
        url = self.icons.url_for(icon_id)
        if url is None:
            self.icon_misses.append(icon_id)
        return url

    # -- component table ------------------------------------------------------
    def _components(self) -> list[Component]:
        if self.spec.components:
            return self.spec.components
        seen: set[str] = set()
        out: list[Component] = []
        for n in self.spec.nodes:
            key = n.product or n.id
            if key in seen or (n.product and catalog_data.get_product(n.product) and
                               catalog_data.get_product(n.product)["category"] == "external"):
                continue
            seen.add(key)
            out.append(Component(name=n.label, product=n.product, role=n.sublabel or ""))
        return out

    def component_slides(self) -> None:
        comps = self._components()
        if not comps:
            return
        chunks = [comps[i : i + TABLE_ROWS_PER_SLIDE] for i in range(0, len(comps), TABLE_ROWS_PER_SLIDE)]
        for ci, chunk in enumerate(chunks):
            page = self.new_slide()
            title = "Components" + (f" ({ci + 1}/{len(chunks)})" if len(chunks) > 1 else "")
            self.chrome(page, title, "official product documentation")
            tid = self.oid("tbl")
            rows = len(chunk) + 1
            ax, ay, aw, _ = AREA
            self.add({"createTable": {"objectId": tid, "rows": rows, "columns": 4,
                                      "elementProperties": {"pageObjectId": page,
                                                            "size": {"width": pt(aw), "height": pt(24 * rows)},
                                                            "transform": {"scaleX": 1, "scaleY": 1, "translateX": ax, "translateY": ay, "unit": "PT"}}}})
            widths = [140, 210, 198, 140]
            for col, w in enumerate(widths):
                self.add({"updateTableColumnProperties": {"objectId": tid, "columnIndices": [col],
                                                          "tableColumnProperties": {"columnWidth": pt(w)}, "fields": "columnWidth"}})
            headers = ["Component", "Role in this architecture", "Key configuration", "Documentation"]
            for col, h in enumerate(headers):
                cell = {"rowIndex": 0, "columnIndex": col}
                self.text(tid, h, size=9, color="#FFFFFF", bold=True, cell=cell)
                self.add({"updateTableCellProperties": {"objectId": tid,
                                                        "tableRange": {"location": cell, "rowSpan": 1, "columnSpan": 1},
                                                        "tableCellProperties": {"tableCellBackgroundFill": solid(BLUE)},
                                                        "fields": "tableCellBackgroundFill.solidFill"}})
            for ri, c in enumerate(chunk, start=1):
                prod = catalog_data.get_product(c.product) if c.product else None
                name = c.name or (prod["name"] if prod else "")
                self.text(tid, name, size=9, color=INK, bold=True, cell={"rowIndex": ri, "columnIndex": 0})
                self.text(tid, c.role or "", size=8.5, color=INK_SOFT, cell={"rowIndex": ri, "columnIndex": 1})
                self.text(tid, c.key_config or "", size=8.5, color=INK_SOFT, cell={"rowIndex": ri, "columnIndex": 2})
                docs = prod.get("docs") if prod else None
                if docs:
                    cell = {"rowIndex": ri, "columnIndex": 3}
                    label = docs.replace("https://", "")
                    self.text(tid, label, size=7.5, color="#1A73E8", cell=cell)
                    self.style_range(tid, 0, len(label), url=docs, cell=cell)
                if ri % 2 == 0:
                    self.add({"updateTableCellProperties": {"objectId": tid,
                                                            "tableRange": {"location": {"rowIndex": ri, "columnIndex": 0}, "rowSpan": 1, "columnSpan": 4},
                                                            "tableCellProperties": {"tableCellBackgroundFill": solid(PAPER)},
                                                            "fields": "tableCellBackgroundFill.solidFill"}})

    # -- design considerations ----------------------------------------------------
    def consideration_slides(self) -> None:
        cons = self.spec.considerations
        if not cons:
            return
        chunks = [cons[i : i + CARDS_PER_SLIDE] for i in range(0, len(cons), CARDS_PER_SLIDE)]
        for ci, chunk in enumerate(chunks):
            page = self.new_slide()
            title = "Design considerations" + (f" ({ci + 1}/{len(chunks)})" if len(chunks) > 1 else "")
            self.chrome(page, title, "Google Cloud Architecture Framework")
            ax, ay, aw, ah = AREA
            cols = min(3, len(chunk))
            rows = max(2, (len(chunk) + cols - 1) // cols)  # never let 1-3 cards span the full height
            gap = 12
            cw = (aw - gap * (cols - 1)) / cols
            ch = (ah - gap * (rows - 1)) / rows
            body_size = 9.5 if len(chunk) <= 3 else 8.5
            for i, c in enumerate(chunk):
                col, row = i % cols, i // cols
                r = Rect(ax + col * (cw + gap), ay + row * (ch + gap), cw, ch)
                color = PILLAR_COLORS[(ci * CARDS_PER_SLIDE + i) % len(PILLAR_COLORS)]
                self.shape(page, "RECTANGLE", Rect(r.x, r.y, 4, r.h), fill=color)  # coloured left accent
                card = self.shape(page, "RECTANGLE", Rect(r.x + 4, r.y, r.w - 4, r.h), fill=PAPER, stroke=HAIRLINE, weight=0.75,
                                  content_alignment="TOP")
                points = c.points[:5]
                body = "\n".join(points)
                content = f"{c.pillar}\n{body}" if body else c.pillar
                self.text(card, content, size=body_size, color=INK_SOFT, line_spacing=110)
                self.style_range(card, 0, len(c.pillar), size=body_size + 2.5, color=INK, bold=True)
                if body:
                    self.bullets(card, len(c.pillar) + 1, len(content))

    # -- references ---------------------------------------------------------------
    def reference_slide(self) -> None:
        refs = list(self.spec.references)
        seen = {r.url for r in refs}
        for n in self.spec.nodes:  # add product docs that are not already referenced
            prod = catalog_data.get_product(n.product) if n.product else None
            if prod and prod.get("docs") and prod["docs"] not in seen:
                refs.append(Reference(title=f"{prod['name']} documentation", url=prod["docs"]))
                seen.add(prod["docs"])
        if not refs:
            return
        refs = refs[:16]
        page = self.new_slide()
        self.chrome(page, "References", "official documentation")
        ax, ay, aw, ah = AREA
        half = (len(refs) + 1) // 2 if len(refs) > 8 else len(refs)
        columns = [refs[:half], refs[half:]] if len(refs) > 8 else [refs]
        colw = (aw - 16) / len(columns)
        for ci, col in enumerate(columns):
            if not col:
                continue
            tb = self.shape(page, "TEXT_BOX", Rect(ax + ci * (colw + 16), ay, colw, ah), content_alignment="TOP")
            lines = [f"{r.title}\n{r.url}" for r in col]
            content = "\n\n".join(lines)
            self.text(tb, content, size=9, color=INK, line_spacing=100)
            pos = 0
            for r in col:
                t_end = pos + len(r.title)
                u_start = t_end + 1
                u_end = u_start + len(r.url)
                self.style_range(tb, pos, t_end, bold=True)
                self.style_range(tb, u_start, u_end, size=7.5, color="#1A73E8", url=r.url)
                pos = u_end + 2
