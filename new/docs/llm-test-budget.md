# LLM picks and test budget

Per-agent **best** vs **best value**, then what cash you need to test this factory.

Full 14-role bake-off (quality vs value + $10 pot): [agent-model-bakeoff.md](agent-model-bakeoff.md). Two pots ($10 pick vs $4 / $15 run), Azure 0 TPM, and which models are free: [Can I test all models for free?](#can-i-test-all-models-for-free).

Source of token counts: `phase1/agent_settings.py` `CYCLE_USAGE` (17 calls, ~49k prompt + ~15k completion per full cycle). The 11 planning/review agents call a model. **Gate 5 writes the app with a Python assembler**, not a hosted coding agent — that is why build barely appears in the bill. Prices: OpenRouter / vendor list, 23 Sep 2026. Real UI (one call per screen) and Gate 3 revises run higher — use the **3× buffer**.

Do **not** put GPT-4.1 full or Opus on every agent. No Sonnet as a factory default.

---

## Per agent — quality vs value (14 roles)

Architecture names **14 roles**. Settings has **12 keys**. AI Engineer, Developer, and Migration Author share `build` (one bake-off, not three).

**Best** = strongest for that artefact. Sonnet is listed where it actually wins (long prose, review). It is **not** listed for JSX or stack-lock — Qwen / 4.1-mini beat it there. Opus is never best here (slower, not better JSON).

Bake-off on every agent: **2 dummy runs × the five models** (quality winner always included). **Load = 3× listed.** UI = 4 screens × 2 apps.

| # | Role | Settings key | Best (no quality cut) | Best value | 2-run bake-off load |
|---|---|---|---|---|---|
| 1 | Intake | `intake` | **Haiku 4.5** (one question, JSON). Sonnet in the five. | Gemini Flash | **$1.50** (2×10 calls; Flash, mini, DeepSeek, Haiku, Sonnet) |
| 2 | BRD | `brd` | **Sonnet 4.6** | DeepSeek V3.2 | **$1.00** |
| 3 | Architect | `architect` | **gpt-4.1-mini** (stays on-profile). Sonnet best *note*. | DeepSeek V3.2 | **$1.00** |
| 4 | UI / UX | `ui` | **Sonnet 4.6** | Qwen3-Coder (Groq/Ollama) | **$2.00** (screens are the walkthrough — Sonnet is the quality pick) |
| 5 | DevOps | `devops` | **gpt-4.1-mini** | Gemini Flash | **$0.20** |
| 6 | Decomposer | `decomposer` | **gpt-4.1-mini** (this BRD, these screens). Sonnet in the five. | gpt-4o-mini | **$0.50** |
| 7 | QA | `qa` | **Qwen3-Coder** | gpt-4o-mini | **$0.30** |
| 8 | Overview | `overview` | **Sonnet 4.6** (human briefing) | Gemini Flash | **$0.25** |
| 9 | AI Engineer | `build` | Assembler writes the file. Qwen only if you turn a coding agent on. | Skip the model | *see 11* |
| 10 | Developer | `build` | same | same | *see 11* |
| 11 | Migration Author | `build` | Assembler writes the down-script | Skip the model | **$0.15** (one pot for 9–11) |
| 12 | Review | `review` | **Sonnet 4.6** | DeepSeek V3.2 | **$0.25** |
| 13 | Adversary | `adversary` | **Sonnet 4.6** | DeepSeek / mini | **$0.20** |
| 14 | Monitor | `monitor` | **Haiku 4.5** (Sonnet in the five; does not help) | Gemini Flash-Lite | **$0.15** |

Five models used across bake-offs (not 14 different SKUs): Gemini Flash, gpt-4o-mini, DeepSeek V3.2, Qwen3-Coder, gpt-4.1-mini, Haiku 4.5, Sonnet 4.6.

**If the client forbids DeepSeek:** swap those rows to 4.1-mini. **Local / $0:** deterministic + Ollama `qwen2.5-coder:14b`.

### Total cash to test every agent

| How you test | What you run | Load |
|---|---|---|
| **Value only** | Best-value model, 2 runs each role | **$1** |
| **Quality only** | Best model only, 2 runs each role (Sonnet on BRD / overview / review / adversary) | **$3** |
| **Full bake-off (recommended)** | 2 runs × five models on every role | **$10** |
| Paid coding agent on Gate 5, or Opus on every role | Do not | $100+ |

**Put $10 on OpenRouter.** That is the number. Leftover stays as credit.

Quality-only (~$3) is enough if you already trust the cheaper models and only need to see Sonnet on UI / BRD / overview / review / adversary. Full $10 is if you want the side-by-side on every agent.

Factory **defaults** stay cheap (DeepSeek BRD, no Sonnet in `QUALITY_DEFAULTS`). Use Sonnet in Settings only on the bake-off days.

**Two pots. Do not add them.**

| What | Cash | What it buys |
|---|---|---|
| **Test / pick models** | **$10** | 2 dummy runs × 5 models on every role. One-time. Leftover stays on OpenRouter. |
| **1 full SDLC after you picked** | **$4** | Quality models, 2 human revises, Python writes the app. **This is the demo.** |
| 1 full SDLC **+ coding agent** | **~$14–15** | The $4 plus OpenHands heals. |

The $10 is not part of every SDLC. 10 live requirements, assembler only: **~$40**, plus the one-time **$10** if you have not done the bake-off.

---

## Can I test all models for free?

**No.** The bake-off is seven models. Free APIs only cover some.

| Model | Free? |
|---|---|
| Gemini Flash | **Yes** — Google AI Studio |
| Qwen3-Coder / Llama | **Yes** — Groq, OpenRouter `:free`, or Ollama |
| DeepSeek V3.2 | **Sometimes** — OpenRouter `:free` if listed; paid if not |
| gpt-4o-mini | **No** API free tier |
| gpt-4.1-mini | **No** |
| Haiku 4.5 | **No** |
| **Sonnet 4.6** | **No** |

Sonnet is the quality pick for UI, BRD, overview, review, adversary. No vendor gives that API away.

**$0** = Flash + Qwen/Llama + assembler. That is the value pack, not the full bake-off.

**All seven, including Sonnet:** **$10 on OpenRouter.** Cheapest way. Azure trial will not do it (0 TPM).

### Why Azure free will not work

Microsoft sets LLM quota to **0 TPM** on free/trial offers. The $200 credit does not buy tokens-per-minute.

[Foundry quotas](https://learn.microsoft.com/en-us/azure/foundry/openai/quotas-limits):

| Offer | Azure OpenAI / Foundry GPT |
|---|---|
| **Free trial** (`FreeTrial_2014-09-01`) | **All models: 0 TPM** |
| **Lightweight trial** | **All models: 0 TPM** |
| **Azure Pass** | **All models: 0 TPM** |
| Azure for Students | 1K TPM on some models. **0** on o-series, GPT-4.1 |
| Pay-as-you-go | Real TPM |

**0 TPM = you cannot deploy.** Portal shows the model. Deploy → **Quota Not Met.** Credit sits unused. Credit and quota are two switches; trial is 0 to stop abuse. Unlock = **Pay-As-You-Go**, then leftover $200 can spend. [Q&A](https://learn.microsoft.com/en-in/answers/questions/5907451/free-trials-get-200-but-have-a-0-quota-limit-on-ll)

There is also **no free SKU** for Haiku, Sonnet, GPT-5, DeepSeek. Foundry UI is free. Inference bills when quota exists.

**To test models: OpenRouter $10. Not Azure free.**

### Free credits on other providers

| Provider | Free what | Works for this factory? |
|---|---|---|
| **Google AI Studio** ([Gemini API](https://ai.google.dev/gemini-api/docs/pricing)) | Flash tokens free, daily cap | **Yes** — intake, overview, DevOps, monitor (value) |
| **Groq** | Llama / Qwen, RPM + daily token cap | **Yes** — UI/QA value. Hits the wall mid-day |
| **OpenRouter `:free`** | 50 req/day, no card | **Partial.** No Sonnet. 50 calls ≈ one intake, not 14 × 5 |
| **OpenRouter + $10 parked** | Same free models, **1,000 req/day**. Money stays yours | Best $0-token path if you already planned the $10 pot |
| **Ollama on your GPU** | `qwen2.5-coder:14b` | **Yes** — UI/QA. $0 |
| **AWS Activate** ([Founders $1k](https://startups.aws.com/lp/aws-activate-credits)) | Bedrock (Claude, Llama, …) | **Maybe.** Must be a startup. Apply, wait. Not same-day |
| **Google Cloud $300 trial** | Vertex Gemini if you upgrade to PAYG | Gemini only. Not Sonnet. Credits ≠ AI Studio |
| **Azure $200 trial** | Credit with **0 TPM** | **No** |
| **OpenAI / Anthropic API** | No real free API. Chat apps ≠ API | **No** for bake-off |

Practical: Gemini + Groq + Ollama this week. Put **$10 on OpenRouter** when you need Sonnet / Haiku / 4.1-mini. Apply to AWS Activate only if you already qualify as a startup.

---

## What one cycle costs (listed)

| Pack | Who runs what | Listed USD / cycle | After 3× buffer |
|---|---|---|---|
| **A · Deterministic** | No paid model | $0 | $0 |
| **B · Best value** | Mini everywhere; Qwen on UI; Gemini on intake if wired | **~$0.016 – $0.02** | **~$0.05** |
| **C · Best per agent** | Haiku intake/review/adversary, DeepSeek BRD, 4.1-mini architect/decomposer, Qwen UI/QA, mini elsewhere | **~$0.05** | **~$0.16** |
| **D · Frontier everywhere** | Same expensive model on all 12 — do not | — | — |

Pack C is the Settings “quality” set. It stays under $1/cycle on purpose.

A hosted coding agent (OpenHands / SWE-agent) is **not** in these numbers. Gate 5 uses the Python assembler. Turn a coding agent on and the pot is $2–20+ per cycle. Leave it off to test the factory.

---

## Approximated budget to **test**

Goal: prove intake → seven gates → artefacts → catalog, with real model text on BRD/UI, not a production tenant.

| What you are testing | Cycles you will actually burn | Pack | Cash to load | What it covers |
|---|---|---|---|---|
| Gate / CI only | Unlimited | A | **$0** | All seven gates, preview, SQLite app, scanners. Prose is stub. |
| Wiring + a few happy paths | ~10 listed / ~5 buffered | B | **$2 – $5** | Several full loops on mini + Qwen UI. |
| **Recommended test pot** | ~40 buffered value cycles, or ~15 quality dress rehearsals | B, then C for 2–3 runs | **$10** | Weeks of local testing. Extra screens, a few revises, retries. |
| Client walkthrough week | ~10 buffered C cycles + retries | C | **$10** | Live BRD (DeepSeek) + screens. No Sonnet. |
| Hosted coding agent, or one huge model on all agents | Do not use this to “test the factory” | D or engines | $100+ | Product experiment, not gate QA. |

**Practical load: $10 on OpenRouter.** Covers value testing and a DeepSeek walkthrough. $0 if you stay deterministic.

### How the $10 is spent (value pack)

- ~600 listed mini cycles, or ~200 after the 3× buffer.
- Or ~80 listed / ~28 buffered Pack C cycles if you switch Settings to the quality set for dress rehearsal.
- One $10 OpenRouter top-up also lifts `:free` rate limits (50 → 1,000 req/day) if you mix free models.

### BRD bake-off — 2 BRDs × each of the five

One BRD is **2 calls** (refine the template + critique). Tokens in `CYCLE_USAGE`: 4k in / 1.5k out per call. Real drafts can be fatter (refine may send ~8–14k of markdown) — use the **3×** column.

`Settings → brd` only. Other agents stay mini. Sonnet is **this comparison only**, not the factory default.

| Rank | Model | Why it is in the five | 1 BRD listed | 2 BRDs listed | Load (3×) | Quality / value |
|---|---|---|---|---|---|---|
| 1 | `deepseek/deepseek-v3.2` | Long structured docs, cheap output | $0.0035 | $0.007 | **$0.02** | Best value. Default BRD. |
| 2 | `openai/gpt-4o-mini` | Reliable headings, cheapest OpenAI | $0.003 | $0.006 | **$0.02** | Floor. Fine for wiring. |
| 3 | `openai/gpt-4.1-mini` | Stronger schema / REQ ids than mini | $0.008 | $0.016 | **$0.05** | Best OpenAI on this job. |
| 4 | `anthropic/claude-haiku-4.5` | Tight structure, Claude family | $0.023 | $0.046 | **$0.14** | Quality if DeepSeek is blocked. |
| 5 | `anthropic/claude-sonnet-4.6` | Best prose a client will read | $0.069 | $0.138 | **$0.41** | Quality winner. Not the default. |

**All five × 2 BRDs:** listed **~$0.21**. **Put $1 on OpenRouter.** Sonnet is ~65% of that pot.

Do not add Opus or GPT-4.1 full. They do not improve REQ-id hygiene enough to pay.

### Architect bake-off — 2 notes × each of the five

Lock, modules, entities, contracts, ADRs are **code** (`architect.decide`). The model writes one **architecture note** (risks, modules, NFRs) from the BRD. **1 call** per run (`CYCLE_USAGE`: 5k in / 2k out). Two dummy BRDs = 2 calls per model.

`Settings → architect` only. Sonnet is in this bake-off because it writes the strongest note — it cannot change the locked stack.

| Rank | Model | Why it is in the five | 1 note listed | 2 notes listed | Load (3×) | Quality / value |
|---|---|---|---|---|---|---|
| 1 | `openai/gpt-4.1-mini` | Best at staying on-profile | $0.005 | $0.010 | **$0.03** | Best for this job. Default. |
| 2 | `deepseek/deepseek-v3.2` | Long technical notes, cheap | $0.002 | $0.004 | **$0.01** | Best value. |
| 3 | `openai/gpt-4o-mini` | Cheap, reliable | $0.002 | $0.004 | **$0.01** | Floor. |
| 4 | `anthropic/claude-haiku-4.5` | Tight risks / NFR lists | $0.015 | $0.030 | **$0.09** | Strong structure. |
| 5 | `anthropic/claude-sonnet-4.6` | Best note a reviewer will read | $0.045 | $0.090 | **$0.27** | Quality winner on prose. Not the lock. |

**All five × 2 notes:** listed **~$0.14**. **Put $1 on OpenRouter.** Sonnet is ~65% of the pot.

Score the note: does it invent Java/.NET, skip entities from the BRD, or ignore refused off-profile hits? `decide()` still locks node/python either way.

### Cloud credits instead of cash

Same *models*, different bill. Not cheaper than $10 OpenRouter for the full bake-off. Useful if the SoW must say Azure/Bedrock.

**Testing: OpenRouter is cheaper than Azure.** Azure mapping: [agent-model-bakeoff.md — Azure · alternative](agent-model-bakeoff.md#azure--alternative). Azure **free trial cannot run the bake-off** (0 TPM — see above).

| Pot | Typical | Use on |
|---|---|---|
| Azure startup / Foundry (PAYG or approved quota) | ~$1,000 starter | `gpt-4o-mini`, `gpt-4.1-mini` — not the $200 trial |
| AWS Activate Founders | $1,000–$5,000 | Bedrock Haiku / Llama / DeepSeek if listed. Apply as a startup. |
| Google Cloud startup / $300 trial | Vertex after PAYG upgrade | Gemini Flash. Not Sonnet. Not AI Studio. |
| Groq / Gemini free tiers | $0 + caps | Value-pack agents only |
| RunPod GPU | ~$0.34–0.44 / hr | Air-gapped booth day. Idle week ≈ $60–80. Wrong tool for gate testing. |

---

## Settings mapping (copy into the UI)

**Best value (test)**

```
intake        google/gemini-2.5-flash     # or openai/gpt-4o-mini
brd           deepseek/deepseek-v3.2      # or openai/gpt-4o-mini
architect     openai/gpt-4o-mini
ui            qwen/qwen3-coder
decomposer    openai/gpt-4o-mini
qa            openai/gpt-4o-mini
devops        openai/gpt-4o-mini
overview      openai/gpt-4o-mini
build         openai/gpt-4o-mini
review        openai/gpt-4o-mini
adversary     openai/gpt-4o-mini
monitor       openai/gpt-4o-mini
```

**Best per agent (walkthrough)**

```
intake        anthropic/claude-haiku-4.5
brd           deepseek/deepseek-v3.2
architect     openai/gpt-4.1-mini
ui            anthropic/claude-sonnet-4.6
decomposer    openai/gpt-4.1-mini
qa            qwen/qwen3-coder
devops        openai/gpt-4o-mini
overview      openai/gpt-4o-mini
build         openai/gpt-4o-mini
review        anthropic/claude-haiku-4.5
adversary     anthropic/claude-haiku-4.5
monitor       openai/gpt-4o-mini
```

---

## Do not buy for testing

- Claude Opus, GPT-4.1 / GPT-4o full, o3-style reasoning as the default.
- Azure Free Trial as the test path (0 TPM).
- A RunPod box left on overnight.
- A hosted coding agent on Gate 5 “just to see.” The assembler already writes the app. Sonnet on UI/BRD is a Settings flip for bake-off / walkthrough, not a factory default.

Related: Settings → cycle cost in the app (`agent_settings.cycle_cost`). This file is the planning source.
