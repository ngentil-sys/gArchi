"""Architecture specification produced by the agent and consumed by the renderer."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ZoneKind = Literal[
    "cloud",      # Google Cloud organization / boundary (outermost)
    "project",    # Google Cloud project
    "region",     # Region / zone grouping
    "network",    # VPC / subnet
    "data",       # Data / storage / analytics tier
    "security",   # Security / identity / governance services
    "operations", # Observability, CI/CD, management
    "external",   # Internet, users, SaaS, other clouds
    "onprem",     # On-premises / colocation
    "generic",
]

EdgeStyle = Literal["solid", "dashed", "dotted"]

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class Zone(BaseModel):
    """A labelled container (boundary) drawn around nodes and/or child zones."""

    id: str = Field(description="Short snake_case identifier, e.g. 'vpc_prod'.")
    label: str = Field(description="Display label, e.g. 'VPC: prod-vpc'.")
    detail: str | None = Field(
        default=None, max_length=48,
        description="Second line with low-level facts, e.g. 'us-central1 · 10.10.0.0/16' or 'project: acme-prod-01'.",
    )
    kind: ZoneKind = "generic"
    parent: str | None = Field(default=None, description="Id of the enclosing zone, if nested.")

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(f"zone id '{v}' must match {_ID_RE.pattern}")
        return v


class Node(BaseModel):
    """A product/component drawn as an icon card."""

    id: str = Field(description="Short snake_case identifier unique in the diagram, e.g. 'api_run'.")
    label: str = Field(description="Primary label, usually the product name, e.g. 'Cloud Run'.")
    sublabel: str | None = Field(default=None, description="Role in this design, e.g. 'Orders API'.")
    product: str | None = Field(
        default=None,
        description="Catalog product id (see list_gcp_products), e.g. 'cloud_run'. Drives icon and docs link.",
    )
    icon: str | None = Field(default=None, description="Optional explicit icon id override.")
    zone: str | None = Field(default=None, description="Id of the zone that contains this node.")
    details: list[str] = Field(
        default_factory=list, max_length=3,
        description="Up to 3 short technical lines (<= 20 chars each) shown on the card in monospace: "
                    "region/zone, IP or CIDR, machine type, tier/edition, instance counts, ports. "
                    "e.g. ['us-central1', '10.10.1.0/24', 'e2-standard-4 x3'].",
    )

    @field_validator("details")
    @classmethod
    def _short_details(cls, v: list[str]) -> list[str]:
        for d in v:
            if len(d) > 20:
                raise ValueError(f"detail line too long ({len(d)} > 20 chars): '{d}'")
        return v

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(f"node id '{v}' must match {_ID_RE.pattern}")
        return v


class Edge(BaseModel):
    source: str
    target: str
    label: str | None = Field(default=None, max_length=28, description="Short protocol/flow label, e.g. 'HTTPS :443', 'CDC', 'BGP 169.254.0.1'.")
    style: EdgeStyle = "solid"
    bidirectional: bool = False


class Component(BaseModel):
    """Row in the component table."""

    name: str
    product: str | None = None
    role: str = Field(description="What this component does in the architecture.")
    key_config: str | None = Field(default=None, description="Notable configuration (edition, tier, HA, region...).")


class Consideration(BaseModel):
    pillar: str = Field(description="Architecture Framework pillar, e.g. 'Security, privacy & compliance'.")
    points: list[str] = Field(description="2-5 concise bullet points.")


class Reference(BaseModel):
    title: str
    url: str


class ArchitectureSpec(BaseModel):
    title: str = Field(description="Deck/diagram title, e.g. 'Retail Order Platform on Google Cloud'.")
    subtitle: str | None = Field(default=None, description="One-line summary shown on the cover.")
    customer: str | None = None
    zones: list[Zone] = Field(default_factory=list)
    nodes: list[Node]
    edges: list[Edge] = Field(default_factory=list)
    components: list[Component] = Field(default_factory=list)
    considerations: list[Consideration] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)
    notes: str | None = Field(default=None, description="Free-form design notes for the speaker notes.")

    @model_validator(mode="after")
    def _check_refs(self) -> "ArchitectureSpec":
        zone_ids = {z.id for z in self.zones}
        node_ids = [n.id for n in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            dupes = sorted({i for i in node_ids if node_ids.count(i) > 1})
            raise ValueError(f"duplicate node ids: {dupes}")
        if len(zone_ids) != len(self.zones):
            raise ValueError("duplicate zone ids")
        for z in self.zones:
            if z.parent and z.parent not in zone_ids:
                raise ValueError(f"zone '{z.id}' has unknown parent '{z.parent}'")
            if z.parent == z.id:
                raise ValueError(f"zone '{z.id}' cannot be its own parent")
        for n in self.nodes:
            if n.zone and n.zone not in zone_ids:
                raise ValueError(f"node '{n.id}' references unknown zone '{n.zone}'")
        nid = set(node_ids)
        for e in self.edges:
            if e.source not in nid:
                raise ValueError(f"edge source '{e.source}' is not a node id")
            if e.target not in nid:
                raise ValueError(f"edge target '{e.target}' is not a node id")
        if not self.nodes:
            raise ValueError("at least one node is required")
        return self


def example_spec() -> dict:
    """Compact example used in the agent instruction and tests."""
    return {
        "title": "Serverless Web Application on Google Cloud",
        "subtitle": "Globally load-balanced Cloud Run API with Cloud SQL, Memorystore and Pub/Sub analytics",
        "zones": [
            {"id": "ext", "label": "Clients", "kind": "external"},
            {"id": "gcp", "label": "Google Cloud: project prod-web", "detail": "org: acme.example · folder: prod", "kind": "project"},
            {"id": "edge", "label": "Global edge", "detail": "anycast 34.120.10.5", "kind": "network", "parent": "gcp"},
            {"id": "vpc", "label": "VPC: prod-vpc", "detail": "us-central1 · 10.10.0.0/16", "kind": "network", "parent": "gcp"},
            {"id": "data", "label": "Data & analytics", "detail": "US multi-region", "kind": "data", "parent": "gcp"},
            {"id": "ops", "label": "Security & operations", "kind": "operations", "parent": "gcp"},
        ],
        "nodes": [
            {"id": "users", "label": "Users", "product": "users", "zone": "ext", "details": ["web + mobile"]},
            {"id": "lb", "label": "Cloud Load Balancing", "sublabel": "Global external HTTPS", "product": "cloud_load_balancing", "zone": "edge", "details": ["34.120.10.5 :443", "TLS 1.2+ / managed"]},
            {"id": "armor", "label": "Cloud Armor", "sublabel": "WAF + DDoS", "product": "cloud_armor", "zone": "edge", "details": ["OWASP CRS 3.3", "100 rps/IP limit"]},
            {"id": "cdn", "label": "Cloud CDN", "product": "cloud_cdn", "zone": "edge", "details": ["CACHE_ALL_STATIC"]},
            {"id": "api", "label": "Cloud Run", "sublabel": "Web API", "product": "cloud_run", "zone": "vpc", "details": ["us-central1", "2 vCPU / 1 GiB", "min 1 · max 50"]},
            {"id": "cache", "label": "Memorystore", "sublabel": "Redis cache", "product": "memorystore", "zone": "vpc", "details": ["10.10.3.4 :6379", "STANDARD_HA 5 GB"]},
            {"id": "db", "label": "Cloud SQL", "sublabel": "PostgreSQL HA", "product": "cloud_sql", "zone": "vpc", "details": ["10.10.2.5 (private)", "db-custom-4-16384", "regional HA · PITR"]},
            {"id": "topic", "label": "Pub/Sub", "sublabel": "Events", "product": "pubsub", "zone": "data", "details": ["7 d retention"]},
            {"id": "bq", "label": "BigQuery", "sublabel": "Analytics", "product": "bigquery", "zone": "data", "details": ["dataset: events", "partition: day"]},
            {"id": "iam", "label": "IAM", "product": "iam", "zone": "ops", "details": ["sa-api@ · sa-etl@"]},
            {"id": "mon", "label": "Cloud Monitoring", "product": "cloud_monitoring", "zone": "ops", "details": ["SLO 99.9% avail"]},
        ],
        "edges": [
            {"source": "users", "target": "lb", "label": "HTTPS :443"},
            {"source": "lb", "target": "api", "label": "serverless NEG"},
            {"source": "armor", "target": "lb", "style": "dashed", "label": "policy"},
            {"source": "cdn", "target": "lb", "style": "dashed"},
            {"source": "api", "target": "cache", "label": "TCP 6379"},
            {"source": "api", "target": "db", "label": "private IP :5432"},
            {"source": "api", "target": "topic", "label": "publish"},
            {"source": "topic", "target": "bq", "label": "BQ subscription"},
        ],
        "components": [
            {"name": "Cloud Run", "product": "cloud_run", "role": "Stateless web API", "key_config": "min 1 / max 50 instances, Direct VPC egress"},
            {"name": "Cloud SQL", "product": "cloud_sql", "role": "System of record", "key_config": "PostgreSQL 16, Enterprise Plus, regional HA, PITR"},
        ],
        "considerations": [
            {"pillar": "Security, privacy & compliance", "points": ["Cloud Armor WAF policy on the external ALB", "Private IP only for Cloud SQL; IAM database auth"]},
            {"pillar": "Reliability", "points": ["Regional HA Cloud SQL with automated failover", "Cloud Run multi-zone by default; add a second region behind the global ALB for DR"]},
        ],
        "references": [
            {"title": "Cloud Run documentation", "url": "https://cloud.google.com/run/docs"},
        ],
    }
