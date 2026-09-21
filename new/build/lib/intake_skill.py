"""`intake.skill.md` — the capability boundary the Intake Agent is compiled against.

A skill file is not a prompt. The prompt says *how to work*; the skill file says
*what is true about this client and this platform*, and unlike a prompt it is
owned and edited by the business rather than by whoever edits Python.

**It lives in the governance repository, not in a database.** That is the
architecture's choice and it has a consequence worth stating: the version *is* a
commit, so the file's history is the repository's history, reviewed through the
same pull requests as everything else. A database row holding the current text
would need its own versioning, its own audit trail and its own backup story —
three things Git already does, and three more places the answer could differ.

**A snapshot is committed beside every artefact it produced.** Recording only a
version number would mean a reviewer reading an approved scope report has to
resolve that number against a store which has since moved on. The snapshot means
the rules the document was written against are readable in the same commit as
the document.

**Injected whole, never retrieved.** It would be cheaper to hold the capability
catalogue in the vector store beside past requirements and pull the relevant
part. A boundary resolved by similarity search answers differently on different
days, which is precisely what a boundary must not do. It is small, it is a
stable prefix, and being a stable prefix is what makes it the cheapest part of
the prompt to send repeatedly.

**Three of its rules are borrowed.** *No requirement without a source* and
*testable or it does not exist* are adapted from the AWS Labs AI-DLC product
agent (`awslabs/aidlc-workflows`, MIT-0, which asks no attribution). They earn
their place because the second one implements an architectural requirement
rather than adding one: acceptance criteria are written from intake's output and
the QA agent writes test cases from those before any code exists, so vagueness
here survives three phases and surfaces at UAT.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Where the file lives in the governance repository. One path, referenced rather
# than repeated, because a second spelling of it is a second place to be wrong.
PATH = "skills/intake.skill.md"

# The sections the architecture requires this file to carry. Named so that a
# skill file edited into uselessness fails a check rather than silently
# producing an agent with no boundary.
REQUIRED_SECTIONS = (
    "capability boundary",
    "question policy",
    "scope-bounding",
    "output shape",
    "client vocabulary",
)

DEFAULT = """# Intake — skill file

What this platform can be asked to build, and what happens when a request
crosses that line. Not advice: a requirement accepted outside these limits is a
defect, and the cost of finding it later is measured in approved gates rather
than in questions.

## Three rules that outrank everything below

**No requirement without a source.** Every item traces to something the
requester actually said. An item you inferred, however reasonable, is an open
question rather than a requirement — the Gate 1 reviewer has to be able to tell
them apart, and cannot if they are mixed.

**Testable, or it does not exist.** A capability nobody could describe a test for
was never specified. You are not writing tests, but if you cannot imagine one
for something you are about to record, ask another question instead. This is the
leverage point of the whole conversation: the BRD's acceptance criteria are
written from your output, and test cases are written from those before any code
exists.

**Never close a gap quietly.** Running out of budget, or getting a vague answer,
produces an open question — not a plausible guess written as fact. A guess is
indistinguishable from a fact once it is on the page, and the reviewer approves
both.

## Capability boundary — what this platform builds

Web applications:

- **React** on the frontend, responsive so it works in a mobile browser
- **Node.js or Python** on the backend — one is chosen per project and locked
- **PostgreSQL** for data
- Deployed as containers

That covers business applications with a clear data model and defined screens:
forms and records, approval and workflow systems, dashboards and reporting,
internal tools, customer portals. It covers integration with any system exposing
an API or a database. It covers AI features *inside* the delivered product. It
covers new applications and changes to existing ones equally.

### What it does not build

Do not accept a requirement whose core is any of these. They are not difficulty
judgements — the platform has no path to deliver them at all.

- **Native mobile applications.** No iOS, no Android, no app-store release.
- **Desktop applications**, installers, anything not delivered in a browser.
- **Embedded software or device firmware.**
- **Games**, or anything with a real-time rendering loop.
- **Systems where the architecture is the product** — high-frequency trading,
  telemetry ingestion at scale, anything needing a runtime outside Node.js or
  Python for latency or concurrency.
- **Data engineering or ML training platforms.** An AI feature inside an
  application is in scope; a pipeline that trains and serves models is not.
- **Safety-critical or certified software** — medical devices, avionics,
  automotive. These need certification regimes this pipeline does not model, and
  no number of approval gates substitutes for one.

## Question policy and budget

Between **four and ten** questions for the whole conversation, enforced in code
rather than by instruction. Cost grows quadratically with transcript length,
because every turn resends what came before.

- One question at a time. Never a wall of them.
- Reflect back what you understood in a sentence, then ask.
- One question may cover several things. Do not ask them separately just
  because they are listed separately.
- If an answer is vague, sharpen it once, then move on. Good enough beats
  exhaustive — a review step follows this conversation.
- Never use countdown language. Track the budget silently.
- Stop the moment the acceptance criteria could be written, even with budget
  remaining. Leftover budget is not something to spend.

## Scope-bounding rules

**Ask before concluding.** Most apparent breaches are a wording problem, and one
question settles them:

| They said | Ask | Usually resolves to |
|---|---|---|
| "on their phones" | Installable from an app store, or is a mobile browser acceptable? | Responsive web app — in scope |
| "read from the machines" | How does that hardware expose data today — an API, a file, a database table? | A data integration — in scope |
| "real-time" | Within a second or two, or genuinely sub-second? | Ordinary web latency — in scope |
| "offline" | No network at all, or tolerating a poor one? | Depends — ask |

