"""The Run Ledger — what connects a line of code back to a decision.

The architecture's traceability claim is that asking *"why does this exist?"* of
anything the pipeline produced resolves to a numbered requirement a named person
approved. That claim needs a graph: requirement → ticket → test → commit →
approval, traversable in both directions.

**Both directions matter, and for different people.** Forwards answers *"is this
requirement actually covered?"* — the question at a gate. Backwards answers *"why
is this line here, and who signed for it?"* — the question in an audit, or six
months later when nobody remembers. A structure that only walks one way answers
half of them.

**Append-only, because it is the audit trail.** Links are added and never
removed. A requirement that was descoped keeps its edges; the fact that work was
done and then dropped is part of the record, and a ledger that can forget is a
ledger whose silence proves nothing.

**It stores references, not content.** A commit SHA rather than a diff, a
requirement id rather than a BRD. Git already holds the artefacts, and a second
copy is a second thing that can disagree with the first.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterator, Literal

NodeKind = Literal["requirement", "ticket", "test", "commit", "approval"]

# The shape the architecture names: requirement to ticket, test, commit,
# approval. Declared rather than inferred, so an edge nobody intended is
# refused instead of quietly extending the model.
ALLOWED_EDGES: frozenset[tuple[str, str]] = frozenset(
    {
        ("requirement", "ticket"),
        ("requirement", "approval"),
        ("ticket", "test"),
        ("ticket", "commit"),
        ("test", "commit"),
        ("commit", "approval"),
    }
)


class UnknownRelation(Exception):
    """An edge the traceability model does not define.

    Refused rather than accepted, because a ledger that accepts any edge can
    answer any question and prove none of them: the value of "this commit traces
    to that requirement" depends entirely on the path being one the model
    intends.
    """


@dataclass(frozen=True)
class Node:
    kind: NodeKind
    id: str

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.kind}:{self.id}"


@dataclass
class Ledger:
    """An append-only traceability graph for one requirement's run."""

    _forward: dict[Node, set[Node]] = field(default_factory=lambda: defaultdict(set))
    _back: dict[Node, set[Node]] = field(default_factory=lambda: defaultdict(set))

    def link(self, parent: Node, child: Node) -> None:
        """Record that ``child`` exists because of ``parent``."""
        if (parent.kind, child.kind) not in ALLOWED_EDGES:
            raise UnknownRelation(
                f"the ledger does not model {parent.kind} → {child.kind}; "
                f"defined relations: "
                + ", ".join(f"{a} → {b}" for a, b in sorted(ALLOWED_EDGES))
            )
        self._forward[parent].add(child)
        self._back[child].add(parent)

    # ── forwards: is this requirement covered? ───────────────────────────────

    def children(self, node: Node) -> set[Node]:
        return set(self._forward.get(node, set()))

    def descendants(self, node: Node) -> set[Node]:
        """Everything produced because of this node, at any depth."""
        return set(self._walk(node, self._forward))

    def covered_by(self, node: Node, kind: NodeKind) -> set[Node]:
        """Descendants of one kind — 'which tests cover this requirement?'"""
        return {n for n in self.descendants(node) if n.kind == kind}

    # ── backwards: why does this exist, and who signed? ──────────────────────

    def parents(self, node: Node) -> set[Node]:
        return set(self._back.get(node, set()))

    def ancestors(self, node: Node) -> set[Node]:
        return set(self._walk(node, self._back))

    def traces_to(self, node: Node) -> set[Node]:
        """The requirements this node exists because of.

        The answer to *"why does this line of code exist?"* — and if it comes
        back empty for a commit, that commit reached the repository without a
        requirement behind it, which is itself the finding.
        """
        return {n for n in self.ancestors(node) if n.kind == "requirement"}

    def approvals_for(self, node: Node) -> set[Node]:
        """Who signed for this, anywhere in its history."""
        return {n for n in self.ancestors(node) | self.descendants(node) if n.kind == "approval"}

    # ── integrity ────────────────────────────────────────────────────────────

    def orphans(self, kind: NodeKind) -> set[Node]:
        """Nodes of a kind that trace to no requirement.

        A commit with no requirement behind it, or a test covering nothing, is
        exactly what the traceability claim asserts cannot exist — so the ledger
        should be able to name them rather than only assert their absence.
        """
        seen = set(self._forward) | {c for cs in self._forward.values() for c in cs}
        return {n for n in seen if n.kind == kind and not self.traces_to(n)}

    def _walk(self, start: Node, edges: dict[Node, set[Node]]) -> Iterator[Node]:
        """Breadth-first, cycle-safe.

        The model is acyclic by intent, but a ledger that hangs on malformed
        input is worse than one that returns what it can — this is audit code,
        and it runs when something has already gone wrong.
        """
        seen: set[Node] = set()
        queue = list(edges.get(start, set()))
        while queue:
            node = queue.pop(0)
            if node in seen:
                continue
            seen.add(node)
            yield node
            queue.extend(edges.get(node, set()))


def requirement(id: str) -> Node:
    return Node("requirement", id)


def ticket(id: str) -> Node:
    return Node("ticket", id)


def test(id: str) -> Node:
    return Node("test", id)


def commit(sha: str) -> Node:
    return Node("commit", sha)


def approval(gate: int, requirement_id: str) -> Node:
    """One gate decision, addressed the way the attestation path is."""
    return Node("approval", f"{requirement_id}/gate-{gate}")
