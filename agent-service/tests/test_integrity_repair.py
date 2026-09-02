"""A defective work-item graph is repaired, not shipped.

Every integrity check already existed and ran after every split — ownership,
spelling consistency, cycles, dangling edges. All four were computed, stored
on the artefact, logged as errors, and then the package published regardless.
A real split carried eleven dangling edges pointing at a work item that never
existed, straight into the graph that feeds code generation.

``dependency_cycles`` said as much in its own docstring — "it must block the
gate rather than surface at build time" — and nothing acted on it.
"""

from __future__ import annotations

import pytest

from app import invariants
from app.schemas import Decomposition, WorkItem


def _decomposition(items: list[tuple[str, list[str]]]) -> Decomposition:
    return Decomposition(
        work_items=[
            WorkItem(id=i, title=f"Item {i}", department="Development",
                     description=f"Work item {i}.", depends_on=deps)
            for i, deps in items
        ],
        packages=[],
    )


class TestDanglingEdges:
    def test_an_edge_to_a_missing_item_is_dropped(self):
        d = _decomposition([("WI-01", []), ("WI-02", ["WI-99"])])
        repairs = invariants.repair_dependencies(d)

        assert repairs["dropped_dangling"] == ["WI-02 -> WI-99"]
        assert invariants.dangling_dependencies(d) == []

    def test_real_edges_survive(self):
        """Repair must not cost ordering that was correct."""
        d = _decomposition([("WI-01", []), ("WI-02", ["WI-01", "WI-99"])])
        invariants.repair_dependencies(d)

        item = next(i for i in d.work_items if i.id == "WI-02")
        assert item.depends_on == ["WI-01"]

    def test_the_observed_failure(self):
        """Eleven edges pointing at one non-existent item — the shape that
        actually shipped."""
        d = _decomposition(
            [("WI-01", []), *[(f"WI-{n:02d}", ["WI-03"]) for n in range(4, 15)]]
        )
        assert len(invariants.dangling_dependencies(d)) == 11

        repairs = invariants.repair_dependencies(d)
        assert len(repairs["dropped_dangling"]) == 11
        assert invariants.dangling_dependencies(d) == []


class TestCycles:
    def test_a_cycle_is_broken(self):
        d = _decomposition([("A", ["C"]), ("B", ["A"]), ("C", ["B"])])
        assert invariants.dependency_cycles(d)

        repairs = invariants.repair_dependencies(d)

        assert repairs["broke_cycles"]
        assert invariants.dependency_cycles(d) == []

    def test_only_what_is_needed_is_removed(self):
        """One edge per pass, re-detecting each time — breaking one edge can
        resolve several overlapping cycles, and dropping one per reported
        cycle would remove more ordering than necessary."""
        d = _decomposition([("A", ["C"]), ("B", ["A"]), ("C", ["B"])])
        repairs = invariants.repair_dependencies(d)
        assert len(repairs["broke_cycles"]) == 1

    def test_a_self_dependency_is_a_cycle(self):
        d = _decomposition([("A", ["A"])])
        invariants.repair_dependencies(d)
        assert invariants.dependency_cycles(d) == []

    def test_a_healthy_graph_is_left_alone(self):
        d = _decomposition([("A", []), ("B", ["A"]), ("C", ["B"])])
        repairs = invariants.repair_dependencies(d)

        assert repairs == {"dropped_dangling": [], "broke_cycles": []}
        assert next(i for i in d.work_items if i.id == "C").depends_on == ["B"]


class TestBothAtOnce:
    def test_dangling_edges_go_first(self):
        """A dangling edge is skipped by cycle detection, so removing it can
        expose a cycle that was hidden behind it."""
        d = _decomposition([("A", ["B", "GONE"]), ("B", ["A"])])
        repairs = invariants.repair_dependencies(d)

        assert repairs["dropped_dangling"] == ["A -> GONE"]
        assert repairs["broke_cycles"]
        assert invariants.dangling_dependencies(d) == []
        assert invariants.dependency_cycles(d) == []


class TestWhatTheReviewerIsTold:
    def test_repairs_are_reported_separately_from_defects(self):
        from app.routes.artifacts import _integrity_summary

        summary = _integrity_summary(
            {
                "unowned": [],
                "multiply_owned": [],
                "inconsistent_spelling": [],
                "cycles": [],
                "dangling": [],
                "repairs": {"dropped_dangling": ["WI-04 -> WI-03"], "broke_cycles": []},
            }
        )

        assert "All checks passed" in summary
        assert "Repaired automatically" in summary
        assert "WI-04 -> WI-03" in summary

    def test_a_clean_split_says_nothing_about_repairs(self):
        from app.routes.artifacts import _integrity_summary

        summary = _integrity_summary({"repairs": {"dropped_dangling": [], "broke_cycles": []}})
        assert "Repaired automatically" not in summary
