"""Structured outputs each agent must produce.

These replace the hand-written JSON Schema dictionaries in
``llm.service.js``. Field descriptions are load-bearing — they are the only
instruction the model sees for that field, and several exist because the
model previously got it wrong in a specific, observed way.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

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

class Artefact(BaseModel):
    """Base for everything an agent produces and the system stores.

    Field names stay snake_case in Python and serialise to camelCase. That is
    not a style preference: the frontend, the retained JavaScript
    code-generation service, and every artifact written before this port all
    use camelCase, and both services share one Mongo collection. A snake_case
    document is not a differently-formatted document — it is one the rest of
    the system reads as absent. The port emitted `data_model` where code
    generation reads `dataModel`, so a work package arrived carrying no schema
    at all, and the requirement review screen showed "None specified" for
    every field.

    ``populate_by_name`` keeps validation tolerant of either spelling, so a
    model that answers in snake_case still parses.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Requirement(Artefact):
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
class TechChoice(Artefact):
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


class Entity(Artefact):
    name: str = Field(description="Entity name, e.g. 'Return', 'Customer'.")
    fields: list[str] = Field(description="Field names on this entity.")
    description: str


class Page(Artefact):
    page: str
    description: str


class Phase(Artefact):
    phase: str
    duration: str = Field(description="e.g. '2 weeks'.")
    description: str


