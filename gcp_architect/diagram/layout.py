"""Deterministic layout engine: ArchitectureSpec -> positioned zones and node cards.

Coordinates are in points on a 720 x 405 pt Google Slides page. The layout is
zone-driven: top-level zones are laid out left-to-right, nested zones are laid
out left-to-right inside their parent, and nodes inside a zone are packed into a
small grid ordered by their rank in the edge graph (sources first), so the main
flow reads left-to-right. If the natural size exceeds the drawing area the
whole diagram is scaled down uniformly.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from .spec import ArchitectureSpec, Node, Zone

PAGE_W, PAGE_H = 720.0, 405.0

# Drawing area for the diagram (below the slide title, above the footer).
AREA = (16.0, 52.0, 688.0, 336.0)  # x, y, w, h

NODE_W, NODE_H = 100.0, 66.0
NODE_W_DETAILED = 120.0  # wider card when any node carries detail lines (monospace is wide)
DETAIL_LINE_H = 9.5  # extra card height per detail line
GAP_X, GAP_Y = 24.0, 16.0
ZONE_PAD = 10.0
ZONE_HEADER = 20.0  # space reserved for the zone label at the top
ZONE_DETAIL_H = 10.0  # extra header height when the zone has a detail line
ZONE_GAP = 16.0
MAX_ROWS = 3

_LEFT, _RIGHT = "__left__", "__right__"


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
    detail_lines: int = 0  # max detail lines on any card (drives card height)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Block:
    """Intermediate tree used while measuring."""

    zone: Zone | None
    nodes: list[Node]
    children: list["_Block"]
    w: float = 0.0
    h: float = 0.0
    cols: int = 0
    rows: int = 0
    node_block_w: float = 0.0
    node_block_h: float = 0.0


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
        for p in pred.get(u, []):
            d = max(d, depth(p) + 1)
        onstack.discard(u)
        memo[u] = d
        return d

    return {n: depth(n) for n in order}


def _grid(n: int) -> tuple[int, int]:
    if n <= 0:
        return 0, 0
    cols = math.ceil(n / MAX_ROWS)
    rows = math.ceil(n / cols)
    return cols, rows


def _zone_header(zone: Zone | None) -> float:
    return ZONE_HEADER + (ZONE_DETAIL_H if zone is not None and zone.detail else 0.0)


def _measure(block: _Block, node_w: float, node_h: float) -> None:
    for c in block.children:
        _measure(c, node_w, node_h)
    n = len(block.nodes)
    cols, rows = _grid(n)
    block.cols, block.rows = cols, rows
    block.node_block_w = cols * node_w + max(0, cols - 1) * GAP_X if n else 0.0
    block.node_block_h = rows * node_h + max(0, rows - 1) * GAP_Y if n else 0.0

    parts_w = ([block.node_block_w] if n else []) + [c.w for c in block.children]
    parts_h = ([block.node_block_h] if n else []) + [c.h for c in block.children]
    inner_w = sum(parts_w) + max(0, len(parts_w) - 1) * ZONE_GAP
    inner_h = max(parts_h) if parts_h else node_h

    if block.zone is None:  # pseudo container for free nodes
        block.w, block.h = inner_w, inner_h
    else:
        label_w = max(len(block.zone.label) * 5.6, len(block.zone.detail or "") * 4.6) + 14
        block.w = max(inner_w, node_w, label_w) + 2 * ZONE_PAD
        block.h = inner_h + _zone_header(block.zone) + ZONE_PAD


def _place(block: _Block, x: float, y: float, h: float, node_w: float, node_h: float, depth: int, zones: list[ZoneBox], nodes: dict[str, NodeBox]) -> None:
    """Place a block at (x, y) with an allotted height `h` (siblings are stretched to equal height)."""
    if block.zone is not None:
        zones.append(ZoneBox(block.zone, Rect(x, y, block.w, h), depth))
        cx, cy = x + ZONE_PAD, y + _zone_header(block.zone)
        inner_h = h - _zone_header(block.zone) - ZONE_PAD
        child_depth = depth + 1
    else:
        cx, cy = x, y
        inner_h = h
        child_depth = depth

    cursor = cx
    if block.nodes:
        top = cy + (inner_h - block.node_block_h) / 2
        per_col = _column_sizes(len(block.nodes), block.cols)
        i = 0
        for col, size in enumerate(per_col):
            col_h = size * node_h + (size - 1) * GAP_Y
            col_top = top + (block.node_block_h - col_h) / 2
            for row in range(size):
                node = block.nodes[i]
                i += 1
                nx = cursor + col * (node_w + GAP_X)
                ny = col_top + row * (node_h + GAP_Y)
                nodes[node.id] = NodeBox(node, Rect(nx, ny, node_w, node_h))
        cursor += block.node_block_w + ZONE_GAP
    for child in block.children:
        _place(child, cursor, cy, inner_h, node_w, node_h, child_depth, zones, nodes)
        cursor += child.w + ZONE_GAP


def _column_sizes(n: int, cols: int) -> list[int]:
    base, extra = divmod(n, cols)
    return [base + (1 if i < extra else 0) for i in range(cols)]


def build_layout(spec: ArchitectureSpec) -> Layout:
    warnings: list[str] = []
    ranks = compute_ranks(spec)
    order_index = {n.id: i for i, n in enumerate(spec.nodes)}
    detail_lines = max((len(n.details) for n in spec.nodes), default=0)
    node_h = NODE_H + DETAIL_LINE_H * detail_lines
    node_w = NODE_W_DETAILED if detail_lines else NODE_W

    def sort_nodes(ns: list[Node]) -> list[Node]:
        return sorted(ns, key=lambda n: (ranks.get(n.id, 0), order_index[n.id]))

    zone_by_id = {z.id: z for z in spec.zones}
    nodes_by_zone: dict[str | None, list[Node]] = defaultdict(list)
    for n in spec.nodes:
        nodes_by_zone[n.zone].append(n)

    # Free nodes: sources go to a left column, everything else to a right column.
    free = nodes_by_zone.pop(None, [])
    in_deg = defaultdict(int)
    out_deg = defaultdict(int)
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
        _measure(r, node_w, node_h)
    total_w = sum(r.w for r in roots) + max(0, len(roots) - 1) * ZONE_GAP
    total_h = max(r.h for r in roots) if roots else node_h

    zones: list[ZoneBox] = []
    nodes: dict[str, NodeBox] = {}
    cursor = 0.0
    for r in roots:
        _place(r, cursor, 0.0, total_h, node_w, node_h, 0, zones, nodes)
        cursor += r.w + ZONE_GAP

    ax, ay, aw, ah = AREA
    scale = min(1.0, aw / total_w, ah / total_h)
    if scale < 0.72:
        warnings.append(
            f"Diagram was scaled to {scale:.0%} to fit the slide; consider fewer nodes per slide "
            "or splitting into a context diagram plus detail diagrams."
        )
    dx = ax + (aw - total_w * scale) / 2
    dy = ay + (ah - total_h * scale) / 2

    zones = [ZoneBox(z.zone, z.rect.scaled(scale, dx, dy), z.depth) for z in zones]
    nodes = {k: NodeBox(v.node, v.rect.scaled(scale, dx, dy)) for k, v in nodes.items()}
    # Ensure parents are drawn before children (they already are, by construction).
    zones.sort(key=lambda zb: zb.depth)
    return Layout(zones=zones, nodes=nodes, scale=scale, node_w=node_w * scale, node_h=node_h * scale,
                  detail_lines=detail_lines, warnings=warnings)
