"""Git artefact store, exercised against real throwaway repositories.

No mocking of git itself: these create an actual repository per test, so what
is verified is that commits, branches and authorship genuinely land — not that
a stub was called.
"""

import json

import pytest
from git import Repo

from app.git_store import GitStore, GitStoreError, open_store


@pytest.fixture
def store(tmp_path):
    return GitStore.init(tmp_path / "governance")


BRD = {
    "objective": "Automate returns",
    "architecture_diagram": "flowchart TD\n  A --> B",
    "db_schema_diagram": "erDiagram\n  RETURN ||--o{ ITEM : has",
    "tech_stack": [{"layer": "Backend", "choice": "FastAPI", "rationale": "r"}],
}

DECOMPOSITION = {
    "work_items": [{"id": "WI-01", "title": "Schema", "department": "Development", "depends_on": []}],
    "integrity": {"unowned": [], "multiply_owned": [], "cycles": []},
    "packages": [
        {"team": "Development", "objective": "Build the API"},
        {"team": "Sales & Marketing", "objective": "Launch comms"},
    ],
}


class TestInit:
    def test_creates_a_repository_with_a_base_commit(self, tmp_path):
        """A branch must exist before others can be based on it."""
        s = GitStore.init(tmp_path / "g")
        assert s.repo.head.commit
        assert s.repo.active_branch.name == "main"

    def test_rejects_a_directory_that_is_not_a_repository(self, tmp_path):
        (tmp_path / "plain").mkdir()
        with pytest.raises(GitStoreError):
            GitStore(tmp_path / "plain")


class TestCommit:
    def test_commits_on_a_branch_named_for_the_stage(self, store):
        result = store.commit_artefacts(
            artifact_id="abc123", stage="brd", agent="brd",
            files=store.brd_files(BRD), message="BRD: Returns",
        )
        assert result.branch == "req/abc123/brd"
        assert store.repo.active_branch.name == "req/abc123/brd"

    def test_files_land_under_the_requirement_folder(self, store):
        result = store.commit_artefacts(
            artifact_id="abc123", stage="brd", agent="brd",
            files=store.brd_files(BRD), message="BRD",
        )
        assert "requirements/abc123/brd/brd.json" in result.files
        assert "requirements/abc123/brd/diagrams/architecture.mmd" in result.files

    def test_commit_is_authored_by_the_agent_not_a_human(self, store):
        """History should show which stage produced an artefact."""
        store.commit_artefacts(
            artifact_id="a1", stage="brd", agent="brd", files={"x.json": "{}"}, message="m",
        )
        assert store.repo.head.commit.author.name == "brd-agent[bot]"

    def test_each_stage_branches_from_the_default_branch(self, store):
        """Gates must be reviewable in isolation, not stacked on each other."""
        store.commit_artefacts(artifact_id="a1", stage="requirement", agent="intake",
                               files={"requirement.md": "# R"}, message="req")
        store.commit_artefacts(artifact_id="a1", stage="brd", agent="brd",
                               files={"brd/brd.json": "{}"}, message="brd")
        brd_head = store.repo.heads["req/a1/brd"].commit
        assert brd_head.parents[0] == store.repo.heads["main"].commit

    def test_empty_changeset_is_refused(self, store):
        with pytest.raises(GitStoreError):
            store.commit_artefacts(artifact_id="a1", stage="brd", agent="brd", files={}, message="m")

    def test_unknown_agent_is_refused(self, store):
        with pytest.raises(GitStoreError):
            store.commit_artefacts(artifact_id="a1", stage="brd", agent="nobody",
                                   files={"a.txt": "x"}, message="m")

    @pytest.mark.parametrize("bad", ["../../escape.txt", "../other-artifact/brd.json", "/etc/passwd"])
    def test_path_traversal_is_refused(self, store, bad):
        """Paths come from model output. Escaping into another requirement's
        folder stays inside the repository, so a repo-root check is not enough."""
        with pytest.raises(GitStoreError):
            store.commit_artefacts(artifact_id="a1", stage="brd", agent="brd",
                                   files={bad: "x"}, message="m")


class TestLayouts:
    def test_diagrams_are_split_out_for_independent_linting(self, store):
        files = store.brd_files(BRD)
        assert files["brd/diagrams/architecture.mmd"].startswith("flowchart")
        assert files["brd/diagrams/schema.mmd"].startswith("erDiagram")

    def test_brd_without_diagrams_omits_them(self, store):
        assert store.brd_files({"objective": "x"}) == {"brd/brd.json": '{\n  "objective": "x"\n}'}

    def test_graph_and_packages_are_separate_files(self, store):
        """They are approved by different people, so they are separate PRs."""
        files = store.workitem_files(DECOMPOSITION)
        assert "workitems/graph.json" in files
        assert "workitems/development/package.json" in files

    def test_department_names_are_made_path_safe(self, store):
        files = store.workitem_files(DECOMPOSITION)
        assert "workitems/sales-marketing/package.json" in files

    def test_requirement_markdown_embeds_the_structured_form(self, store):
        files = store.requirement_files(
            {"title": "T", "summary": "S", "in_scope": ["a"]}, "user: hello"
        )
        body = files["requirement.md"]
        assert "# T" in body and "- a" in body and "user: hello" in body
        assert '"title": "T"' in body

    def test_ui_screens_become_one_file_each(self, store):
        files = store.ui_files({
            "screens": [{"name": "ReturnsDashboard", "source": "function X(){}"}],
            "clarifications": ["Which status values?"],
        })
        assert "ui/screens/returnsdashboard.jsx" in files
        assert "ui/clarifications.md" in files


class TestRead:
    def test_reads_back_a_committed_artefact(self, store):
        store.commit_artefacts(artifact_id="a1", stage="brd", agent="brd",
                               files=store.brd_files(BRD), message="m")
        assert json.loads(store.read("a1", "brd/brd.json"))["objective"] == "Automate returns"

    def test_missing_artefact_reads_as_none(self, store):
        assert store.read("a1", "nope.json") is None


class TestPush:
    def test_pushes_a_branch_to_a_remote(self, store, tmp_path):
        bare = Repo.init(tmp_path / "remote.git", bare=True)
        store.repo.create_remote("origin", str(bare.working_dir or tmp_path / "remote.git"))
        store.commit_artefacts(artifact_id="a1", stage="brd", agent="brd",
                               files={"brd/brd.json": "{}"}, message="m")
        store.push("req/a1/brd")
        assert "req/a1/brd" in [r.name for r in bare.refs]

    def test_push_without_a_remote_is_refused(self, store):
        store.commit_artefacts(artifact_id="a1", stage="brd", agent="brd",
                               files={"brd/brd.json": "{}"}, message="m")
        with pytest.raises(GitStoreError):
            store.push("req/a1/brd")


class TestOptionalConfiguration:
    def test_absent_configuration_disables_git_rather_than_failing(self, monkeypatch):
        """The pipeline must still run where no repository is configured."""
        monkeypatch.delenv("GOVERNANCE_REPO_PATH", raising=False)
        assert open_store() is None

    def test_configured_path_opens_the_store(self, monkeypatch, tmp_path):
        GitStore.init(tmp_path / "g")
        monkeypatch.setenv("GOVERNANCE_REPO_PATH", str(tmp_path / "g"))
        assert open_store() is not None
