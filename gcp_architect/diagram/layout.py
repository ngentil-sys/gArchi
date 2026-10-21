"""Deterministic layout engine: ArchitectureSpec -> positioned zones and node cards.

Coordinates are in points on a 720 x 405 pt Google Slides page. The layout is
zone-driven: top-level zones are laid out left-to-right, nested zones are laid
out left-to-right inside their parent, and nodes inside a zone are packed into a
small grid ordered by their rank in the edge graph (sources first), so the main
flow reads left-to-right.

Legibility first: text sizes are fixed and card sizes are derived from the text
they must hold. When the natural size exceeds the drawing area the engine
compacts everything *except* text in stages (gaps -> padding/icons/title bar ->
more rows per column) and only as a last resort scales the geometry uniformly,
clamping fonts to a readable floor and emitting a warning.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field, replace

from .spec import ArchitectureSpec, Node, Zone

PAGE_W, PAGE_H = 720.0, 405.0

# Text metrics (pt). Fonts are fixed; geometry adapts to them.
LABEL_PT, SUBLABEL_PT, DETAIL_PT = 9.0, 7.5, 6.8
ZONE_PT, ZONE_DETAIL_PT = 9.0, 7.2
LABEL_CHAR_W, SUB_CHAR_W, MONO_CHAR_W = 5.1, 4.1, 4.4  # avg glyph widths incl. letter spacing
TEXT_INSET = 14.4  # Slides shape text insets: 7.2 pt each side
LINE_H = {"label": 10.4, "sub": 8.7, "detail": 7.9}

# Kept for callers that import them (routing / preview).
GAP_X, GAP_Y = 24.0, 16.0


@dataclass(frozen=True)
class Params:
    """Non-text geometry; each compaction level shrinks these, never the fonts."""

    level: int = 0
    area_top: float = 52.0  # below the slide title
    area_bottom: float = 388.0  # above the footer
    gap_x: float = 24.0
    gap_y: float = 16.0
    zone_gap: float = 16.0
    zone_pad: float = 10.0
    zone_header: float = 20.0
    zone_detail_h: float = 10.0
    icon: float = 26.0
    icon_top: float = 4.0
    card_pad_bottom: float = 5.0
    max_rows: int = 3
    min_card_w: float = 100.0
    title_pt: float = 18.0
    text_scale: float = 1.0  # last resort: shrink fonts AND card metrics together (never one without the other)

    @property
    def area(self) -> tuple[float, float, float, float]:
        return 16.0, self.area_top, 688.0, self.area_bottom - self.area_top


COMPACTION_LEVELS: list[Params] = [
    Params(level=0),
    Params(level=1, gap_x=16, gap_y=12, zone_gap=10, zone_pad=7),
    Params(level=2, gap_x=14, gap_y=10, zone_gap=8, zone_pad=6, zone_header=17, zone_detail_h=9,
           icon=20, icon_top=3, area_top=40, title_pt=14, card_pad_bottom=4),
    Params(level=3, gap_x=12, gap_y=8, zone_gap=8, zone_pad=5, zone_header=16, zone_detail_h=9,
           icon=18, icon_top=3, area_top=40, title_pt=14, card_pad_bottom=3, max_rows=4),
    Params(level=4, gap_x=10, gap_y=7, zone_gap=6, zone_pad=4, zone_header=15, zone_detail_h=8,
           icon=16, icon_top=2, area_top=36, area_bottom=394, title_pt=13, card_pad_bottom=3, max_rows=5),
]
# Beyond level 4, text and cards shrink together in these steps.
TEXT_SCALE_STEPS = (0.92, 0.85, 0.78, 0.72)
ROW_CANDIDATES = (3, 2, 4, 5, 6)

AREA = COMPACTION_LEVELS[0].area  # backwards-compatible default drawing area


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def scaled(self, s: float, dx: float, dy: float) -> "Rect":
        return Rect(dx + self.x * s, dy + self.y * s, self.w * s, self.h * s)


@dataclass
class ZoneBox:
    zone: Zone
    rect: Rect
    depth: int


@dataclass
class NodeBox:
    node: Node
    rect: Rect


@dataclass
class Layout:
    zones: list[ZoneBox]  # parents before children (draw order)
    nodes: dict[str, NodeBox]
    scale: float
    node_w: float
    node_h: float
    params: Params = field(default_factory=Params)
    detail_lines: int = 0
    fonts: dict[str, float] = field(default_factory=dict)  # final font sizes after any last-resort scaling
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Block:
    zone: Zone | None
    nodes: list[Node]
    children: list["_Block"]
    w: float = 0.0
    h: float = 0.0
    cols: int = 0
    rows: int = 0
    node_block_w: float = 0.0
    node_block_h: float = 0.0


# --------------------------------------------------------------------------- text metrics
def _wrap_lines(text: str, char_w: float, width: float) -> int:
    """Number of lines `text` needs at `char_w` average glyph width inside `width`."""
    usable = max(width - TEXT_INSET, char_w * 4)
    per_line = max(4, int(usable / char_w))
    lines, cur = 1, 0
    for word in text.split():
        need = len(word) + (1 if cur else 0)
        if cur and cur + need > per_line:
            lines += 1
            cur = len(word)
        else:
            cur += need
    return lines


def card_size(nodes: list[Node], p: Params) -> tuple[float, float]:
    """Uniform card size that fits the longest label word, sublabel and detail lines."""
    t = p.text_scale
    lcw, scw, mcw = LABEL_CHAR_W * t, SUB_CHAR_W * t, MONO_CHAR_W * t
    lh = {k: v * t for k, v in LINE_H.items()}
    w = p.min_card_w * t
    for n in nodes:
        longest_word = max((len(wd) for wd in n.label.split()), default=0)
        w = max(w, longest_word * lcw + TEXT_INSET + 4)
        for d in n.details:
            w = max(w, len(d) * mcw + TEXT_INSET + 4)
        if n.sublabel:
            longest_sub = max((len(wd) for wd in n.sublabel.split()), default=0)
            w = max(w, longest_sub * scw + TEXT_INSET + 4)
    w = min(w, 150.0)
    text_h = 0.0
    for n in nodes:
        h = _wrap_lines(n.label, lcw, w) * lh["label"]
        if n.sublabel:
            h += _wrap_lines(n.sublabel, scw, w) * lh["sub"]
        h += len(n.details) * lh["detail"]
        text_h = max(text_h, h)
    h = p.icon_top + p.icon + 4 + text_h + p.card_pad_bottom + 3.6  # 3.6 = bottom text inset
    return round(w, 1), round(max(h, 50.0 * t), 1)


# --------------------------------------------------------------------------- graph ranks
def compute_ranks(spec: ArchitectureSpec) -> dict[str, int]:
    """Longest-path layering over the edge graph; cycles are broken on back edges."""
    order = [n.id for n in spec.nodes]
    pred: dict[str, list[str]] = defaultdict(list)
    for e in spec.edges:
        if e.source != e.target:
            pred[e.target].append(e.source)

    memo: dict[str, int] = {}
    onstack: set[str] = set()

    def depth(u: str) -> int:
        if u in memo:
            return memo[u]
        if u in onstack:
            return 0
        onstack.add(u)
        d = 0
        for q in pred.get(u, []):
            d = max(d, depth(q) + 1)
        onstack.discard(u)
        memo[u] = d
        return d

    return {n: depth(n) for n in order}


# --------------------------------------------------------------------------- measuring / placing
def _grid(n: int, max_rows: int) -> tuple[int, int]:
    if n <= 0:
        return 0, 0
    cols = math.ceil(n / max_rows)
    rows = math.ceil(n / cols)
    return cols, rows


def _column_sizes(n: int, cols: int) -> list[int]:
    base, extra = divmod(n, cols)
    return [base + (1 if i < extra else 0) for i in range(cols)]


def _zone_header(zone: Zone | None, p: Params) -> float:
    return p.zone_header + (p.zone_detail_h if zone is not None and zone.detail else 0.0)


def _measure(block: _Block, p: Params, node_w: float, node_h: float) -> None:
    for c in block.children:
        _measure(c, p, node_w, node_h)
    n = len(block.nodes)
    cols, rows = _grid(n, p.max_rows)
    block.cols, block.rows = cols, rows
    block.node_block_w = cols * node_w + max(0, cols - 1) * p.gap_x if n else 0.0
    block.node_block_h = rows * node_h + max(0, rows - 1) * p.gap_y if n else 0.0

    parts_w = ([block.node_block_w] if n else []) + [c.w for c in block.children]
    parts_h = ([block.node_block_h] if n else []) + [c.h for c in block.children]
    inner_w = sum(parts_w) + max(0, len(parts_w) - 1) * p.zone_gap
    inner_h = max(parts_h) if parts_h else node_h

    if block.zone is None:
        block.w, block.h = inner_w, inner_h
    else:
        label_w = (max(len(block.zone.label) * 5.6, len(block.zone.detail or "") * 4.6)) * p.text_scale + TEXT_INSET
        block.w = max(inner_w, node_w, label_w) + 2 * p.zone_pad
        block.h = inner_h + _zone_header(block.zone, p) + p.zone_pad


def _place(block: _Block, x: float, y: float, h: float, p: Params, node_w: float, node_h: float,
           depth: int, zones: list[ZoneBox], nodes: dict[str, NodeBox]) -> None:
    """Place a block at (x, y) with an allotted height `h` (siblings are stretched to equal height)."""
    if block.zone is not None:
        zones.append(ZoneBox(block.zone, Rect(x, y, block.w, h), depth))
        cx, cy = x + p.zone_pad, y + _zone_header(block.zone, p)
        inner_h = h - _zone_header(block.zone, p) - p.zone_pad
        child_depth = depth + 1
    else:
        cx, cy = x, y
        inner_h = h
        child_depth = depth

    cursor = cx
    if block.nodes:
        top = cy + (inner_h - block.node_block_h) / 2
        i = 0
        for col, size in enumerate(_column_sizes(len(block.nodes), block.cols)):
            col_h = size * node_h + (size - 1) * p.gap_y
            col_top = top + (block.node_block_h - col_h) / 2
            for row in range(size):
                node = block.nodes[i]
                i += 1
                nx = cursor + col * (node_w + p.gap_x)
                ny = col_top + row * (node_h + p.gap_y)
                nodes[node.id] = NodeBox(node, Rect(nx, ny, node_w, node_h))
        cursor += block.node_block_w + p.zone_gap
    for child in block.children:
        _place(child, cursor, cy, inner_h, p, node_w, node_h, child_depth, zones, nodes)
        cursor += child.w + p.zone_gap


def _attempt(spec: ArchitectureSpec, p: Params, ranks: dict[str, int]) -> tuple[list[ZoneBox], dict[str, NodeBox], float, float, float, float]:
    """Lay out at params `p`; returns (zones, nodes, total_w, total_h, node_w, node_h) unscaled."""
    order_index = {n.id: i for i, n in enumerate(spec.nodes)}

    def sort_nodes(ns: list[Node]) -> list[Node]:
        return sorted(ns, key=lambda n: (ranks.get(n.id, 0), order_index[n.id]))

    node_w, node_h = card_size(spec.nodes, p)
    nodes_by_zone: dict[str | None, list[Node]] = defaultdict(list)
    for n in spec.nodes:
        nodes_by_zone[n.zone].append(n)

    free = nodes_by_zone.pop(None, [])
    in_deg: dict[str, int] = defaultdict(int)
    out_deg: dict[str, int] = defaultdict(int)
    for e in spec.edges:
        in_deg[e.target] += 1
        out_deg[e.source] += 1
    left_free = [n for n in free if out_deg[n.id] >= in_deg[n.id]]
    right_free = [n for n in free if n not in left_free]

    children_of: dict[str | None, list[Zone]] = defaultdict(list)
    for z in spec.zones:
        children_of[z.parent].append(z)

    def make_block(z: Zone) -> _Block:
        return _Block(zone=z, nodes=sort_nodes(nodes_by_zone.get(z.id, [])), children=[make_block(c) for c in children_of[z.id]])

    roots: list[_Block] = []
    if left_free:
        roots.append(_Block(zone=None, nodes=sort_nodes(left_free), children=[]))
    roots.extend(make_block(z) for z in children_of[None])
    if right_free:
        roots.append(_Block(zone=None, nodes=sort_nodes(right_free), children=[]))

    for r in roots:
        _measure(r, p, node_w, node_h)
    total_w = sum(r.w for r in roots) + max(0, len(roots) - 1) * p.zone_gap
    total_h = max(r.h for r in roots) if roots else node_h

    zones: list[ZoneBox] = []
    nodes: dict[str, NodeBox] = {}
    cursor = 0.0
    for r in roots:
        _place(r, cursor, 0.0, total_h, p, node_w, node_h, 0, zones, nodes)
        cursor += r.w + p.zone_gap
    return zones, nodes, total_w, total_h, node_w, node_h


def build_layout(spec: ArchitectureSpec) -> Layout:
    warnings: list[str] = []
    ranks = compute_ranks(spec)
    detail_lines = max((len(n.details) for n in spec.nodes), default=0)

    def best_rows(p: Params):
        """Try several rows-per-column counts and keep the one that fits the slide best."""
        best = None
        for rows in ROW_CANDIDATES:
            q = replace(p, max_rows=rows)
            zones, nodes, tw, th, nw, nh = _attempt(spec, q, ranks)
            _, _, aw, ah = q.area
            fit = min(aw / tw, ah / th)
            if best is None or fit > best[-1]:
                best = (q, zones, nodes, tw, th, nw, nh, fit)
            if fit >= 1.0:
                break
        return best

    chosen = None
    candidates = list(COMPACTION_LEVELS) + [replace(COMPACTION_LEVELS[-1], text_scale=t) for t in TEXT_SCALE_STEPS]
    for p in candidates:
        chosen = best_rows(p)
        if chosen[-1] >= 1.0:
            break

    p, zones, nodes, tw, th, nw, nh, fit = chosen
    ax, ay, aw, ah = p.area
    scale = min(1.0, fit)
    if scale < 1.0:
        warnings.append(
            f"Diagram exceeds the slide even with maximum compaction and {p.text_scale:.0%} text; "
            f"geometry was scaled to {scale:.0%}. Split into a context diagram plus detail diagrams, "
            "shorten labels/details, or reduce nodes."
        )
    elif p.text_scale < 1.0:
        warnings.append(f"Dense diagram: text and cards were reduced to {p.text_scale:.0%} to fit. "
                        "Consider splitting into several diagram slides.")
    elif p.level >= 2:
        warnings.append(f"Diagram is dense (compaction level {p.level}): gaps, icons and title were reduced to keep text legible.")

    dx = ax + (aw - tw * scale) / 2
    dy = ay + (ah - th * scale) / 2
    zones = [ZoneBox(z.zone, z.rect.scaled(scale, dx, dy), z.depth) for z in zones]
    nodes = {k: NodeBox(v.node, v.rect.scaled(scale, dx, dy)) for k, v in nodes.items()}
    zones.sort(key=lambda zb: zb.depth)

    def f(size: float, floor: float) -> float:
        return round(max(floor, size * p.text_scale * scale), 1)

    fonts = {
        "label": f(LABEL_PT, 6.0), "sub": f(SUBLABEL_PT, 5.5), "detail": f(DETAIL_PT, 5.0),
        "zone": f(ZONE_PT, 6.5), "zone_detail": f(ZONE_DETAIL_PT, 5.5), "edge": f(7.0, 5.5),
    }
    return Layout(zones=zones, nodes=nodes, scale=scale, node_w=nw * scale, node_h=nh * scale,
                  params=replace(p), detail_lines=detail_lines, fonts=fonts, warnings=warnings)
