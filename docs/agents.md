# Agent Specification & Requirement Flows

**Companion to:** [architecture.md](architecture.md) · [git-integration.md](git-integration.md)
**Scope:** Agents 1–4 (Intake → BRD → UI → Decomposition). Agents 5–8 (Code, Review, Test, Remediation) are out of scope per architecture.md §11.

Requirement-flow diagrams live in [architecture.md §4](architecture.md#4-requirement-approaches); §5 below links to them.

---

## 1. Intake Agent

**Role.** Converts a plain-language business request into a complete, unambiguous requirement through guided conversation.

**Satisfies.** Proposal §2.1, D2 · SoW 9.0

| | |
|---|---|
| **Input** | Originator tier, conversation history |
| **Output** | Refined transcript, then a structured requirement document |
| **Gate it feeds** | GATE 1 (BRD approval) |
| **Current implementation** | `runChatTurn` (Claude Haiku 4.5), `runFinalize` (GPT-4o Mini) |

**Function.** Asks one question per turn, tracking a five-item coverage checklist: primary users and core flow; net-new versus integration; data sensitivity and scale; in-product AI features; and preferred code-generation model. It never asks two checklist items separately when one question can cover both.

**Bounded loop.** A floor of 4 questions and a ceiling of 10, both enforced in code rather than by prompt instruction. The ceiling matters for cost, not just experience: every turn re-sends the entire transcript, so token spend grows quadratically. A measured 41-turn conversation consumed ~95,000 input tokens against ~12,700 for the same intake held to ten — roughly 7.5× for 4× the turns.

**Termination.** Emits a sentinel once all five checklist items have broad-strokes answers, or terminates unconditionally at the ceiling. An explicit request to stop from the requester overrides the floor.

**Failure mode addressed.** The sentinel was previously matched by exact string equality against the whole reply. When the model wrapped it in a sentence, the match failed silently and the conversation ran 30 further turns of pleasantries. Detection is now a whole-word match anywhere in the reply.

---

## 2. BRD Agent

**Role.** Expands an approved requirement into a production-ready BRD an engineering team could build from directly.

**Satisfies.** Proposal §2.2, D2 · SoW 9.0, 10.0

| | |
|---|---|
| **Input** | Approved requirement summary, capped intake transcript (30 messages) |
| **Output** | Structured BRD — objective, architecture, architecture diagram, tech stack, user flow, data model, ER diagram, page behaviour, security design, deployment/operations, timeline, assumptions, open questions |
| **Gate it feeds** | GATE 1 (BRD approval) |
| **Current implementation** | `runDetailedReport`, `runDesignCritique`, `patchDetailedReport`, `runFsdChatEdit` (DeepSeek V3.2 / GPT-4o Mini) |

**Function.** Produces every field concretely — named entities, real pages, specific technologies. Each tech-stack choice must state why it fits, the leading alternative considered and why it was rejected, and the main limitation of the choice in this operating environment.

**Internal loop — critique then patch.** A separate reviewer inspects the finished BRD in a **fresh context**, seeing only the requirement and the document, never the reasoning that produced it. It judges fitness for purpose: will each component work under the real operating conditions; does a governing industry standard already exist that the design ignored; is every promised capability genuinely delivered; does the data model claim fields the chosen components cannot produce; and is failure/safe-state behaviour defined where the build touches physical systems, money or regulated data. Findings of high or medium severity trigger a rewrite pass. Bounded to 2 rounds with plateau detection.

**Failure handling.** If the rewrite cannot be applied, unresolved findings are appended to `openQuestions` rather than discarded — a failed auto-fix degrades to "flagged for a human", never to silence.

**Why the fresh context matters.** Asking the same model to be more careful during generation did not work; reviewing the artefact cold did. On a real BRD this found four defects independently, including a data-model field the chosen hardware could not physically produce, and a missing safe-state definition for a public venue.

---

## 3. UI Agent

**Role.** Generates the application interface from the approved BRD, before any code is written.

**Satisfies.** SoW 11.0, 12.0 · SoW 2.0 (Phase 1)

| | |
|---|---|
| **Input** | Approved BRD, design-system skill file |
| **Output** | Working React screens, versioned in Git |
| **Gate it feeds** | GATE 2 (UI approval) |
| **Current implementation** | `ui_agent`, called after BRD approval and before decomposition |

**Function.** Produces production React screens constrained by a design-system skill file carrying tokens, components and accessibility rules. Explicitly named as an agent in SoW 11.0: *"The agent raises clarification requests where the BRD is ambiguous."* That clarification loop is part of its contract, not an optional extra.

**Planned first, then written one screen at a time.** A plan call names the screens and what belongs on each; a second call per screen writes its source, four at a time. Each screen is told the others' names so cross-screen navigation resolves.

**Failure mode addressed.** The original single call asked for every screen's source at once under a 14,000-token ceiling. A nine-page BRD produces ~44,000 tokens of React — three times the budget — so the JSON truncated mid-string and failed as a parse error rather than a short answer, losing the entire design rather than the last screen. Measured after the split: 9 screens, 174,763 characters, 292s. A screen that still fails now costs one screen and is reported to the reviewer as a clarification, never dropped silently.

**Position is the point.** SoW 12.0 states *"code generation for the affected scope cannot start until UI approval is recorded."* The JavaScript pipeline inverted this — its nearest artefact was produced *by* code generation, so the thing meant to gate it depended on it. The Python pipeline runs the agent in the correct position, but the blocking gate itself is still absent: there is no `ui_review` stage, so approval is recorded rather than required.

**Reviewer.** VP. SoW 11.0 names "UI/UX and Business Analysts", but no such tier exists in `hierarchy.config.js` — the tiers are md, ceo, vp, pm and tl. VP is the closest existing authority and is what the code, CODEOWNERS and webhook all use. Introducing dedicated reviewer roles would be a hierarchy change to agree with the client.

**Note on document conflict.** Proposal §7 lists "UI prototype generation" as a roadmap item, and §11 excludes everything in §7. The SoW response reconciles this by distinguishing **working React screens (Phase 1)** from a **Figma round-trip (add-on)**. Worth confirming with the client.

---

## 4. Decomposition Agent

**Role.** Breaks an approved BRD into scoped work items ready for independent execution.

**Satisfies.** Proposal §2.3, D3

| | |
|---|---|
| **Input** | Approved BRD (and, in target state, approved UI) |
| **Output** | Work items with dependency edges and owning department; per-department slices carrying objective, architecture, tech stack, phased plan, data model with ownership flags, and dependencies |
| **Gates it feeds** | GATE 3 (VP approves the graph), GATE 4 (each TL approves their slice) |
| **Current implementation** | `runTeamSplit`, `runTeamReportChatEdit` (GPT-4o Mini) + `normalizeEntityOwnership` |

**Function.** Assigns work across the five departments — QA, AI, Development, DevOps, Sales & Marketing — skipping any with nothing to do. Each package is an execution-focused mini-BRD.

**The integration contract.** Departments generate code independently and never see each other's output, so the package is the *only* thing keeping the finished modules compatible. Three invariants are enforced:

1. **Shared entities are described identically everywhere** — same name, same field names, character for character, copied verbatim from the BRD.
2. **Entities a department only reads are still included**, flagged as not owned, so it references rather than reinventing them.
3. **Exactly one department owns each entity** — never zero, never two.

**Deterministic repair.** `normalizeEntityOwnership` runs after every split. Unowned entities are assigned (preferring Development, which owns the core application); double-claimed entities are demoted to a single owner. This exists because the prompt alone was insufficient — a real split marked four entities read-only in all five packages, meaning no department would have generated their schema.

**Failure mode addressed.** Before the data model was carried into packages, one department created a table `Return_Items` while another wrote a foreign key against `ReturnItems` — a combination that cannot build.

**Target-state change.** Per architecture.md §2.1, this agent must additionally emit a **dependency graph** across work items. The current five-department split has no ordering between packages.

---

## 5. Requirement flows by originator

The three originator paths — MD, VP and TL — are diagrammed in **[architecture.md §4](architecture.md#4-requirement-approaches)**, together with the approval-chain table and the governance note on each.

They live there rather than here to keep a single source of truth: the diagrams describe routing (an architecture concern), while this document specifies agent behaviour.

---

## 6. Gate summary

| Gate | Approver | Object | Status |
|---|---|---|---|
| GATE 1 | Per approval chain | BRD / requirement | ✅ Built |
| GATE 2 | VP | UI screens | ⚠️ Agent runs and publishes; approval does not yet block |
| GATE 3 | VP | Work-item dependency graph | ❌ Not built — approval currently implicit in BRD approval |
| GATE 4 | Owning TL | Department slice | ⚠️ Partial — TLs can edit and request revision, but no explicit approve-before-codegen gate |

`/codegen/start` currently checks only `currentStage === 'approved'`. Nothing verifies that the decomposition itself was reviewed.

---

## 7. Agents 5–8 — out of scope

Code, Review, Test and Remediation agents exist in the current build as `runCodeGen`, `runCodeReview`, `runSecurityAutoFix` and `runBuildFix`, but are not specified here. See architecture.md §11.
