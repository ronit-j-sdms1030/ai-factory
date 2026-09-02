# Git Integration — Agents 1–4

**Companion to:** [architecture.md](architecture.md) · [agents.md](agents.md)
**Scope:** Intake, BRD, UI and Decomposition. Agents 5–8 keep their own code-generation
repositories and are out of scope here.

**Satisfies.** Proposal §2.4, D4 · SoW 5.0, 6.0

---

## 1. What Git is for here

Git is not storage bolted onto the pipeline. It is where the governance record lives.

Mongo holds *working state* — which stage a requirement is in, who has approved, what the
current draft says. It is mutable by design, and an approval that changes a document in
place leaves no evidence of what was approved.

Git holds *the record*. Every agent output is a commit authored by that agent. Every
approval gate is a pull request. The two answer different questions:

| Question | Answered by |
|---|---|
| What is this requirement's state right now? | Mongo |
| What exactly did the VP approve, and when? | Git |
| Which agent produced this, and from what? | Git |
| Who is this waiting on? | Mongo |

One consequence worth stating plainly: **the pipeline runs without Git configured.**
`open_store()` returns `None` when `GOVERNANCE_REPO_PATH` is unset and publishing becomes
silently inert. That is deliberate — the migration should not require every deployment to
have a repository before anything works — but it does mean an unconfigured environment
produces no audit trail while looking healthy.

---

## 2. End-to-end path

```
agent produces an artefact
        │
        ▼
git_store.commit_artefacts()      branch off main, write files, commit as the agent
        │
        ▼
publish._push_with_token()        push the base branch, then this one
        │
        ▼
github_api.open_pull_request()    one pull request per stage
        │
        ▼
reviewers.github_logins_for()     request review from the tier that gates this stage
        │
        ▼
─────────── a human approves on GitHub ───────────
        │
        ▼
POST /api/webhooks/github         signature verified before the payload is parsed
        │
        ▼
state_machine.transition()        the gate advances, in Mongo
```

Everything above the dashed line is the pipeline publishing. Everything below it is a
human decision travelling back. The two halves are deliberately separate: publishing is
best-effort and never blocks an approval, while an approval arriving by webhook is
re-authorised from scratch rather than trusted because GitHub let it through.

---

## 3. Repository layout

One folder per requirement, inside a governance monorepo:

```
requirements/<slug>/
    requirement.md              agent 1 — transcript + structured summary
    brd/brd.json                agent 2
    brd/diagrams/*.mmd          architecture and ER diagrams, split out
    ui/screens/*.jsx            agent 3 — one file per screen
    ui/preview.html             the screens assembled into one runnable page
    ui/clarifications.md        what the UI agent could not resolve alone
    workitems/graph.json        agent 4 — the dependency graph
    workitems/<dept>/package.json   one per department
```

**The slug is readable and stable.** `req/6a968da2ee951e14aa09ae1a/brd` identifies nothing
in a branch list, so the folder pairs the title with a short id suffix:
`employee-attendance-and-leave-system-for-wor-8950fffe`. It is computed once and stored on
the artifact as `repoSlug`, never recomputed — the FSD edit chat can rename a requirement
mid-pipeline, and a folder that moved with the title would strand every artefact already
committed under the old name.

**Diagrams are separate files.** A Mermaid diagram inside a JSON blob cannot be linted,
diffed or previewed; as `.mmd` files GitHub renders them in the pull request.

**The requirement is Markdown, not JSON.** It is the artefact a human actually reads at the
first gate. The structured form is embedded for machines.

---

## 4. Branches — one per stage, not one per requirement

```
main
req/<slug>/requirement
req/<slug>/brd
req/<slug>/ui
req/<slug>/workitems
```

Each stage branches **from `main`**, not from the previous stage. A reviewer opening the
BRD's pull request sees only the BRD — not the requirement, the screens and everything
else that has happened since.

The cost of that choice is a common confusion: browsing the `requirement` branch and
navigating to `brd/` gives a 404, because that folder exists only on the `brd` branch. The
branch selector is the answer.

**A stage commit is a snapshot, not an append log.** The folder is cleared before writing,
and staging uses `git add --all` over the requirement path rather than `index.add`, which
records additions only. Without both, regenerating left every earlier attempt in place —
one UI branch accumulated 32 screen files across five runs, including the same screen under
two different slugs after its name was normalised, with no way to tell which was current.

---

## 5. Agents as commit authors

Each agent commits under its own identity:

| Agent | Commit author |
|---|---|
| 1 · Intake | `intake-agent[bot] <intake-agent@stark.local>` |
| 2 · BRD | `brd-agent[bot] <brd-agent@stark.local>` |
| 3 · UI | `ui-agent[bot] <ui-agent@stark.local>` |
| 4 · Decomposition | `decomposition-agent[bot] <decomposition-agent@stark.local>` |

`git log` therefore attributes each artefact to the agent that produced it, rather than to
"the service". An unknown agent name is refused rather than defaulted, so a new agent
cannot commit anonymously by omission.

Each agent commits **once, on completion** — never mid-run. A partially generated BRD is
not a reviewable artefact.

---

## 6. Reviewers and enforcement

Each stage maps to the tiers that gate it:

| Stage | Reviewing tiers | Gate |
|---|---|---|
| `requirement` | MD, CEO | Gate 0 |
| `brd` | MD, CEO, VP | GATE 1 |
| `ui` | VP | GATE 2 |
| `workitems` | VP | GATE 3 |

Two mechanisms resolve those tiers to accounts, because only one works everywhere.