class BRD(Artefact):
    """A production-ready business requirements document."""

    objective: str
    architecture: str = Field(
        description="The actual end-to-end request/data flow through the system, in prose — not a list of components."
    )
    architecture_diagram: str = Field(
        description=(
            "A valid Mermaid flowchart (starting 'flowchart TD' or 'graph LR') with a node for "
            "every component in techStack and labelled arrows for the real data flow. No code fence."
        )
    )
    tech_stack: list[TechChoice]
    user_flow: list[str] = Field(description="The real ordered steps a user takes, specific to this requirement.")
    data_model: list[Entity]
    db_schema_diagram: str = Field(
        description=(
            "A valid Mermaid erDiagram covering every entity in dataModel and the relationships "
            "between them. Mirror dataModel rather than inventing entities. No code fence."
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
class Finding(Artefact):
    section: str = Field(description="Which BRD field is wrong, e.g. 'techStack', 'dataModel'.")
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


class Critique(Artefact):
    """Fitness-for-purpose defects found in a proposed design."""

    findings: list[Finding] = Field(
        description="Empty if the design is genuinely sound. Do not invent findings to appear thorough."
    )


# ── UI ───────────────────────────────────────────────────────────────────────
# Screens are planned first and their source generated one call at a time.
# Asking for every screen's React source in a single response overflowed the
# token ceiling and truncated the JSON mid-string, which fails as a parse
# error rather than as a short answer — so the whole design was lost, not
# merely the last screen.
class ScreenOutline(Artefact):
    """One planned screen.

    Only ``name`` is required. Every other field is asked for and defaulted if
    missing, because four required fields per item is more than several models
    reliably deliver: gpt-oss-120b and gpt-oss-20b both dropped route, purpose
    and keyElements from every screen, and Groq rejects that server-side with
    a 400 — so one omission lost the entire plan and with it the whole design.
    A screen described only by its name still generates; a plan that never
    validates does not.
    """

    name: str = Field(description="PascalCase component name, e.g. 'ReturnsDashboard'.")
    route: str = Field(default="", description="URL path, e.g. '/returns'.")
    purpose: str = Field(default="", description="What this screen is for, in one line.")
    key_elements: list[str] = Field(
        default_factory=list,
        description=(
            "The concrete things on this screen — tables, forms, filters, actions — named "
            "specifically enough that its source can be written from this alone."
        ),
    )


class UIPlan(Artefact):
    """The set of screens to build, decided before any source is written."""

    screens: list[ScreenOutline]
    clarifications: list[str] = Field(
        description=(
            "Questions where the BRD is genuinely ambiguous about interface behaviour. Empty if none. "
            "These surface to the reviewer rather than being silently guessed."
        )
    )


class ScreenEditResult(Artefact):
    """One screen rewritten to satisfy a reviewer's request."""

    source: str = Field(
        description=(
            "The COMPLETE revised component, same rules as the original: plain JavaScript with "
            "JSX, no imports, no exports, and the same component name. Not a diff and not a "
            "fragment — the whole file, because it replaces the file."
        )
    )
    summary: str = Field(description="One line saying what changed, for the reviewer.")


class ScreenSource(Artefact):
    """One screen's implementation."""

    source: str = Field(
        description=(
            "A complete, self-contained React function component. Plain JavaScript with JSX, no "
            "imports and no export statement — it is assembled into a single-file preview. Every "
            "interactive element must have a real handler; every component referenced must be "
            "defined here or be another screen in this set."
        )
    )


class Screen(Artefact):
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


class UIDesign(Artefact):
    """The application interface, generated before any code."""

    screens: list[Screen]
    clarifications: list[str] = Field(
        description=(
            "Questions where the BRD is genuinely ambiguous about interface behaviour. Empty if none. "
            "These surface to the reviewer rather than being silently guessed."
        )
    )


# ── Decomposition ────────────────────────────────────────────────────────────
class OwnedEntity(Artefact):
    entity: str = Field(description="Entity name exactly as the BRD's dataModel spells it.")
    fields: list[str] = Field(description="Field names exactly as the BRD spells them.")
    owned_by_this_department: bool = Field(
        description=(
            "True if this department creates and owns the entity; false if it only reads or "
            "references one another department owns."
        )
    )


class WorkItem(Artefact):
    id: str = Field(description="Short stable identifier, e.g. 'WI-01'.")
    title: str
    department: str = Field(description=f"Owning department, one of: {', '.join(TEAM_DEPARTMENTS)}.")
    description: str
    depends_on: list[str] = Field(
        description="ids of work items that must complete first. Empty if none. Must not form a cycle."
    )


# ``plan`` and ``securityDesign`` exist because runCodeGen reads them —
# "## Implementation Plan" and "## Security Design" are sections of the prompt
# that decides which files a department generates. The port omitted both, so
# each fell back to its `|| []` default and those sections arrived empty.
class PackagePhase(Artefact):
    phase: str = Field(description="e.g. 'Phase 1: Schema and migrations'.")
    description: str
    tasks: list[str] = Field(description="Concrete pieces of work in this phase.")


class DepartmentPackage(Artefact):
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
    plan: list[PackagePhase] = Field(
        description="How this department sequences its own work, in delivery order."
    )
    security_design: list[str] = Field(
        description=(
            "Security obligations this department implements — authentication, authorisation, "
            "encryption, audit, input validation. Only what this department is responsible for."
        )
    )
    dependencies: list[str] = Field(
        description="What this department needs from another, naming the specific interface or entity."
    )


class Decomposition(Artefact):
    """An approved BRD broken into dependency-ordered work items and department packages."""

    work_items: list[WorkItem]
    packages: list[DepartmentPackage] = Field(description="One per department that has real work. Skip the rest.")


# ── Conversational edits ─────────────────────────────────────────────────────
class EditOperation(Artefact):
    target: str = Field(description="'title', 'requirement', or 'detailedReport'.")
    path: str = Field(
        default="",
        description=(
            "Dot path to the field within the target, e.g. 'objective' or "
            "'techStack.0.choice'. Empty string replaces the whole target. "
            "Intermediate keys must already exist."
        ),
    )
    value: Any = Field(description="The replacement value for that path.")


class FsdEdit(Artefact):
    """The requested changes to a requirement or its BRD, as small path-based replacements."""

    change_summary: str = Field(description="One or two sentences describing what changed, for the chat reply.")
    operations: list[EditOperation] = Field(
        description="At least one operation. Change only what was asked for; leave everything else untouched."
    )


class TeamReportEdit(Artefact):
    """The revised department package after a team lead's requested change."""

    change_summary: str = Field(description="One or two sentences describing what changed.")
    updated_package: DepartmentPackage = Field(
        description="The COMPLETE revised package. The team field must not change."
    )
