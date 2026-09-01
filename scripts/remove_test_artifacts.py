"""Remove the artifacts created by pipeline test runs on 2026-09-01.

Each id below was produced by a scripted end-to-end run or an endpoint probe,
not by a person using the app. Listed explicitly rather than matched by title
or date so this cannot widen to real work by accident, and each is checked
against its expected title before deletion.

Deliberately NOT included:
  6a968da2ee951e14aa09ae1a  StarkLogix — a real requirement, migrated instead
  the four pre-port artifacts (Employee Leave, Retail Returns, SCADA, Banquet)

    python scripts/remove_test_artifacts.py [--apply]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent-service"))

from bson import ObjectId  # noqa: E402

from app import db  # noqa: E402

# id -> the title it must have, as a guard against deleting the wrong document.
TEST_ARTIFACTS = {
    "6a967bf8e78da932e9f2e27a": "Damaged Stock Write-Off System",
    "6a968370e66217373f3a1031": "Damaged Stock Write-Off System",
    "6a968518f1a62f806822d456": "Damaged Stock Write-Off System",
    "6a968c0df1a62f806822d457": "Untitled requirement",
    "6a968c14f1a62f806822d458": "Untitled requirement",
    "6a968cfbee951e14aa09ae19": "Untitled requirement",
    "6a968f3e732463c63164b555": "Damaged Stock Write-Off Tool",
}


def main(apply: bool) -> int:
    removed = skipped = 0
    for artifact_id, expected_title in TEST_ARTIFACTS.items():
        found = db.artifacts().find_one({"_id": ObjectId(artifact_id)})
        if not found:
            print(f"  {artifact_id}  already gone")
            continue

        actual = found.get("title") or ""
        if actual != expected_title:
            print(f"  {artifact_id}  SKIPPED — title is '{actual[:44]}', expected '{expected_title}'")
            skipped += 1
            continue

        print(f"  {artifact_id}  {str(found.get('currentStage')):18s} {actual[:44]}")
        if apply:
            db.artifacts().delete_one({"_id": ObjectId(artifact_id)})
            db.generation_jobs().delete_many({"artifactId": artifact_id})
        removed += 1

    print(f"\n{removed} {'removed' if apply else 'would be removed'}" + (f", {skipped} skipped" if skipped else ""))
    if not apply and removed:
        print("Re-run with --apply to delete them.")

    print("\nRemaining:")
    for a in db.artifacts().find().sort("createdAt", 1):
        print(f"  {a['_id']}  {str(a.get('currentStage')):18s} {(a.get('title') or '')[:44]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(apply="--apply" in sys.argv))
