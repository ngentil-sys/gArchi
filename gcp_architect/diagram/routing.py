"""Connector routing shared by the Slides builder and the local preview.

Google Slides bent connectors route themselves between the chosen connection
sites; we only choose the sites and predict the path well enough to place edge
labels and to draw the preview.
"""

from __future__ import annotations

from dataclasses import dataclass

from .layout import GAP_X, Rect

# Connection site indexes for RECTANGLE / ROUND_RECTANGLE (ECMA-376).
SITE_TOP, SITE_LEFT, SITE_BOTTOM, SITE_RIGHT = 0, 1, 2, 3


@dataclass
class Route:
    start_site: int
    end_site: int
    points: list[tuple[float, float]]  # predicted polyline
    label_center: tuple[float, float]
    label_side: str  # "center" | "right": where the label box sits relative to label_center


def route(a: Rect, b: Rect, scale: float = 1.0) -> Route:
    dx, dy = b.cx - a.cx, b.cy - a.cy
    gap = GAP_X * scale

    same_column = abs(dx) < a.w / 2
    skips_a_card = abs(dy) > a.h * 1.5
    if same_column and skips_a_card:
        # Loop around the right-hand side so the line does not cross cards in between.
        off = gap / 2
        x = max(a.x + a.w, b.x + b.w) + off
        pts = [(a.x + a.w, a.cy), (x, a.cy), (x, b.cy), (b.x + b.w, b.cy)]
        return Route(SITE_RIGHT, SITE_RIGHT, pts, (x, (a.cy + b.cy) / 2), "center")

    different_column = abs(dx) >= a.w
    if different_column or abs(dx) >= abs(dy):
        if dx >= 0:
            s, e = SITE_RIGHT, SITE_LEFT
            p0, p3 = (a.x + a.w, a.cy), (b.x, b.cy)
        else:
            s, e = SITE_LEFT, SITE_RIGHT
            p0, p3 = (a.x, a.cy), (b.x + b.w, b.cy)
        mx = (p0[0] + p3[0]) / 2
        pts = [p0, (mx, p0[1]), (mx, p3[1]), p3]
        if abs(dy) > 12 * scale:
            label = (mx, (p0[1] + p3[1]) / 2)
        else:
            label = (mx, p0[1] - 9 * scale)
        return Route(s, e, pts, label, "center")

    if dy >= 0:
        s, e = SITE_BOTTOM, SITE_TOP
        p0, p3 = (a.cx, a.y + a.h), (b.cx, b.y)
    else:
        s, e = SITE_TOP, SITE_BOTTOM
        p0, p3 = (a.cx, a.y), (b.cx, b.y + b.h)
    my = (p0[1] + p3[1]) / 2
    pts = [p0, (p0[0], my), (p3[0], my), p3]
    if abs(dx) > 12 * scale:
        return Route(s, e, pts, ((p0[0] + p3[0]) / 2, my), "center")
    return Route(s, e, pts, (p0[0] + 4 * scale, my), "right")


def label_rect(r: Route, w: float, h: float) -> Rect:
    x, y = r.label_center
    if r.label_side == "right":
        return Rect(x, y - h / 2, w, h)
    return Rect(x - w / 2, y - h / 2, w, h)
