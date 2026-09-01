from app.invariants import (
    dangling_dependencies,
    dependency_cycles,
    entity_ownership_report,
    normalize_entity_ownership,
)
from app.schemas import Decomposition, DepartmentPackage, OwnedEntity, WorkItem


def _pkg(team: str, entities: list[tuple[str, bool]]) -> DepartmentPackage:
    return DepartmentPackage(
        team=team,
        objective="o",
        architecture="a",
        tech_stack=[],
        data_model=[OwnedEntity(entity=e, fields=["id"], owned_by_this_department=owned) for e, owned in entities],
        plan=[],
        security_design=[],
        dependencies=[],
    )


def _decomp(packages, work_items=None) -> Decomposition:
    return Decomposition(work_items=work_items or [], packages=packages)


def _owner_of(decomposition, entity):
    return [
        p.team for p in decomposition.packages
        for e in p.data_model if e.entity == entity and e.owned_by_this_department
    ]


class TestOwnership:
    def test_unowned_entity_is_assigned(self):
        """Owned by nobody means no department generates the schema at all."""
        d = _decomp([_pkg("QA", [("Refund", False)]), _pkg("Development", [("Refund", False)])])
        assert _owner_of(normalize_entity_ownership(d), "Refund") == ["Development"]

    def test_unowned_falls_back_to_first_lister(self):
        d = _decomp([_pkg("QA", [("TestPlan", False)]), _pkg("AI", [("TestPlan", False)])])
        assert _owner_of(normalize_entity_ownership(d), "TestPlan") == ["QA"]

    def test_double_owned_is_demoted_to_one(self):
        """Two owners means two conflicting schemas for the same table."""
        d = _decomp([_pkg("AI", [("Damage", True)]), _pkg("Development", [("Damage", True)])])
        assert _owner_of(normalize_entity_ownership(d), "Damage") == ["AI"]

    def test_correct_ownership_is_left_alone(self):
        d = _decomp([_pkg("AI", [("Damage", True)]), _pkg("Development", [("Damage", False)])])
        assert _owner_of(normalize_entity_ownership(d), "Damage") == ["AI"]

    def test_empty_packages_do_not_raise(self):
        normalize_entity_ownership(_decomp([_pkg("DevOps", [])]))

    def test_report_flags_inconsistent_spelling(self):
        """Return_Items vs ReturnItems is an unbuildable combination, not cosmetic."""
        d = _decomp([
            _pkg("Development", [("Return_Items", True)]),
            _pkg("AI", [("ReturnItems", False)]),
        ])
        # Variants are sorted for deterministic output, so compare as a set.
        flagged = entity_ownership_report(d)["inconsistent_spelling"]
        assert len(flagged) == 1
        assert set(flagged[0].split(" vs ")) == {"Return_Items", "ReturnItems"}

    def test_healthy_report_is_empty(self):
        d = normalize_entity_ownership(
            _decomp([_pkg("Development", [("Return", True)]), _pkg("QA", [("Return", False)])])
        )
        report = entity_ownership_report(d)
        assert report == {"unowned": [], "multiply_owned": [], "inconsistent_spelling": []}


class TestDependencyGraph:
    def _items(self, spec):
        return [
            WorkItem(id=i, title=i, department="Development", description="d", depends_on=deps)
            for i, deps in spec
        ]

    def test_acyclic_graph_has_no_cycles(self):
        d = _decomp([], self._items([("A", []), ("B", ["A"]), ("C", ["B"])]))
        assert dependency_cycles(d) == []

    def test_cycle_is_detected(self):
        """A cycle means no valid execution order exists."""
        d = _decomp([], self._items([("A", ["C"]), ("B", ["A"]), ("C", ["B"])]))
        assert dependency_cycles(d) != []

    def test_self_dependency_is_a_cycle(self):
        d = _decomp([], self._items([("A", ["A"])]))
        assert dependency_cycles(d) != []

    def test_dangling_dependency_is_reported(self):
        d = _decomp([], self._items([("A", ["NOPE"])]))
        assert dangling_dependencies(d) == ["A -> NOPE"]

    def test_dangling_dependency_does_not_crash_cycle_check(self):
        d = _decomp([], self._items([("A", ["NOPE"]), ("B", ["A"])]))
        assert dependency_cycles(d) == []
