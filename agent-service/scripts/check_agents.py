"""Run each agent against a throwaway requirement and show what it produced.

The point is evidence, not a green tick. Every step prints real content —
entity names, screen names, department assignments — so you can judge whether
an agent did the work or returned something plausible-looking and empty. A
mock would be obvious here: it cannot invent a data model that matches the
requirement it was given.

Touches nothing. No Mongo write, no Git commit, no pull request, no artifact
in anyone's workspace. It calls the agents directly.

    python scripts/check_agents.py              # all four agents
    python scripts/check_agents.py --skip-ui    # skip the slow one (~5 min)
    python scripts/check_agents.py --brief      # summary lines only
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.brd import brd_agent  # noqa: E402
from app.agents.decomposition import decomposition_agent  # noqa: E402
from app.agents.intake import finalize_requirement, run_chat_turn  # noqa: E402
from app.agents.ui import ui_agent  # noqa: E402

BRIEF = "--brief" in sys.argv
SKIP_UI = "--skip-ui" in sys.argv

# Deliberately specific. A vague brief makes every agent look competent — the
# test is whether what comes back is about *this* system.
REQUIREMENT = (
    "Build an internal Damaged Stock Write-Off System for our retail warehouses. "
    "Warehouse staff scan a damaged item's barcode on a mobile device, photograph the damage, "
    "pick a reason code and severity tier, and submit a write-off. A supervisor approves or "
    "rejects it, and approved write-offs deduct from inventory and post to the finance ledger. "
    "Roughly 40 staff and 8 supervisors across 3 sites, about 200 write-offs a day. "
    "It integrates with our existing SAP inventory over REST. No AI features are needed. "
    "Data is internal commercial data, not personal data."
)


def head(n: int, title: str) -> None:
    print(f"\n{'━' * 74}\n{n}. {title}\n{'━' * 74}", flush=True)


def show(label: str, value) -> None:
    if BRIEF:
        return
    print(f"   {label:<22}{value}", flush=True)


def check_intake() -> tuple[dict, float]:
    head(1, "INTAKE AGENT — conversation to structured requirement")
    started = time.time()

    history = [{"role": "assistant", "content": "Describe the capability you need."}]
    message = REQUIREMENT
    asked = 0

    for _ in range(12):
        history.append({"role": "user", "content": message})
        turn = run_chat_turn(history, "the Managing Director (MD)")
        if turn["type"] == "ready":
            break
        asked += 1
        history.append({"role": "assistant", "content": turn["text"]})
        show(f"Q{asked}", turn["text"][:70].replace("\n", " "))
        message = "Yes — use your best judgement on the rest."

    requirement = finalize_requirement(history)
    elapsed = time.time() - started
    data = requirement.model_dump(by_alias=True)

    print(f"\n   asked {asked} question(s), finalised in {elapsed:.0f}s", flush=True)
    show("title", data["title"])
    show("inScope", f"{len(data['inScope'])} items")
    show("functionalReqs", f"{len(data['functionalRequirements'])} items")
    show("preferredModel", data["preferredCodeGenModel"])
    if not BRIEF and data["inScope"]:
        print(f"   first in-scope item: {data['inScope'][0][:66]}", flush=True)

    assert asked >= 1, "intake asked nothing — the floor is not being enforced"
    assert data["inScope"], "intake produced no scope"
    return data, elapsed


def check_brd(requirement: dict) -> tuple[dict, float]:
    head(2, "BRD AGENT — requirement to full FSD, with critique loop")
    started = time.time()
    result = brd_agent({"requirement": requirement, "chat_history": []})
    brd, rounds = result["brd"], result.get("critique_rounds", 0)
    elapsed = time.time() - started

    print(f"\n   {len(json.dumps(brd)):,} chars in {elapsed:.0f}s, {rounds} critique round(s)", flush=True)
    show("techStack", f"{len(brd['techStack'])} layers")
    show("dataModel", f"{len(brd['dataModel'])} entities")
    show("pageBehavior", f"{len(brd['pageBehavior'])} pages")
    show("securityDesign", f"{len(brd['securityDesign'])} items")
    show("openQuestions", f"{len(brd['openQuestions'])} raised")
    if not BRIEF:
        print(f"   entities: {', '.join(e['name'] for e in brd['dataModel'][:7])}", flush=True)
        print(f"   diagram : {'mermaid flowchart present' if 'flowchart' in brd['architectureDiagram'] or 'graph' in brd['architectureDiagram'] else 'MISSING'}", flush=True)

    assert brd["dataModel"], "BRD has no data model"
    assert brd["techStack"], "BRD has no tech stack"
    return brd, elapsed


def check_ui(brd: dict) -> tuple[dict, float]:
    head(3, "UI AGENT — approved FSD to reviewable screens")
    started = time.time()
    ui = ui_agent({"brd": brd})["ui"]
    elapsed = time.time() - started

    total = sum(len(s["source"]) for s in ui["screens"])
    print(f"\n   {len(ui['screens'])} screens, {total:,} chars of React in {elapsed:.0f}s", flush=True)
    for screen in ui["screens"]:
        show(screen["name"], f"{screen['route']:<24}{len(screen['source']):>7,} chars")
    show("clarifications", f"{len(ui['clarifications'])} raised")
    if not BRIEF and ui["clarifications"]:
        print(f"   e.g. {ui['clarifications'][0][:66]}", flush=True)

    assert ui["screens"], "UI agent produced no screens"
    assert all("function" in s["source"] or "=>" in s["source"] for s in ui["screens"]), \
        "a screen contains no component definition"
    return ui, elapsed


def check_decomposition(brd: dict) -> tuple[dict, float]:
    head(4, "DECOMPOSITION AGENT — FSD to work items and department packages")
    started = time.time()
    result = decomposition_agent({"brd": brd})["work_items"]
    elapsed = time.time() - started

    packages, items = result.get("packages", []), result.get("work_items", [])
    print(f"\n   {len(items)} work items across {len(packages)} departments in {elapsed:.0f}s", flush=True)
    for package in packages:
        owned = [e["entity"] for e in package.get("dataModel") or [] if e.get("ownedByThisDepartment")]
        show(package["team"], f"owns {len(owned)} entities, {len(package.get('plan') or [])} phases")
    integrity = result.get("integrity") or {}
    problems = {k: v for k, v in integrity.items() if v}
    show("integrity", "clean" if not problems else f"PROBLEMS: {problems}")

    assert packages, "decomposition produced no packages"
    # Checked explicitly, because the first run of this script reported success
    # over a decomposition that returned zero work items: a malformed entry
    # failed validation, the retry came back without them, and asserting only
    # on packages let it through. A green tick over an empty graph is the
    # exact failure this script exists to catch.
    assert items, (
        "decomposition produced 0 work items — the dependency graph is empty, "
        "so nothing downstream has an execution order"
    )
    # Every entity in the BRD has to be owned by exactly one department, or a
    # department generates code against a schema nobody creates.
    owners: dict[str, list[str]] = {}
    for package in packages:
        for entity in package.get("dataModel") or []:
            if entity.get("ownedByThisDepartment"):
                owners.setdefault(entity["entity"], []).append(package["team"])
    unowned = [e["name"] for e in brd["dataModel"] if e["name"] not in owners]
    if unowned:
        print(f"   WARNING: {len(unowned)} BRD entities owned by nobody: {', '.join(unowned[:6])}", flush=True)
    return result, elapsed


def main() -> int:
    print("Running each agent against a throwaway requirement.")
    print("Nothing is written to Mongo, Git, or anyone's workspace.")
    timings: list[tuple[str, float]] = []

    requirement, t = check_intake();                  timings.append(("intake", t))
    brd, t = check_brd(requirement);                  timings.append(("brd", t))
    if not SKIP_UI:
        _, t = check_ui(brd);                         timings.append(("ui", t))
    _, t = check_decomposition(brd);                  timings.append(("decomposition", t))

    print(f"\n{'━' * 74}\nAll agents produced real output.\n")
    for name, seconds in timings:
        print(f"   {name:<16}{seconds:>6.0f}s")
    print(f"   {'total':<16}{sum(t for _, t in timings):>6.0f}s")
    if SKIP_UI:
        print("\n   (UI agent skipped — drop --skip-ui to include it.)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
