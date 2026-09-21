"""Workflow templates — which stages a run performs, and which gates fire.

The client picks one at intake and it is fixed for the run. The architecture is
explicit about why: *a workflow that changes shape mid-run defeats the audit
trail.* A template chosen per gate, or negotiated as the work proceeds, means
the record of what happened cannot be compared against what was supposed to
happen — because there was never a fixed answer to the second half.

**The rule that stops this becoming a way to skip governance.** A template
decides which *stages* apply. It does not decide whether a stage's gate is
optional: if a stage runs, its gate fires, and every gate that fires is
mandatory and attested. Gates keep their canonical numbers throughout — a run
that does not perform UAT fires gates 1 to 5 and 7, not "gates 1 to 6
renumbered". Renumbering would make two runs incomparable in the audit trail,
which is the thing the templates exist to protect.

**Screens are never traded away.** A template may reduce UI *scope* — a
brownfield change regenerates only the screens it touches — but never its
existence. Screens are what the business actually approves, so the stage that
produces them cannot be the one dropped for speed. Hotfix is the single
exception, and only because a production correction that changes no screen has
none to generate.

**One reading recorded rather than assumed.** The architecture describes hotfix
as *"abbreviated scope → build/test/scan → merge gate only → expedited UAT →
release, shortened SLA"*. "Merge gate only" is taken here to mean *the only gate
before UAT is merge* — so gates 5, 6 and 7 fire and gates 1 to 4 do not. The
alternative reading, that merge is the only gate at all, contradicts the
sentence that follows it. This is flagged in ``AMBIGUOUS`` below so it can be
settled deliberately rather than discovered in an audit.
"""

from __future__ import annotations

from dataclasses import dataclass

import gate_engine

# Stages, in the order a full run performs them. A template's stage set is a
# subset of these; the order never varies.
STAGES = (
    "scope",
    "brd",
    "design",
    "plan",
    "build",
    "uat_deploy",
    "release",
)

# Which gate closes which stage. A stage that runs fires its gate; a stage that
# does not run fires nothing. There is no third case, which is what makes "a
# template cannot skip a gate" checkable rather than aspirational.
STAGE_GATES: dict[str, int] = {
    "scope": 1,
    "brd": 2,
    "design": 3,
    "plan": 4,
    "build": 5,
    "uat_deploy": 6,
    "release": 7,
}


@dataclass(frozen=True)
class Template:
    """One named, versioned way of running the pipeline.

    ``ui_scope`` is the concession a template is allowed to make on screens:
    every screen, only the ones a change touches, or — for hotfix alone — none,
    because there are none. It is deliberately not a boolean: "skip the UI" is
    not a state a template may reach by accident.

    ``sla_multiplier`` scales every gate's response window. Hotfix shortens
    them; it does not remove them, because an unbounded wait on a production
    incident is the failure the timer exists to prevent.
    """

    name: str
    stages: tuple[str, ...]
    ui_scope: str  # "all" | "changed_only" | "none"
    dev_streams: str  # "per_department" | "single"
    sla_multiplier: float
    applies_when: str
    notes: str = ""

    @property
    def gates(self) -> tuple[int, ...]:
        """The gates this run fires, in canonical numbering.

        Derived from the stages rather than declared alongside them. Two lists
        that must agree are two lists that can disagree, and the failure would
        be a gate quietly not firing.
        """
        return tuple(STAGE_GATES[s] for s in self.stages if s in STAGE_GATES)

    def runs(self, stage: str) -> bool:
        return stage in self.stages

    def fires(self, gate: int) -> bool:
        return gate in self.gates


TEMPLATES: dict[str, Template] = {
    "full_governance": Template(
        name="full_governance",
        stages=STAGES,
        ui_scope="all",
        dev_streams="per_department",
        sla_multiplier=1.0,
        applies_when="A new client-facing application. The default for a first engagement.",
    ),
    "brownfield_change": Template(
        name="brownfield_change",
        stages=STAGES,
        ui_scope="changed_only",
        dev_streams="per_department",
        sla_multiplier=1.0,
        applies_when="A feature added to an existing, already-approved system.",
        notes=(
            "Fires every gate. What differs is scope, not ceremony: the BRD is a delta "
            "against the approved one, and only the screens the change touches are "
            "regenerated."
        ),
    ),
    "internal_tool": Template(
        name="internal_tool",
        # UAT deploy is folded into release: the business reviewer who would sign
        # UAT is the one approving release on a tool this size, and asking the
        # same person twice is ceremony rather than control.
        stages=("scope", "brd", "design", "plan", "build", "release"),
        ui_scope="all",
        dev_streams="single",
        sla_multiplier=1.0,
        applies_when="Low-risk internal tooling.",
        notes=(
            "Six gates — 1 to 5 and 7. Screens are still generated and still approved: "
            "the concessions are a single development stream and a folded UAT gate."
        ),
    ),
    "hotfix": Template(
        name="hotfix",
        stages=("build", "uat_deploy", "release"),
        ui_scope="none",
        dev_streams="single",
        # Shortened, never removed. A production incident with no response
        # deadline is the failure the timer exists to prevent.
        sla_multiplier=0.25,
        applies_when="Production incident correction.",
        notes=(
            "Still fully attested, and never bypasses the merge gate. Scope is "
            "abbreviated and captured on the requirement rather than gated, because a "
            "correction that has to wait for a scope approval is not a hotfix."
        ),
    ),
}

# Readings of the specification that are defensible but not certain. Recorded
# here rather than buried in a comment so they can be settled deliberately.
AMBIGUOUS: dict[str, str] = {
    "hotfix.gates": (
        "The architecture says 'merge gate only → expedited UAT → release'. Read here as "
        "'merge is the only gate before UAT', so gates 5, 6 and 7 fire. The narrower "
        "reading — merge alone — would leave a production release ungated, which the "
        "same paragraph's 'still fully attested' contradicts."
    ),
}


class UnknownTemplate(Exception):
    """A template name that does not exist, refused rather than defaulted.

    Defaulting to full governance would be the safe-looking choice and the wrong
    one: a typo would silently produce a different run shape from the one
    someone selected, and the audit trail would record the shape rather than the
    mistake.
    """


def for_name(name: str) -> Template:
    try:
        return TEMPLATES[name]
    except KeyError:
        raise UnknownTemplate(
            f"no workflow template named '{name}'; available: {', '.join(sorted(TEMPLATES))}"
        ) from None


def approval_chain(name: str) -> dict[str, list[str]]:
    """The roles entitled to act at each gate this template fires.

    Resolved once at intake and written onto the requirement, so a mid-flight
    change to a group cannot rewrite who was allowed to approve a decision
    already in progress.
    """
    return gate_engine.resolve_approval_chain({}, list(for_name(name).gates))


def sla_for(name: str, base_hours: float) -> float:
    """A gate's response window under this template."""
    return base_hours * for_name(name).sla_multiplier
