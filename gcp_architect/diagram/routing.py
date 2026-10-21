"""Connector routing shared by the Slides builder and the local preview.

Google Slides bent connectors route themselves between the chosen connection
sites; we only choose the sites and predict the path well enough to place edge
labels and to draw the preview.
"""

from __future__ import annotations

from dataclasses import dataclass

from .layout import GAP_X, GAP_Y, Rect

# Connection site indexes for RECTANGLE / ROUND_RECTANGLE (ECMA-376).
SITE_TOP, SITE_LEFT, SITE_BOTTOM, SITE_RIGHT = 0, 1, 2, 3


@dataclass
class Route:
    start_site: int
    end_site: int
    points: list[tuple[float, float]]  # predicted polyline
    label_center: tuple[float, float]
    label_side: str  # "center" | "right": where the label box sits relative to label_center


def route(a: Rect, b: Rect, scale: float = 1.0, gap_x: float = GAP_X, gap_y: float = GAP_Y) -> Route:
    dx, dy = b.cx - a.cx, b.cy - a.cy
    gap = gap_x * scale

    same_column = abs(dx) < a.w / 2
    # Adjacent rows are one (card + gap) apart; anything beyond 1.5x means a card sits in between.
    skips_a_card = abs(dy) > 1.5 * (a.h + gap_y * scale)
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


def label_rect(r: Route, w: float, h: float, placed: list[Rect] | None = None, obstacles: list[Rect] | None = None) -> Rect:
    """Label box on the route.

    Samples positions along the whole polyline and picks the one with the least overlap with
    `obstacles` (cards) and previously `placed` labels, preferring the route's natural label point.
    """
    x, y = r.label_center
    natural = Rect(x, y - h / 2, w, h) if r.label_side == "right" else Rect(x - w / 2, y - h / 2, w, h)
    obstacles = obstacles or []
    placed_rects = placed if placed is not None else []

    def cost(rect: Rect) -> float:
        c = sum(_overlap_area(rect, o) for o in obstacles) + 3 * sum(_overlap_area(rect, p) for p in placed_rects)
        c += 0.02 * (abs(rect.cx - natural.cx) + abs(rect.cy - natural.cy))  # stay near the natural spot
        return c

    best, best_cost = natural, cost(natural)
    if best_cost > 0:
        for px, py in _sample_polyline(r.points, step=5.0):
            for cand in (Rect(px - w / 2, py - h / 2, w, h), Rect(px + 2, py - h / 2, w, h), Rect(px - w - 2, py - h / 2, w, h)):
                c = cost(cand)
                if c < best_cost - 1e-6:
                    best, best_cost = cand, c
    if placed is not None:
        placed.append(best)
    return best


def _sample_polyline(points: list[tuple[float, float]], step: float) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        length = max(abs(x1 - x0), abs(y1 - y0))
        n = max(1, int(length / step))
        for i in range(n + 1):
            t = i / n
            out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return out


def _overlap_area(a: Rect, b: Rect) -> float:
    w = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
    h = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
    return w * h if w > 0 and h > 0 else 0.0
