"""Structured outputs each agent must produce.

These replace the hand-written JSON Schema dictionaries in
``llm.service.js``. Field descriptions are load-bearing — they are the only
instruction the model sees for that field, and several exist because the
model previously got it wrong in a specific, observed way.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .config import TEAM_DEPARTMENTS

# Shared guidance. A named product that does not exist is a worse failure
# than a vague one, because it survives review and only breaks at build time.
_REAL_TECHNOLOGY = (
    "Name ONE specific, real technology or vendor for this layer. If the requirement stated "
    "no preference, choose the single best-fit established option yourself rather than listing "
    "alternatives. Never name a product you are not confident exists — prefer a major cloud "
    "vendor's real equivalent over a boutique product you are unsure about."
)


# ── Intake ───────────────────────────────────────────────────────────────────
class Requirement(BaseModel):
    """The finished intake conversation, structured for approval."""

    title: str = Field(description="Short title, under 80 characters.")
    summary: str = Field(description="2-4 sentence plain-language summary of what is being built and why.")
    in_scope: list[str] = Field(description="Concrete capabilities included.")
    out_of_scope: list[str] = Field(description="Explicit exclusions, to prevent scope creep.")
    functional_requirements: list[str]
    non_functional_requirements: list[str] = Field(
        description="Performance, availability, compliance and data-sensitivity constraints."
    )
    preferred_code_gen_model: str = Field(
        description=(
            "Which model the requester wants used to GENERATE CODE — not a feature of their "
            "product. Use their stated preference, or 'No preference' if they gave none."
        )
    )
    open_questions: list[str] = Field(description="Anything unresolved for the approver to weigh in on.")


# ── BRD ──────────────────────────────────────────────────────────────────────
class TechChoice(BaseModel):
    layer: str = Field(description="e.g. 'Frontend', 'Backend/API', 'Database', 'Hosting', 'Auth', 'CI/CD'.")
    choice: str = Field(description=_REAL_TECHNOLOGY)
    rationale: str = Field(
        description=(
            "Three short sentences: why this fits THIS requirement; the leading alternative "
            "considered and why it was rejected; the main limitation of the choice in this "
            "operating environment. A rationale that only praises the choice is incomplete — "
            "every real engineering decision has a trade-off."
        )
    )


class Entity(BaseModel):
    name: str = Field(description="Entity name, e.g. 'Return', 'Customer'.")
    fields: list[str] = Field(description="Field names on this entity.")
    description: str


class Page(BaseModel):
    page: str
    description: str


class Phase(BaseModel):
    phase: str
    duration: str = Field(description="e.g. '2 weeks'.")
    description: str


class BRD(BaseModel):
    """A production-ready business requirements document."""

    objective: str
    architecture: str = Field(
        description="The actual end-to-end request/data flow through the system, in prose — not a list of components."
    )
    architecture_diagram: str = Field(
        description=(
            "A valid Mermaid flowchart (starting 'flowchart TD' or 'graph LR') with a node for "
            "every component in tech_stack and labelled arrows for the real data flow. No code fence."
        )
    )
    tech_stack: list[TechChoice]
    user_flow: list[str] = Field(description="The real ordered steps a user takes, specific to this requirement.")
    data_model: list[Entity]
    db_schema_diagram: str = Field(
        description=(
            "A valid Mermaid erDiagram covering every entity in data_model and the relationships "
            "between them. Mirror data_model rather than inventing entities. No code fence."
        )
    )
    page_behavior: list[Page]
    security_design: list[str]
    deployment_and_operations: list[str] = Field(
        description="Environments, CI/CD, monitoring, backup, rollback — how it runs in production, not how it is built."
    )
    timeline: list[Phase]
    assumptions: list[str]
    open_questions: list[str]


# ── Design critique ──────────────────────────────────────────────────────────
class Finding(BaseModel):
    section: str = Field(description="Which BRD field is wrong, e.g. 'tech_stack', 'data_model'.")
    severity: str = Field(
        description=(
            "'high' — the design fails at its core job, misses a governing standard, or omits a "
            "safety/compliance obligation. 'medium' — it works but a materially better-fitting "
            "approach exists, or a promised capability is only partly delivered. 'low' — cosmetic; "
            "these are ignored."
        )
    )
    issue: str = Field(description="The defect and the concrete circumstance under which it bites.")
    recommendation: str = Field(description="The specific change, naming the replacement approach or component.")


class Critique(BaseModel):
    """Fitness-for-purpose defects found in a proposed design."""

    findings: list[Finding] = Field(
        description="Empty if the design is genuinely sound. Do not invent findings to appear thorough."
    )


# ── UI ───────────────────────────────────────────────────────────────────────
class Screen(BaseModel):
    name: str = Field(description="PascalCase component name, e.g. 'ReturnsDashboard'.")
    route: str = Field(description="URL path, e.g. '/returns'.")
    purpose: str
    source: str = Field(
        description=(
            "A complete, self-contained React function component. Plain JavaScript with JSX, no "
            "imports and no export statement — it is assembled into a single-file preview. Every "
            "interactive element must have a real handler; every component referenced must be "
            "defined here or be another screen in this set."
        )
    )


class UIDesign(BaseModel):
    """The application interface, generated before any code."""

    screens: list[Screen]
    clarifications: list[str] = Field(
        description=(
            "Questions where the BRD is genuinely ambiguous about interface behaviour. Empty if none. "
            "These surface to the reviewer rather than being silently guessed."
        )
    )


# ── Decomposition ────────────────────────────────────────────────────────────
class OwnedEntity(BaseModel):
    entity: str = Field(description="Entity name exactly as the BRD's data_model spells it.")
    fields: list[str] = Field(description="Field names exactly as the BRD spells them.")
    owned_by_this_department: bool = Field(
        description=(
            "True if this department creates and owns the entity; false if it only reads or "
            "references one another department owns."
        )
    )


class WorkItem(BaseModel):
    id: str = Field(description="Short stable identifier, e.g. 'WI-01'.")
    title: str
    department: str = Field(description=f"Owning department, one of: {', '.join(TEAM_DEPARTMENTS)}.")
    description: str
    depends_on: list[str] = Field(
        description="ids of work items that must complete first. Empty if none. Must not form a cycle."
    )


class DepartmentPackage(BaseModel):
    team: str = Field(description=f"Exactly one of: {', '.join(TEAM_DEPARTMENTS)}.")
    objective: str
    architecture: str = Field(description="The slice of the system this department owns, in prose.")
    tech_stack: list[TechChoice] = Field(description="Only layers this department is responsible for.")
    data_model: list[OwnedEntity] = Field(
        description=(
            "Every entity this department reads or writes, including ones another department owns. "
            "Copy each VERBATIM from the BRD — identical name, identical field names, character for "
            "character. Departments generate code independently and never see each other's output, "
            "so these names are the only thing making the finished modules fit together."
        )
    )
    dependencies: list[str] = Field(
        description="What this department needs from another, naming the specific interface or entity."
    )


class Decomposition(BaseModel):
    """An approved BRD broken into dependency-ordered work items and department packages."""

    work_items: list[WorkItem]
    packages: list[DepartmentPackage] = Field(description="One per department that has real work. Skip the rest.")


# ── Conversational edits ─────────────────────────────────────────────────────
class EditOperation(BaseModel):
    target: str = Field(description="'title', 'requirement', or 'detailedReport'.")
    path: str = Field(
        default="",
        description=(
            "Dot path to the field within the target, e.g. 'objective' or "
            "'tech_stack.0.choice'. Empty string replaces the whole target. "
            "Intermediate keys must already exist."
        ),
    )
    value: Any = Field(description="The replacement value for that path.")


class FsdEdit(BaseModel):
    """The requested changes to a requirement or its BRD, as small path-based replacements."""

    change_summary: str = Field(description="One or two sentences describing what changed, for the chat reply.")
    operations: list[EditOperation] = Field(
        description="At least one operation. Change only what was asked for; leave everything else untouched."
    )


class TeamReportEdit(BaseModel):
    """The revised department package after a team lead's requested change."""

    change_summary: str = Field(description="One or two sentences describing what changed.")
    updated_package: DepartmentPackage = Field(
        description="The COMPLETE revised package. The team field must not change."
    )
