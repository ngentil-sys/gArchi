#!/usr/bin/env python3
"""Render a local PNG preview of the diagram slide (approximation of the Slides output).

Usage: python scripts/preview_diagram.py [spec.json] [-o out.png]
Without a spec file the built-in example spec is rendered.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gcp_architect.diagram.icons import local_icon_path  # noqa: E402
from gcp_architect.diagram.layout import PAGE_H, PAGE_W, build_layout  # noqa: E402
from gcp_architect.diagram.routing import label_rect, route  # noqa: E402
from gcp_architect.diagram.slides_builder import HAIRLINE, INK, INK_MUTED, LINE_GRAY, ZONE_STYLE  # noqa: E402
from gcp_architect.diagram.spec import ArchitectureSpec, example_spec  # noqa: E402
from gcp_architect import catalog_data  # noqa: E402

S = 3  # px per pt


def font(size_pt: float, bold: bool = False):
    cands = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf" if bold else "/usr/share/fonts/truetype/roboto/Roboto-Regular.ttf",
    ]
    for c in cands:
        if Path(c).exists():
            return ImageFont.truetype(c, int(size_pt * S))
    return ImageFont.load_default()


def wrap(draw, text, fnt, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=fnt) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", nargs="?")
    ap.add_argument("-o", "--out", default="/tmp/diagram_preview.png")
    args = ap.parse_args()
    data = json.loads(Path(args.spec).read_text()) if args.spec else example_spec()
    spec = ArchitectureSpec.model_validate(data)
    lay = build_layout(spec)
    sc = lay.scale

    img = Image.new("RGB", (int(PAGE_W * S), int(PAGE_H * S)), "white")
    d = ImageDraw.Draw(img)
    d.text((16 * S, 14 * S), "Architecture overview", fill=INK, font=font(18))
    d.rectangle([16 * S, 43 * S, 52 * S, 45.5 * S], fill="#4285F4")

    for zb in lay.zones:
        st = ZONE_STYLE.get(zb.zone.kind, ZONE_STYLE["generic"])
        r = zb.rect
        d.rectangle([r.x * S, r.y * S, (r.x + r.w) * S, (r.y + r.h) * S], fill=st["fill"], outline=st["stroke"], width=int(st["weight"] * S))
        d.text(((r.x + 7) * S, (r.y + 4) * S), zb.zone.label, fill=st["label"], font=font(max(7.5, 9 * sc), bold=True))

    card = {}
    for nid, nb in lay.nodes.items():
        r = nb.rect
        card[nid] = r
        d.rounded_rectangle([r.x * S, r.y * S, (r.x + r.w) * S, (r.y + r.h) * S], radius=int(5 * S), fill="white", outline=HAIRLINE, width=2)
        icon_id, _ = catalog_data.resolve_icon_id(nb.node.product, nb.node.icon)
        p = local_icon_path(icon_id) if icon_id else None
        ic = 26 * sc
        if p:
            im = Image.open(p).convert("RGBA").resize((int(ic * S), int(ic * S)), Image.LANCZOS)
            img.paste(im, (int((r.cx - ic / 2) * S), int((r.y + 4 * sc) * S)), im)
        f1, f2 = font(max(7, 9 * sc)), font(max(6.5, 7.5 * sc))
        lines = [(l, f1, INK) for l in wrap(d, nb.node.label, f1, (r.w - 10) * S)]
        if nb.node.sublabel:
            lines += [(l, f2, INK_MUTED) for l in wrap(d, nb.node.sublabel, f2, (r.w - 10) * S)]
        y = (r.y + r.h - 4) * S - sum(fn.size * 1.15 for _, fn, _ in lines)
        for t, fn, col in lines:
            d.text((r.cx * S - d.textlength(t, font=fn) / 2, y), t, fill=col, font=fn)
            y += fn.size * 1.15

    for e in spec.edges:
        if e.source not in card or e.target not in card:
            continue
        a, b = card[e.source], card[e.target]
        rt = route(a, b, sc)
        pts = rt.points
        col = LINE_GRAY
        for i in range(len(pts) - 1):
            seg = [tuple(v * S for v in pts[i]), tuple(v * S for v in pts[i + 1])]
            if e.style == "solid":
                d.line(seg, fill=col, width=3)
            else:
                _dashed(d, seg, col)
        # arrow head oriented along the last segment
        (px, py), (x, y) = pts[-2], pts[-1]
        ah = 4
        if abs(x - px) >= abs(y - py):
            sgn = 1 if x >= px else -1
            tri = [(x, y), (x - sgn * ah, y - ah / 1.6), (x - sgn * ah, y + ah / 1.6)]
        else:
            sgn = 1 if y >= py else -1
            tri = [(x, y), (x - ah / 1.6, y - sgn * ah), (x + ah / 1.6, y - sgn * ah)]
        d.polygon([(qx * S, qy * S) for qx, qy in tri], fill=col)
        if e.label:
            lsize = max(6.5, 7 * sc)
            fl = font(lsize)
            w = max(24.0, len(e.label) * lsize * 0.58 + 10)
            lr = label_rect(rt, w, 13)
            d.rectangle([lr.x * S, lr.y * S, (lr.x + lr.w) * S, (lr.y + lr.h) * S], fill="white")
            tw = d.textlength(e.label, font=fl)
            d.text((lr.cx * S - tw / 2, lr.cy * S - fl.size / 2), e.label, fill=INK_MUTED, font=fl)

    img.save(args.out)
    print(f"wrote {args.out} (scale {sc:.2f}); warnings: {lay.warnings}")


def _dashed(d, seg, col, dash=6, gap=4):
    (x0, y0), (x1, y1) = seg
    length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    if length == 0:
        return
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    pos = 0
    while pos < length:
        end = min(pos + dash, length)
        d.line([(x0 + ux * pos, y0 + uy * pos), (x0 + ux * end, y0 + uy * end)], fill=col, width=3)
        pos += dash + gap


if __name__ == "__main__":
    main()
