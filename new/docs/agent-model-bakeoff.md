# Agent model bake-off

Per-role **best for the job** (no quality cut) and **best value**, then cash to test every role.

Prices: OpenRouter list, 23 Sep 2026. Token counts: `phase1/agent_settings.py` `CYCLE_USAGE`. **Load = 3× listed** (fat skill prefixes, growing transcripts, JSON retries, re-runs).

Architecture names **14 roles**. Settings has **12 keys**. AI Engineer, Developer, and Migration Author share `build` — one bake-off, not three.

**14 roles ≠ 14 SKUs.** Seven models cover every bake-off:

`google/gemini-2.5-flash` · `openai/gpt-4o-mini` · `deepseek/deepseek-v3.2` · `qwen/qwen3-coder` · `openai/gpt-4.1-mini` · `anthropic/claude-haiku-4.5` · `anthropic/claude-sonnet-4.6`

Method on every agent: **2 dummy requirements × five models**. Quality winner is always in the five. Opus and GPT-4.1 full are never in the five.

Factory defaults stay cheap. Flip Sonnet on in Settings only on bake-off days.

Related cycle pot: [llm-test-budget.md](llm-test-budget.md).

**Azure alternative** (models, free-tier truth, OpenRouter vs Azure cash): [Azure · alternative](#azure--alternative).

---

## Total budget

| How you test | What you run | Load |
|---|---|---|
| Value only | Best-value model, 2 runs each role | **$1** |
| Quality only | Best model only, 2 runs each role (Sonnet on BRD / overview / review / adversary) | **$3** |
| **Full bake-off (recommended)** | 2 runs × five models on every role | **$10** |
| Paid coding agent on Gate 5 (OpenHands / SWE-agent) or Opus on every role | Do not | $100+ |

**Put $10 on OpenRouter.** That is the all-agent pot. Leftover stays as credit.

---

## Once models are selected — one production cycle

**What this cycle is.** One requirement walks intake → seven gates → artefacts → catalog.

**The 12 agents still run.** Intake, BRD, Architect, UI, DevOps, Decomposer, QA, Overview, Review, Adversary, and Monitor each call a model. They write the scope, BRD, architecture note, screens, tickets, test cases, and review notes.

**Gate 5 does not hire a coder model.** AI Engineer, Developer, and Migration Author are three *ticket owners*, one Settings key (`build`). In this demo a Python assembler (`phase4/build.py`) writes `app/{rid}/` from those tickets, screens, and the BRD. No OpenHands. No SWE-agent. That is why the cycle stays cheap — not because the other agents are off.

Turn a hosted coding agent on and this table is wrong ($2–20+ extra per cycle). These numbers assume the assembler writes the app.

**Intake 10 calls.** ≤9 questions; last call writes the scope report.

**UI 4 screens.** One model call per screen on the first pass.

Prices: OpenRouter list, 23 Sep 2026.

**Revisions = 2 per step.** Each agent runs **3 times**: first artefact + two Gate revises (that agent redoes its work). Gate 3 sides are independent: Architect ×3 and UI ×3. Build / Review / Adversary each ×3 (not the 5-iteration heal cap).

`3× load` below is **token slack** (fat skills, growing transcripts, JSON retries). It is not a third revision. Revisions are already in the middle column.

| Pack | First pass (listed) | **+ 2 revises / step (listed)** | Same, 3× token slack |
|---|---|---|---|
| **Best value** | $0.03 | **$0.08** | **$0.25** |
| Factory defaults (cheap quality) | $0.08 | **$0.25** | **$0.76** |
| **Best for the job** (Sonnet on UI, BRD, overview, review, adversary) | $0.38 | **$1.15** | **$3.46** |

**Plan cash per requirement after you pick models**

| Pack | Plan |
|---|---|
| Value | **$0.25** |
| Factory defaults | **$1** |
| Quality winners | **$4** |

10 live requirements on the quality pack ≈ **$40**. Same on value ≈ **$2.50**. Bake-off $10 is *picking* models; this is *running* them. UI on Sonnet is most of that jump.

### Per step — quality winners (1 + 2 revises)

| Step | Model | First pass | + 2 revises | Load (3×) |
|---|---|---|---|---|
| Intake | Haiku 4.5 | $0.045 | $0.135 | $0.41 |
| BRD | Sonnet 4.6 | $0.069 | $0.207 | $0.62 |
| Architect | gpt-4.1-mini | $0.005 | $0.016 | $0.05 |
| UI (4 screens) | Sonnet 4.6 | $0.198 | $0.594 | $1.78 |
| DevOps | gpt-4.1-mini | $0.002 | $0.005 | $0.02 |
| Decomposer | gpt-4.1-mini | $0.003 | $0.010 | $0.03 |
| QA | Qwen3-Coder | $0.001 | $0.003 | $0.01 |
| Overview | Sonnet 4.6 | $0.020 | $0.059 | $0.18 |
| Build (9–11) | gpt-4o-mini | $0.001 | $0.002 | $0.01 |
| Review | Sonnet 4.6 | $0.021 | $0.063 | $0.19 |
| Adversary | Sonnet 4.6 | $0.017 | $0.050 | $0.15 |
| Monitor | Haiku 4.5 | $0.004 | $0.011 | $0.03 |
| **Cycle** | | **$0.38** | **$1.15** | **$3.46** |

Sonnet is ~84% of the quality cycle (UI + BRD + overview + review + adversary). UI alone is half of it. That is intentional: screens are the walkthrough.

### Per step — best value (1 + 2 revises)

| Step | Model | First pass | + 2 revises | Load (3×) |
|---|---|---|---|---|
| Intake | Gemini Flash | $0.004 | $0.012 | $0.04 |
| BRD | DeepSeek V3.2 | $0.004 | $0.011 | $0.03 |
| Architect | DeepSeek V3.2 | $0.002 | $0.007 | $0.02 |
| UI (4 screens) | Qwen3-Coder | $0.010 | $0.030 | $0.09 |
| DevOps | Gemini Flash | <$0.001 | $0.001 | $0.00 |
| Decomposer | gpt-4o-mini | $0.001 | $0.004 | $0.01 |
| QA | gpt-4o-mini | $0.001 | $0.003 | $0.01 |
| Overview | Gemini Flash | $0.001 | $0.002 | $0.01 |
| Build (9–11) | gpt-4o-mini | $0.001 | $0.002 | $0.01 |
| Review | DeepSeek V3.2 | $0.001 | $0.004 | $0.01 |
| Adversary | DeepSeek V3.2 | $0.001 | $0.003 | $0.01 |
| Monitor | Gemini Flash | <$0.001 | $0.001 | $0.00 |
| **Cycle** | | **$0.03** | **$0.08** | **$0.23** |

UI is the largest value-pack line (4 screens × 3). Sonnet is $0.

### What the $4 does **not** include

**2 revises / step = humans sending the artefact back at a gate.** Intake redo, new BRD, new screens, new tickets. That is already in the $1.15 / $4.

**Test fails and bug fixes in this demo do not call a model.** Gate 5 can loop up to **5 times** per ticket (`MAX_LOOPS`). Each pass is scanners + a Python `_heal` (strip a secret marker, add `export`, add a down-script comment). Review bench and adversary in that loop are also Python checks, not Sonnet. Extra token cost ≈ **$0**.

**Not in the $4**

| If this happens | Extra model cost |
|---|---|
| Heal loop, assembler still writes the app | **$0** |
| Gate 6 UAT reject → one more assembler pass | **$0** |
| Human revises already counted (2 per step) | already in $4 |
| Hosted coding agent rewrites files after a red test (up to 5 loops) | **+$2–20** per cycle |
| Bug is in the screens / BRD, not the stamp — UI or BRD runs again beyond the 2 revises | **+$0.60** per extra Sonnet screen set, **+$0.21** per extra Sonnet BRD (listed) |
| Extra screens past four | another Sonnet call each |

**Plan $4** = one requirement, quality models, 2 human revises per step, assembler writes code, heal is free. It is **not** “every CI red + every production bug.”

If you turn a coding agent on and assume 3 of 5 heal loops fire, treat one SDLC as **$4 + ~$10**, not $4.

**$14 breakdown** (planning number, not an invoice)

| Piece | What | Plan |
|---|---|---|
| Quality factory | 11 model agents, 2 human revises, assembler writes the app | **$4** |
| Coding agent | OpenHands / SWE-agent on Gate 5, Sonnet-class, fat repo + tool traces | **~$10** |
| **One SDLC** | | **~$14** |

`$4` is the 3× slack column already in the quality table. Biggest lines: UI $1.78, BRD $0.62, intake $0.41, review $0.19, overview $0.18, adversary $0.15. Rest $0.15.

`$10` assumes ~8 tickets (4 screens, APIs, schema, AI), one first write each, then **3 heal turns on ~3 tickets that fail**. One turn ≈ 40k in / 8k out on Sonnet ($0.24 listed, $0.72 slack). 8 writes ≈ $6. 9 heals ≈ $6. Rounded to **$10** so the SDLC stays a round number. Listed (no slack) for that coding work is ~$4.

If the coding agent is Qwen, those same 17 turns are ~$0.50, not $10 — the lump is **Sonnet-class OpenHands**, not Qwen. Five heal loops on every ticket → ~$20+. Assembler only → drop the $10, one SDLC is **$4**.

---

## All 14 roles

| # | Role | Settings | Best (no quality cut) | Best value | 2-run bake-off |
|---|---|---|---|---|---|
| 1 | Intake | `intake` | **Haiku 4.5** | Gemini Flash | **$1.50** |
| 2 | BRD | `brd` | **Sonnet 4.6** | DeepSeek V3.2 | **$1.00** |
| 3 | Architect | `architect` | **gpt-4.1-mini** | DeepSeek V3.2 | **$1.00** |
| 4 | UI / UX | `ui` | **Sonnet 4.6** | Qwen3-Coder (Groq / Ollama) | **$2.00** |
| 5 | DevOps | `devops` | **gpt-4.1-mini** | Gemini Flash | **$0.20** |
| 6 | Decomposer | `decomposer` | **gpt-4.1-mini** | gpt-4o-mini | **$0.50** |
| 7 | QA | `qa` | **Qwen3-Coder** | gpt-4o-mini | **$0.30** |
| 8 | Overview | `overview` | **Sonnet 4.6** | Gemini Flash | **$0.25** |
| 9 | AI Engineer | `build` | Assembler writes the file. Qwen only if you turn a coding agent on. | Skip the model | *see 11* |
| 10 | Developer | `build` | same | same | *see 11* |
| 11 | Migration Author | `build` | Assembler writes the down-script | Skip the model | **$0.15** |
| 12 | Review | `review` | **Sonnet 4.6** | DeepSeek V3.2 | **$0.25** |
| 13 | Adversary | `adversary` | **Sonnet 4.6** | DeepSeek / mini | **$0.20** |
| 14 | Monitor | `monitor` | **Haiku 4.5** | Gemini Flash-Lite | **$0.15** |

**Sum of bake-off pots = $7.50.** Round to **$10** for slack.

Client forbids DeepSeek → swap those rows to `gpt-4.1-mini`. Local / $0 → deterministic + Ollama `qwen2.5-coder:14b`.

---

## 1. Intake

**Job.** Multi-turn scope. One question per turn. JSON. Cap is **10 calls** (≤9 questions; last call writes the scope report).

**Best.** `anthropic/claude-haiku-4.5` — stays in the question budget, valid JSON.

**Best value.** `google/gemini-2.5-flash` (or `openai/gpt-4o-mini`).

**Five.** Flash, gpt-4o-mini, DeepSeek V3.2, Haiku 4.5, Sonnet 4.6.

Sonnet is in the five as the quality control. It does not beat Haiku on this job enough to be the default.

| Model | Why | Load (2 × 10 calls, 3×) |
|---|---|---|
| Gemini Flash | Cheapest usable | ~$0.04 |
| gpt-4o-mini | Reliable floor | ~$0.06 |
| DeepSeek V3.2 | Cheap, long context | ~$0.08 |
| Haiku 4.5 | Quality winner | ~$0.35 |
| Sonnet 4.6 | Control | ~$0.95 |

**Pot: $1.50.**

---

## 2. BRD

**Job.** Long structured BRD, REQ ids. **2 calls** (refine + critique). `CYCLE_USAGE`: 4k in / 1.5k out per call.

**Best.** `anthropic/claude-sonnet-4.6` — strongest prose a client will read.

**Best value.** `deepseek/deepseek-v3.2`.

**Five.** DeepSeek, gpt-4o-mini, gpt-4.1-mini, Haiku, Sonnet.

| Model | Why | Load (2 BRDs, 3×) |
|---|---|---|
| DeepSeek V3.2 | Best value. Factory default. | $0.02 |
| gpt-4o-mini | Floor | $0.02 |
| gpt-4.1-mini | Best OpenAI schema / REQ ids | $0.05 |
| Haiku 4.5 | Claude if DeepSeek is blocked | $0.14 |
| Sonnet 4.6 | Quality winner | $0.41 |

**Pot: $1.** Sonnet is ~65% of it. Do not add Opus.

---

## 3. Architect

**Job.** Lock, modules, entities, contracts, ADRs are **code** (`architect.decide`). Model writes **one architecture note** (1 call: 5k in / 2k out). Sonnet cannot change the locked stack.

**Best.** `openai/gpt-4.1-mini` — stays on-profile (node / python).

**Best value.** `deepseek/deepseek-v3.2`.

**Five.** 4.1-mini, DeepSeek, gpt-4o-mini, Haiku, Sonnet (best *note*, not the lock).

| Model | Why | Load (2 notes, 3×) |
|---|---|---|
| gpt-4.1-mini | Best for this job. Default. | $0.03 |
| DeepSeek V3.2 | Best value | $0.01 |
| gpt-4o-mini | Floor | $0.01 |
| Haiku 4.5 | Tight risks / NFRs | $0.09 |
| Sonnet 4.6 | Best note a reviewer will read | $0.27 |

**Pot: $1.** Score the note: invents Java/.NET? skips BRD entities? ignores refused off-profile hits?

---

## 4. UI / UX

**Job.** Real JSX, one screen per call, design tokens. Gate 3 three roles sign these screens. The assembler copies this JSX into the app. **This is the walkthrough artefact — do not cheap it.** Bake-off: **4 screens × 2 apps**.

**Best.** `anthropic/claude-sonnet-4.6` — strongest layout, copy, and tokens a client will approve. Qwen is good JSX per dollar. It is not the quality pick when the screen *is* the product.

**Best value.** `qwen/qwen3-coder` (or Groq / Ollama `qwen2.5-coder:14b` at $0).

**Five.** Qwen3-Coder, gpt-4o-mini, DeepSeek V3.2, gpt-4.1-mini, Sonnet 4.6.

| Model | Why | Load (2 apps × 4 screens, 3×) |
|---|---|---|
| Qwen3-Coder | Best value. Tight JSX. | $0.06 |
| gpt-4o-mini | Floor | $0.05 |
| DeepSeek V3.2 | Cheap long context | $0.05 |
| gpt-4.1-mini | Best OpenAI on screens | $0.13 |
| Sonnet 4.6 | Quality winner. Most of this pot. | $1.19 |

**Pot: $2.** Sonnet is ~80% of it. Do not add Opus.

---

## 5. DevOps

**Job.** Sprint 0 / UAT / rollback notes. 1 call: 2k in / 0.6k out. Two dummy runs per model.

**Best.** `openai/gpt-4.1-mini`.

**Best value.** Gemini Flash.

**Five.** Flash, gpt-4o-mini, DeepSeek V3.2, gpt-4.1-mini, Haiku 4.5. Sonnet does not improve this artefact enough to pay.

| Model | Why | Load (2 runs, 3×) |
|---|---|---|
| Gemini Flash | Best value | $0.003 |
| gpt-4o-mini | Floor | $0.004 |
| DeepSeek V3.2 | Cheap notes | $0.005 |
| gpt-4.1-mini | Quality winner | $0.01 |
| Haiku 4.5 | Tight lists | $0.03 |

**Pot: $0.20.**

---

## 6. Decomposer

**Job.** Tickets from *this* BRD and *these* screens. Path allow-lists. 1 call: 3.5k in / 1.2k out.

**Best.** `openai/gpt-4.1-mini`.

**Best value.** `openai/gpt-4o-mini`.

**Five.** gpt-4o-mini, DeepSeek V3.2, gpt-4.1-mini, Haiku 4.5, Sonnet 4.6.

| Model | Why | Load (2 runs, 3×) |
|---|---|---|
| gpt-4o-mini | Best value | $0.01 |
| DeepSeek V3.2 | Cheap tickets | $0.01 |
| gpt-4.1-mini | Quality winner. This BRD, these screens. | $0.02 |
| Haiku 4.5 | Tight path lists | $0.06 |
| Sonnet 4.6 | Control. Stronger prose, same tickets. | $0.17 |

**Pot: $0.50.**

---

## 7. QA

**Job.** Tests before code. Eight cases, one critical, real screen names. 1 call: 2.5k in / 0.8k out.

**Best.** `qwen/qwen3-coder` — names the real screens. Sonnet in the five as control.

**Best value.** `openai/gpt-4o-mini`.

**Five.** Qwen3-Coder, gpt-4o-mini, DeepSeek V3.2, gpt-4.1-mini, Haiku 4.5.

| Model | Why | Load (2 runs, 3×) |
|---|---|---|
| Qwen3-Coder | Quality winner. Screen names stay exact. | $0.01 |
| gpt-4o-mini | Best value | $0.01 |
| DeepSeek V3.2 | Cheap cases | $0.01 |
| gpt-4.1-mini | Stronger schema | $0.01 |
| Haiku 4.5 | Tight eight-case list | $0.04 |

**Pot: $0.30.**

---

## 8. Overview

**Job.** Non-gating stream briefing. Humans read this. 1 call: 2.5k in / 0.8k out.

**Best.** `anthropic/claude-sonnet-4.6`.

**Best value.** Gemini Flash.

**Five.** Flash, gpt-4o-mini, DeepSeek V3.2, Haiku 4.5, Sonnet 4.6.

| Model | Why | Load (2 runs, 3×) |
|---|---|---|
| Gemini Flash | Best value | $0.003 |
| gpt-4o-mini | Floor | $0.01 |
| DeepSeek V3.2 | Cheap briefing | $0.01 |
| Haiku 4.5 | Tight summary | $0.04 |
| Sonnet 4.6 | Quality winner | $0.12 |

**Pot: $0.25.**

---

## 9–11. AI Engineer · Developer · Migration Author

**Job.** These three names own tickets (AI module, app code, migration). They are **not** three live coder models. Gate 5 is a Python assembler: it writes the app from the BRD, screens, and tickets. OpenHands / SWE-agent stay off unless you set their URLs. One Settings key: `build`. One cheap call only if you still ask a model for a one-line engine note (2k in / 0.4k out).

**Best.** Leave the assembler on. The screens already came from Sonnet. Do not pay a second coder model to rewrite them.

**Best value.** Skip the model. The files still appear.

**Five (only if you call a model).** gpt-4o-mini, Qwen3-Coder, DeepSeek V3.2, gpt-4.1-mini, Haiku 4.5. Do not put Opus or 4o-full here.

| Model | Why | Load (2 notes, 3×) |
|---|---|---|
| gpt-4o-mini | Floor if you want a note | $0.003 |
| Qwen3-Coder | Only if a hosted coding agent is on | $0.003 |
| DeepSeek V3.2 | Cheap note | $0.004 |
| gpt-4.1-mini | Stronger note | $0.01 |
| Haiku 4.5 | Tight one-liner | $0.02 |

**Pot: $0.15** once for all three roles.

---

## 12. Review

**Job.** Four lanes: correctness, security, architecture, quality. 1 call: 3k in / 0.8k out.

**Best.** `anthropic/claude-sonnet-4.6`.

**Best value.** `deepseek/deepseek-v3.2`.

**Five.** DeepSeek V3.2, gpt-4o-mini, gpt-4.1-mini, Haiku 4.5, Sonnet 4.6.

| Model | Why | Load (2 runs, 3×) |
|---|---|---|
| DeepSeek V3.2 | Best value | $0.01 |
| gpt-4o-mini | Floor | $0.01 |
| gpt-4.1-mini | Stronger schema | $0.01 |
| Haiku 4.5 | Tight four lanes | $0.04 |
| Sonnet 4.6 | Quality winner | $0.13 |

**Pot: $0.25.**

---

## 13. Adversary

**Job.** Prove the artefact wrong. 1 call: 2.5k in / 0.6k out.

**Best.** `anthropic/claude-sonnet-4.6`.

**Best value.** DeepSeek or mini.

**Five.** DeepSeek V3.2, gpt-4o-mini, gpt-4.1-mini, Haiku 4.5, Sonnet 4.6.

| Model | Why | Load (2 runs, 3×) |
|---|---|---|
| DeepSeek V3.2 | Best value | $0.01 |
| gpt-4o-mini | Floor | $0.004 |
| gpt-4.1-mini | Stronger attacks | $0.01 |
| Haiku 4.5 | Fast contradictions | $0.03 |
| Sonnet 4.6 | Quality winner | $0.10 |

**Pot: $0.20.**

---

## 14. Monitor

**Job.** Errors / latency / spend hint. 1 call: 1.5k in / 0.4k out.

**Best.** `anthropic/claude-haiku-4.5`. Sonnet is in the five; it does not help this artefact.

**Best value.** Gemini Flash-Lite (or Flash).

**Five.** Flash, gpt-4o-mini, DeepSeek V3.2, Haiku 4.5, Sonnet 4.6.

| Model | Why | Load (2 runs, 3×) |
|---|---|---|
| Gemini Flash | Best value | $0.002 |
| gpt-4o-mini | Floor | $0.003 |
| DeepSeek V3.2 | Cheap hint | $0.004 |
| Haiku 4.5 | Quality winner | $0.02 |
| Sonnet 4.6 | Control. Does not help. | $0.06 |

**Pot: $0.15.**

---

## Settings IDs (copy)

**Quality winners (bake-off / walkthrough)**

```
intake        anthropic/claude-haiku-4.5
brd           anthropic/claude-sonnet-4.6
architect     openai/gpt-4.1-mini
ui            anthropic/claude-sonnet-4.6
devops        openai/gpt-4.1-mini
decomposer    openai/gpt-4.1-mini
qa            qwen/qwen3-coder
overview      anthropic/claude-sonnet-4.6
build         openai/gpt-4o-mini
review        anthropic/claude-sonnet-4.6
adversary     anthropic/claude-sonnet-4.6
monitor       anthropic/claude-haiku-4.5
```

**Best value (cheap test)**

```
intake        google/gemini-2.5-flash
brd           deepseek/deepseek-v3.2
architect     deepseek/deepseek-v3.2
ui            qwen/qwen3-coder
devops        google/gemini-2.5-flash
decomposer    openai/gpt-4o-mini
qa            openai/gpt-4o-mini
overview      google/gemini-2.5-flash
build         openai/gpt-4o-mini
review        deepseek/deepseek-v3.2
adversary     deepseek/deepseek-v3.2
monitor       google/gemini-2.5-flash
```

Factory `QUALITY_DEFAULTS` stays on the cheap set (DeepSeek BRD, no Sonnet). Use the quality-winners block only when you are scoring artefacts.

---

## Azure · alternative

Same 14 roles, Microsoft Foundry / Azure OpenAI instead of OpenRouter. Catalog 23 Sep 2026: [models sold by Azure](https://learn.microsoft.com/en-us/azure/ai-foundry/model-inference/concepts/models), [Claude on Foundry](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/claude-models). Region + TPM quota still decide what *you* can deploy.

### OpenRouter vs Azure — cheaper for **testing**?

**OpenRouter is cheaper and faster to start.** Azure is for a SoW that must say Azure, or leftover PAYG credits with real quota.

| | OpenRouter | Azure Foundry |
|---|---|---|
| Full 14-role bake-off pot | **$10** | **$15–20** if quota exists (DeepSeek ~2–4× list; no Gemini / no Qwen3-Coder) |
| Value cycle + 2 revises (listed) | **$0.08** | **$0.17** |
| Same, 3× token slack | **$0.25** | **$0.50** |
| Quality cycle + 2 revises (listed) | **$1.15** | **$1.23** (Sonnet on UI) |
| Same, 3× slack | **$3.46** | **$3.70** |
| $0 start | `:free` models + deterministic | **Usually no** — Free Trial = **0 TPM** |
| One key, mix vendors | Yes | Deploy each SKU. Marketplace for Claude / DeepSeek. |
| Gemini Flash / Qwen3-Coder | Yes | **Not on Azure.** Nano / Codex instead. |

Same SKU is not cheaper on Azure. Haiku / Sonnet / gpt-5-nano list is **the same** ($1/$5, $3/$15, $0.05/$0.40). Azure **DeepSeek-V3.2** is **$0.58/$1.68** vs OpenRouter **~$0.21–0.28 / $0.31–0.42**. gpt-5.4-mini ($0.75/$4.50) costs more than OpenRouter gpt-4.1-mini ($0.40/$1.60). gpt-5-codex ($1.25/$10) costs more than Qwen3-Coder ($0.12/$0.80).

**$200 Azure trial credit does not beat $10 OpenRouter.** Free Trial / Azure Pass often has **0 TPM** — credit sits, deploy fails (“Quota Not Met”). Unlock = **Pay-As-You-Go**, then the $200 can spend. [Microsoft Q&A](https://learn.microsoft.com/en-in/answers/questions/5907451/free-trials-get-200-but-have-a-0-quota-limit-on-ll)

**Use Azure only if:** SoW names Azure, or PAYG + non-zero quota (or ~$1k Foundry startup credits). Then pick the Azure IDs below. Do not put Opus / gpt-5.4-pro / gpt-5.6-* on these agents.

### Free tier?

**No perpetual free LLM SKU on Azure.** Foundry portal is free. Tokens bill. None of the rows below are “free tier included.”

| Azure model | Free trial? |
|---|---|
| `claude-haiku-4-5`, `claude-sonnet-5` / `4-6` | No. Paid + quota. Trial often blocked. |
| `gpt-5.4-mini`, `gpt-5.3-codex`, `gpt-5-mini` / `nano` | No. Same. |
| `gpt-4.1-mini` | No. Students sometimes **1K TPM**; trial usually **0**. |
| `DeepSeek-V3.2` / V4 Flash | No. Serverless, still billed. |
| `Phi-4-mini-instruct` | Not a free SKU. Sometimes the only catalog row a trial can *see*. |

`$0` for this factory stays: deterministic adapter, Ollama `qwen2.5-coder:14b`, Groq / Gemini free caps, OpenRouter `:free`.

### Per agent on Azure

**Best** = strongest Azure SKU for that artefact. **Value** = cheapest Azure SKU that still works. Azure-hosted = data on Azure. `claude-sonnet-4-6` is Foundry → Anthropic-hosted.

| # | Role | Best (Azure) | Best value (Azure) |
|---|---|---|---|
| 1 | Intake | `claude-haiku-4-5` (Azure-hosted, GA) | `gpt-5-nano` / `gpt-5.4-nano` |
| 2 | BRD | `claude-sonnet-4-6` or `claude-sonnet-5` (Azure-hosted) | `DeepSeek-V3.2` / `DeepSeek-V4-Flash` |
| 3 | Architect | `gpt-4.1-mini` or `gpt-5.4-mini` | `DeepSeek-V3.2` |
| 4 | UI | `claude-sonnet-5` / `claude-sonnet-4-6` | `gpt-5-mini` |
| 5 | DevOps | `gpt-5.4-mini` | `gpt-5-nano` |
| 6 | Decomposer | `gpt-5.4-mini` / `gpt-4.1-mini` | `gpt-5-mini` |
| 7 | QA | `gpt-5.3-codex` | `gpt-5-mini` |
| 8 | Overview | `claude-sonnet-5` | `gpt-5-nano` |
| 9–11 | Build | Assembler writes the app. `gpt-5-mini` only for a short note. | Skip / `Phi-4-mini-instruct` |
| 12 | Review | `claude-sonnet-5` / `4-6` | `DeepSeek-V3.2` |
| 13 | Adversary | same Sonnet | `DeepSeek-V3.2` |
| 14 | Monitor | `claude-haiku-4-5` | `Phi-4-mini-instruct` / nano |

Also on Foundry, not needed: Llama 3.3 70B, Llama 4 Maverick, Mistral Large 3, Grok, `model-router` (loses per-agent control). `gpt-4o-mini` is deprecated (retire 14 Apr 2027) — use nano / 5.x.

### Azure cycle once models are selected (+ 2 revises / step)

Same assumptions as OpenRouter: intake 10 calls, UI 4 screens on Sonnet, assembler writes the app (no hosted coding agent), 1 + 2 revises. Azure value uses nano + DeepSeek + gpt-5-mini (no Gemini / Qwen). Azure quality uses Haiku / Sonnet / 5.4-mini.

| Pack | First pass | + 2 revises | Plan (3× slack) |
|---|---|---|---|
| OpenRouter value | $0.03 | **$0.08** | **$0.25** |
| Azure value | $0.06 | **$0.17** | **$0.50** |
| OpenRouter quality | $0.38 | **$1.15** | **$4** |
| Azure quality (Sonnet on UI) | $0.41 | **$1.23** | **$4** |

10 live reqs, quality + 2 revises: OpenRouter **~$40**. Azure **~$40**.

### Settings IDs (Azure)

**Quality (stay Azure-hosted)**

```
intake        claude-haiku-4-5
brd           claude-sonnet-5
architect     gpt-5.4-mini
ui            claude-sonnet-5
devops        gpt-5.4-mini
decomposer    gpt-5.4-mini
qa            gpt-5.3-codex
overview      claude-sonnet-5
build         gpt-5-mini
review        claude-sonnet-5
adversary     claude-sonnet-5
monitor       claude-haiku-4-5
```

**Value (Azure bill only)**

```
intake        gpt-5-nano
brd           DeepSeek-V3.2
architect     DeepSeek-V3.2
ui            gpt-5-mini
devops        gpt-5-nano
decomposer    gpt-5-mini
qa            gpt-5-mini
overview      gpt-5-nano
build         gpt-5-nano
review        DeepSeek-V3.2
adversary     DeepSeek-V3.2
monitor       Phi-4-mini-instruct
```

Must **deploy** each SKU. Catalog row ≠ live endpoint. Factory demo path stays LiteLLM → OpenRouter unless you wire `AZURE_API_KEY` / `AZURE_API_BASE`.

---

## Do not buy for this test

- Claude Opus, GPT-4.1 / GPT-4o full, o3-style reasoning as a default.
- A hosted coding agent on Gate 5 “just to see.” The assembler already writes the app.
- A RunPod box left on overnight.
- Azure Free Trial as the test path (0 TPM). Pay-As-You-Go or OpenRouter.
