Vendored skill files are not imported or executed. They are prompt context
only, snapshotted beside artefacts.

## Manifest skills (MIT)

Fetched by `scripts/vendor_skills.py` from the pinned `vendor-manifest.json`.

- bmad-code-org/BMAD-METHOD @ f033e70a2c0a3751aaab17dfdd29839ac621f541
- addyosmani/agent-skills @ dc27a9c2e13721158157632de61b4106c6c2a2a1
- wshobson/agents @ 4236bb91f8395b0435f1d8b8baf9e8e4c69a8620

Extra Phase 1–5 skills from those repos (interview-me, ADRs, API design,
planning, TDD, CI/CD, code review, security, incremental build, observability,
frontend UI, secrets, e2e patterns, avoid-ai-writing, bmad-code-review) are
pinned the same way.

## Manual vendor: open-design (Apache-2.0)

`open-design/` is pinned in `open-design/SOURCE.txt` (nexu-io/open-design).
Used by the UI agent bundle for craft + dashboard template guidance. Refresh
by re-fetching the listed paths at that commit; do not run vendor scripts
against this tree (manifest only allows SKILL.md from approved repos).
