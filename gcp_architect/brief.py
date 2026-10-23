"""Customer brief: the structured engagement context captured in the GUI and injected into the agent."""

from __future__ import annotations

from pydantic import BaseModel, Field

STATE_BRIEF = "brief"


class CustomerBrief(BaseModel):
    # Who
    customer: str = Field(default="", description="Customer / account name")
    industry: str = Field(default="", description="Industry or vertical")
    stakeholders: str = Field(default="", description="Who we are designing for (CTO, platform team, ...)")
    # Why
    objectives: str = Field(default="", description="Business goals, problems to solve, success criteria")
    # What (requirements & constraints)
    workloads: str = Field(default="", description="Applications / workloads in scope")
    scale: str = Field(default="", description="Users, TPS, data volumes, growth")
    availability: str = Field(default="", description="SLA / SLO, RTO / RPO, DR expectations")
    performance: str = Field(default="", description="Latency / throughput targets")
    compliance: str = Field(default="", description="PCI, HIPAA, GDPR, SOC 2, FedRAMP, data residency ...")
    regions: str = Field(default="", description="Required / preferred Google Cloud regions")
    budget: str = Field(default="", description="Budget sensitivity, commitments, cost constraints")
    # Current state
    current_infra: str = Field(default="", description="On-prem, other clouds, hosting, platforms")
    network: str = Field(default="", description="CIDRs, connectivity (VPN / Interconnect), DNS, firewalls")
    identity: str = Field(default="", description="IdP (Okta, Entra ID, AD), SSO, CIAM")
    data: str = Field(default="", description="Databases, data stores, volumes, retention")
    tooling: str = Field(default="", description="CI/CD, IaC, observability, ticketing")
    constraints: str = Field(default="", description="Technical, organisational or contractual constraints")
    # Preferences
    preferences: str = Field(default="", description="Naming conventions, preferred products, things to avoid")
    notes: str = Field(default="", description="Free-form notes, pasted discovery-call notes")

    def is_empty(self) -> bool:
        return not any(v.strip() for v in self.model_dump().values())

    def to_markdown(self) -> str:
        sections = [
            ("Customer", [("Customer", self.customer), ("Industry", self.industry), ("Stakeholders", self.stakeholders)]),
            ("Objectives & problems", [("", self.objectives)]),
            ("Requirements & constraints", [
                ("Workloads", self.workloads), ("Scale", self.scale), ("Availability / DR", self.availability),
                ("Performance", self.performance), ("Compliance", self.compliance), ("Regions", self.regions),
                ("Budget", self.budget), ("Constraints", self.constraints),
            ]),
            ("Current infrastructure", [
                ("Hosting / clouds", self.current_infra), ("Network", self.network), ("Identity", self.identity),
                ("Data", self.data), ("Tooling", self.tooling),
            ]),
            ("Preferences", [("", self.preferences)]),
            ("Notes", [("", self.notes)]),
        ]
        out: list[str] = []
        for title, rows in sections:
            rows = [(k, v.strip()) for k, v in rows if v and v.strip()]
            if not rows:
                continue
            out.append(f"### {title}")
            for k, v in rows:
                if k:
                    out.append(f"- **{k}:** {v}")
                else:
                    out.append(v)
            out.append("")
        return "\n".join(out).strip()


def brief_from_state(state) -> CustomerBrief | None:
    raw = state.get(STATE_BRIEF) if state is not None else None
    if not raw:
        return None
    try:
        b = CustomerBrief.model_validate(raw)
    except Exception:  # noqa: BLE001
        return None
    return None if b.is_empty() else b