If the answer confirms the request is genuinely outside the boundary, **do not
refuse and do not improvise a workaround.** Record it as out of scope with the
reason and carry on with what remains. What happens to the excluded part is the
reviewer's decision at Gate 1, not yours.

## Output shape

The scope report carries:

- **In scope** — concrete capabilities, each traceable to something said
- **Out of scope** — and this section is never empty on a real requirement.
  Anything the boundary excludes, anything explicitly not wanted, anything
  discussed and deferred. An empty out-of-scope section means the boundary was
  never tested, and it is the section that prevents an argument at Gate 4 about
  what was agreed.
- **Success** — what the requester expects to change, in their own words.
  "People stop double-booking rooms" is worth more than a feature list: it is
  what a reviewer weighs the scope against, and the only statement that survives
  contact with a UAT session.
- **Open questions** — everything unresolved. A dependency whose interface you
  have not confirmed names the system rather than guessing at its behaviour. A
  checklist item the budget did not reach is an open question, not an omission
  to fill in quietly.

Anything touching personal, financial or health data belongs in the
non-functional requirements, so the compliance constraint travels with the
requirement instead of being rediscovered at design.

## Client vocabulary

<!-- PLACEHOLDER — completed with the client before first use.
     Their names for their systems, departments and roles, so the agent uses
     their words rather than inventing synonyms. A synonym introduced here
     survives into the BRD and breaks entity matching when the decomposition
     agent splits work across departments two phases later.

       - Systems:     <their ERP, HR system, ticketing tool>
       - Departments: <as the client names them>
       - Roles:       <the approver titles in their hierarchy>
-->

No client vocabulary is configured. Until it is, use the requester's own words
for their systems and departments, and never substitute a generic term for a
name they used.
"""


class IncompleteSkillFile(Exception):
    """A skill file missing a section the architecture requires.

    Checked rather than trusted, because the failure is silent: an agent given a
    file with no capability boundary does not error, it simply accepts anything
    — and the cost lands four gates later.
    """


@dataclass(frozen=True)
class SkillFile:
    """The file in force, and the commit that is its version.

    ``version`` is a commit SHA rather than a counter. Git already versions the
    file; a second counter would create two answers to "which version produced
    this", and the two would eventually differ.
    """

    content: str
    version: str
    edited_by: str

    @property
    def is_default(self) -> bool:
        """True when the shipped default is what the agent is running against.

        A true and citable state, not an error — a deployment that has never
        edited the boundary is running the one that shipped.
        """
        return self.content == DEFAULT


def validate(content: str) -> None:
    """Refuse a skill file that has lost a section the architecture requires."""
    flat = " ".join(content.lower().split())
    missing = [s for s in REQUIRED_SECTIONS if s not in flat]
    if missing:
        raise IncompleteSkillFile(
            "the intake skill file must carry: " + ", ".join(missing) + " — an agent given "
            "a file without a capability boundary does not error, it accepts anything"
        )


def shipped() -> SkillFile:
    """The default, for a deployment that has never edited one."""
    return SkillFile(content=DEFAULT, version="shipped", edited_by="")


def load_from_repo(root: str | Path, *, version: str = "shipped", edited_by: str = "") -> SkillFile:
    """The file as it lives in the governance repository.

    Version is the commit that contains it. The path is the architecture's,
    not a preference: a row in a table would need its own audit trail.
    """
    content = (Path(root) / PATH).read_text(encoding="utf-8")
    validate(content)
    return SkillFile(content=content, version=version, edited_by=edited_by)


def snapshot_path_for(requirement_id: str, stage: str) -> str:
    """Where the file is committed beside the artefact it governed.

    Beside, not referenced. A version number alone would send a reviewer to
    resolve it against a store that has since moved on; the snapshot puts the
    rules in the same commit as the document they produced.
    """
    return f"requirements/{requirement_id}/{stage}/intake.skill.md"


class BudgetExhausted(Exception):
    """The conversation has already asked its last permitted question."""


class BudgetTooEarly(Exception):
    """The report was produced before the minimum number of questions."""


@dataclass
class QuestionBudget:
    """The 4-to-10 bound, enforced here rather than by the prompt.

    Cost grows quadratically with transcript length. A model asked to stop at
    ten will, under pressure, ask an eleventh. The counter lives outside it.
    """

    minimum: int = 4
    maximum: int = 10
    asked: int = 0

    def record_question(self) -> None:
        if self.asked >= self.maximum:
            raise BudgetExhausted(
                f"question budget is {self.minimum}–{self.maximum}; "
                f"{self.asked} already asked"
            )
        self.asked += 1

    def may_close(self) -> bool:
        return self.asked >= self.minimum

    def must_close(self) -> bool:
        return self.asked >= self.maximum

    def require_closeable(self) -> None:
        if not self.may_close():
            raise BudgetTooEarly(
                f"a scope report before {self.minimum} questions is a guess; "
                f"{self.asked} asked so far"
            )


def prompt_section(skill: SkillFile) -> str:
    """The file as a prompt fragment — whole, and first.

    Returned entire rather than summarised, and placed ahead of anything that
    varies per turn. The boundary must not change between turns of the same
    conversation, and a stable leading prefix is the part a provider can cache.
    """
    return skill.content
