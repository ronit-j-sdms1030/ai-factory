"""The exact key names stored artefacts must use.

Two other pieces of the system read these documents and neither is going to
be rewritten: the frontend, and the JavaScript code-generation service that
agents 5-8 still live in. Both were written against camelCase, and Mongo holds
one collection shared by the Python and Express services.

A renamed key has no runtime symptom on either side — the frontend renders
"None specified" and code generation receives ``undefined`` — so the contract
is asserted here rather than discovered in a generated build.
"""

from __future__ import annotations

from app.schemas import BRD, Decomposition, DepartmentPackage, Requirement, UIDesign


def _keys(model) -> set[str]:
    """What this model serialises as, which is what lands in Mongo."""
    return set(model.model_json_schema()["properties"])


class TestRequirement:
    """Read by the review-and-send modal in the intake widget."""

    def test_the_fields_the_review_modal_renders(self):
        assert {
            "title", "summary", "inScope", "outOfScope",
            "functionalRequirements", "nonFunctionalRequirements",
            "preferredCodeGenModel", "openQuestions",
        } <= _keys(Requirement)

    def test_nothing_is_left_in_snake_case(self):
        assert not [k for k in _keys(Requirement) if "_" in k]


class TestBRD:
    """Read by the FSD viewer."""

    def test_the_fields_the_fsd_viewer_renders(self):
        assert {
            "objective", "architecture", "architectureDiagram", "techStack",
            "userFlow", "dataModel", "dbSchemaDiagram", "pageBehavior",
            "securityDesign", "deploymentAndOperations", "timeline",
            "assumptions", "openQuestions",
        } <= _keys(BRD)

    def test_nothing_is_left_in_snake_case(self):
        assert not [k for k in _keys(BRD) if "_" in k]


class TestDepartmentPackage:
    """Read by runCodeGen in the retained JavaScript service.

    ``dataModel`` is the one that matters most: a package without it is a
    department generating code against no schema, which is how one team
    created `Return_Items` while another wrote a foreign key against
    `ReturnItems`.
    """

    def test_the_fields_code_generation_reads(self):
        assert {
            "team", "objective", "architecture", "techStack", "dataModel",
            "plan", "securityDesign", "dependencies",
        } <= _keys(DepartmentPackage)

    def test_nothing_is_left_in_snake_case(self):
        assert not [k for k in _keys(DepartmentPackage) if "_" in k]


class TestDecomposition:
    def test_work_items_are_camel_cased(self):
        assert "workItems" in _keys(Decomposition)
        assert "packages" in _keys(Decomposition)


class TestUIDesign:
    def test_screens_and_clarifications(self):
        assert {"screens", "clarifications"} <= _keys(UIDesign)


class TestTolerantValidation:
    """A model answering in snake_case must still parse.

    populate_by_name is what makes the rename safe: the alias is what we
    serialise, not what we are forced to receive.
    """

    def test_snake_case_input_is_accepted(self):
        pkg = DepartmentPackage.model_validate(
            {
                "team": "Development",
                "objective": "o",
                "architecture": "a",
                "tech_stack": [],
                "data_model": [],
                "plan": [],
                "security_design": [],
                "dependencies": [],
            }
        )
        assert pkg.model_dump(by_alias=True)["techStack"] == []

    def test_camel_case_input_is_accepted(self):
        pkg = DepartmentPackage.model_validate(
            {
                "team": "Development",
                "objective": "o",
                "architecture": "a",
                "techStack": [],
                "dataModel": [],
                "plan": [],
                "securityDesign": [],
                "dependencies": [],
            }
        )
        assert pkg.model_dump(by_alias=True)["dataModel"] == []
