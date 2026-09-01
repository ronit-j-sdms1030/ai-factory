"""Naming for a requirement's folder and branches.

``req/6a968da2ee951e14aa09ae1a/brd`` identifies nothing in a branch list. The
folder pairs the title with a short id suffix so a reviewer can tell what they
are looking at, while still separating two requirements that share a title.
"""

from __future__ import annotations

from app.publish import repo_folder


def _artifact(title: str, _id: str = "6a968da2ee951e14aa09ae1a") -> dict:
    return {"_id": _id, "title": title}


class TestNaming:
    def test_the_title_leads_and_a_short_id_follows(self):
        folder = repo_folder(_artifact("StarkLogix Warehouse and Logistics Management Platform"))
        assert folder.startswith("starklogix-warehouse-and-logistics")
        assert folder.endswith("-09ae1a"[-8:]) or folder.endswith("e14aa09ae1a"[-8:])

    def test_two_requirements_sharing_a_title_do_not_collide(self):
        a = repo_folder(_artifact("Damaged Stock Write-Off", "6a968da2ee951e14aa09ae1a"))
        b = repo_folder(_artifact("Damaged Stock Write-Off", "6a967bf8e78da932e9f2e27a"))
        assert a != b

    def test_an_untitled_requirement_still_gets_a_usable_folder(self):
        folder = repo_folder({"_id": "6a968da2ee951e14aa09ae1a", "title": ""})
        assert folder.startswith("requirement-")

    def test_punctuation_is_reduced_to_a_safe_segment(self):
        folder = repo_folder(_artifact("Returns & Warranty: Phase 1 (v2)"))
        assert "&" not in folder and ":" not in folder and "(" not in folder
        assert "/" not in folder

    def test_the_folder_stays_short_enough_to_read(self):
        folder = repo_folder(_artifact("A" * 300))
        assert len(folder) <= 60


class TestStability:
    """The title can change mid-pipeline; the folder must not.

    The FSD edit chat can rename a requirement. If the folder moved with it,
    every artefact already committed would be stranded in a branch nobody
    looks at again, and the webhook would stop resolving the old branches.
    """

    def test_the_folder_is_stored_on_first_use(self):
        artifact = _artifact("Warehouse Platform")
        folder = repo_folder(artifact)
        assert artifact["repoSlug"] == folder

    def test_a_renamed_requirement_keeps_its_original_folder(self):
        artifact = _artifact("Warehouse Platform")
        original = repo_folder(artifact)

        artifact["title"] = "Something Completely Different"
        assert repo_folder(artifact) == original

    def test_an_existing_folder_is_never_recomputed(self):
        artifact = {"_id": "6a968da2ee951e14aa09ae1a", "title": "New", "repoSlug": "legacy-folder-name"}
        assert repo_folder(artifact) == "legacy-folder-name"