**Teams** (`@org/tier-vp`) are what the SoW commits to. They drive CODEOWNERS and branch
protection, so enforcement lives in the repository rather than in application code, and
GitHub's own rule that an author cannot approve their own pull request gives segregation of
duties for free:

```
/requirements/*/requirement.md   @org/tier-md @org/tier-ceo
/requirements/*/brd/             @org/tier-md @org/tier-ceo @org/tier-vp
/requirements/*/ui/              @org/tier-vp
/requirements/*/workitems/graph.json  @org/tier-vp
/requirements/*/workitems/<dept>/     @org/tl-<dept>
```

**Individual users** are the fallback. Teams require a GitHub *organisation*; on a personal
account no team can exist, and requesting one silently reviews nothing. Naming the humans
directly still puts the right people on the pull request — it just cannot be *enforced* by
branch protection.

Falling back is deliberate. A gate nobody is asked to review is worse than one enforced
only by convention, because the first looks fine and quietly waits forever.

The mapping from internal user to GitHub account lives on the user record as `githubLogin`
and is managed through `/api/admin/github-mappings`, restricted to MD and CEO — it decides
whose GitHub approval can move a requirement through a gate, so it is an authority change,
not a profile setting. `GET` on that endpoint reports `blocked_stages`: the gates that
currently cannot advance because no approver for them has an account mapped.

---

## 7. Approvals coming back

A pull request review is a governance decision, so the webhook carries real authority. Two
rules follow.

**Verify before parsing.** The HMAC-SHA256 signature is checked against the raw body before
anything in the payload is trusted, and a missing secret fails closed. The rejection is
deliberately terse — a detailed reason would help someone probe for a valid signature.

**Re-check the reviewer's authority.** GitHub already restricts *who can approve* through
CODEOWNERS, but approval chains are **ordered** and GitHub reviews are not. The reviewer's
tier is therefore re-checked against the gate the requirement is actually waiting on, so an
approval from a later gate's tier arriving early is recorded and ignored rather than acted
on. That check mirrors `state_machine.transition` rather than inventing a parallel rule: a
divergence would let the webhook admit an approval the state machine would then refuse —
or, worse, one it would wrongly accept.

**The same approval means different things at different stages.** Approving the BRD's pull
request while the requirement sits in `fsd_review` is the reviewer saying "this FSD is
done", which is `sendFsdToClient` — not `approve`, an action that stage does not accept at
all:

| Stage | An approval means |
|---|---|
| `pending_approval` | `approve` |
| `fsd_review` | `sendFsdToClient` |
| `fsd_pending_client` | `approveFsd` |
| `fsd_final_approval` | `giveFinalFsdApproval` |
| `pending_client_review` | `acceptChanges` |

Only `pending_approval` models rejection and revision; the FSD loop has no equivalent
transition, so those decisions are recorded but cannot move it.

The branch name resolves back to the requirement by `repoSlug`, falling back to the bare
Mongo id for branches created before slugs existed. Without that fallback, every gate on an
existing pull request would have stopped advancing the moment the naming changed.

---

## 8. Failure modes, and why each is handled the way it is

**Publishing never blocks an approval.** The artefact is already saved in Mongo, and an
approval flow must not stall because GitHub was unreachable. A failure is reported to the
caller and recorded on the job; it never raises.

**A failed publish is not a failed generation.** A job whose only errors are publish errors
is recorded as `done`, not `failed`. Reporting otherwise sent people looking for an FSD
that was sitting on the artifact, because the pull request had opened and only the reviewer
assignment failed.

**The base branch is pushed first.** A repository created empty on GitHub has no branches
at all: the base commit exists only in the local clone, so opening a pull request against
it fails with "base invalid" even though the feature branch pushed fine.

**`--force-with-lease` is pinned to an explicit SHA.** Pushing to a URL rather than a named
remote leaves git without a remote-tracking ref, so a bare lease cannot be evaluated and
refuses with "stale info". The remote SHA is looked up with `ls-remote` and the lease
pinned to it — keeping the protection instead of downgrading to an unconditional `--force`,
which would silently discard someone's work.

**The token never touches `.git/config`.** It is injected into the remote URL for the
duration of the call, so it cannot end up committed or left readable in the working tree.

**Path traversal is scoped to the requirement folder.** File paths originate in model
output. A check against the repository root would still permit `../../other-requirement/`,
letting one requirement's agents overwrite another's approved artefacts while staying
technically inside the repo.

**Publishing is serialised process-wide.** One repository, one working tree, a branch per
requirement — and generation now runs on background threads. Two of them reaching the push
together would have one checking out its branch while the other staged a commit. Model
calls happen outside the lock; the git work it serialises is seconds.

**Republishing returns the existing pull request** rather than failing with GitHub's 422
for a duplicate head branch.

---

## 9. What is not built

**Nothing merges to `main`.** Approving a pull request advances the state in Mongo but does
not merge the branch, so `main` holds only its README. The complete picture of a
requirement exists spread across four branches and is never assembled anywhere. Git is
currently an append-only record of *proposals*, not of accepted decisions. Merging on
approval would make `main` the accumulated record of what the organisation actually agreed
to — this is the most significant gap in the integration.

**Branch protection is not configured.** CODEOWNERS is generated, but without a GitHub
organisation there are no teams for it to reference and no protection rule requiring their
review. On a personal account the gate is advisory: the right people are asked, and nothing
stops the branch being pushed anyway.

**GATE 3 does not block.** The work-item pull request opens and is reviewable, but nothing
waits for it. GATE 2 does block: the decomposition does not run until the VP approves the
screens. See [agents.md §6](agents.md#6-gate-summary).

**Agents 5–8 are not integrated.** Code generation writes to its own repositories and has
no connection to this governance repo.
