const OpenAI = require('openai');

// OpenRouter speaks the OpenAI chat-completions wire format, so the OpenAI
// SDK works unmodified against it — just point baseURL at OpenRouter and use
// an OpenRouter API key instead of an OpenAI one. One key covers every model
// below, whichever provider it comes from.
const client = new OpenAI({
  apiKey: process.env.OPENROUTER_API_KEY,
  baseURL: 'https://openrouter.ai/api/v1',
  defaultHeaders: {
    'HTTP-Referer': process.env.FRONTEND_ORIGIN || 'http://localhost:5173',
    'X-Title': 'Stark Digital AI Software Factory',
  },
});

// Three models for three different jobs. The conversational back-and-forth
// only ever needs plain natural language, so a small/cheap model is fine
// there. Turning the finished conversation into strict structured JSON is a
// much less forgiving task — small models are noticeably less reliable at
// emitting well-formed tool calls (observed: Llama 3.1 8B leaking a
// hand-written "finalize_requirement={...}" string into the chat instead of
// using the real tool-call format) — so that step runs on a stronger model.
// The post-approval detailed report/FSD is the highest-stakes document in
// the pipeline (architecture, data model, security design) and benefits
// most from the strongest reasoning, so it runs on Claude specifically.
const CHAT_MODEL = 'anthropic/claude-haiku-4.5';
const REPORT_MODEL = 'openai/gpt-4o-mini';
const DETAILED_REPORT_MODEL = 'anthropic/claude-sonnet-5';

const READY_SENTINEL = 'READY_TO_FINALIZE';

// The intake conversation is the only place the requirement actually gets
// shaped — the summary, the FSD, and the team work packages are all derived
// from this transcript and nothing else, so whatever the analyst fails to
// ask about here is a gap the build team discovers later. Two levers keep it
// from wrapping up shallow: this floor on question count, enforced in code
// below because a model that decides it has "enough" will otherwise emit the
// sentinel early, and the coverage checklist in the system prompt, which is
// what makes those questions substantive rather than generic form-filling.
const MIN_CLARIFYING_QUESTIONS = 8;

// The floor is a quality guard, not a trap — an explicit request to stop
// always wins over it, so nobody gets held in an interrogation they've asked
// to end.
const FINALIZE_INTENT = /\b(finali[sz]e|wrap (it )?up|that'?s (all|it)|i'?m done|we'?re done|submit it|go ahead|just proceed|enough (questions|detail|info)|no more questions|stop asking)\b/i;

const FINALIZE_TOOL = {
  type: 'function',
  function: {
    name: 'finalize_requirement',
    description: 'Structure the finished intake conversation into a requirement document ready for the approval pipeline.',
    parameters: {
      type: 'object',
      properties: {
        title: { type: 'string', description: 'Short title for this requirement, under 80 characters.' },
        summary: { type: 'string', description: '2-4 sentence plain-language summary of what is being built and why.' },
        inScope: { type: 'array', items: { type: 'string' }, description: 'Concrete capabilities included in this requirement.' },
        outOfScope: { type: 'array', items: { type: 'string' }, description: 'Things explicitly excluded, to prevent scope creep.' },
        functionalRequirements: { type: 'array', items: { type: 'string' } },
        nonFunctionalRequirements: {
          type: 'array',
          items: { type: 'string' },
          description: 'Performance, availability, compliance, and data-sensitivity constraints.',
        },
        recommendedModels: {
          type: 'array',
          description: 'Which AI models the finished PRODUCT should use, if any AI-powered features within it were discussed (e.g. an in-app support chatbot or search). Empty array if none apply. This is NOT about which model generates the code — that is preferredCodeGenModel below.',
          items: {
            type: 'object',
            properties: {
              purpose: { type: 'string', description: "What this model would be used for within the product, e.g. 'in-app support chat' or 'document search'." },
              model: { type: 'string', description: "Specific recommended model, e.g. 'Claude Opus 5' or 'Claude Haiku 4.5'." },
              rationale: { type: 'string' },
            },
            required: ['purpose', 'model', 'rationale'],
          },
        },
        preferredCodeGenModel: {
          type: 'string',
          description: "Which AI model the client wants Stark Digital's factory to use to GENERATE THE CODE for this build (not a feature of their product — the model doing the actual coding work). State the client's stated preference if they gave one, e.g. 'Claude Sonnet 5' or 'GPT-5'. If they had no preference, write 'No preference — let Stark Digital choose'.",
        },
        recommendedSecurityTools: {
          type: 'array',
          description: "Security tooling this build should run through the factory's security gate.",
          items: {
            type: 'object',
            properties: {
              category: { type: 'string', description: "e.g. 'Static analysis (SAST)', 'Secrets scanning', 'Dependency scanning'." },
              tool: { type: 'string', description: "Specific tool, e.g. 'Semgrep', 'gitleaks', 'Snyk'." },
              rationale: { type: 'string' },
            },
            required: ['category', 'tool', 'rationale'],
          },
        },
        openQuestions: { type: 'array', items: { type: 'string' }, description: 'Anything still unresolved that the approver should weigh in on.' },
      },
      required: [
        'title',
        'summary',
        'inScope',
        'outOfScope',
        'functionalRequirements',
        'nonFunctionalRequirements',
        'recommendedModels',
        'preferredCodeGenModel',
        'recommendedSecurityTools',
        'openQuestions',
      ],
    },
  },
};

const DETAILED_REPORT_TOOL = {
  type: 'function',
  function: {
    name: 'generate_detailed_report',
    description: 'Expand an already-approved requirement summary into a fuller BRD-style detailed report.',
    parameters: {
      type: 'object',
      properties: {
        conversationSummary: {
          type: 'string',
          description: 'A narrative recap of the actual intake conversation — what was discussed and asked, roughly in the order it came up. This goes first, before any technical detail, so a reader gets context before the rest of the report.',
        },
        objective: { type: 'string', description: 'The business objective this build serves, in a few sentences.' },
        architecture: {
          type: 'string',
          description: 'Technical architecture overview: components, integration points, how pieces talk to each other, and the end-to-end request/data flow through the system (e.g. client → API → database → response) described in prose.',
        },
        techStack: {
          type: 'array',
          description: 'The concrete technology choice for every layer of the build — no layer should be left vague.',
          items: {
            type: 'object',
            properties: {
              layer: { type: 'string', description: "e.g. 'Frontend', 'Backend/API', 'Database', 'Hosting/Infrastructure', 'Authentication', 'CI/CD'." },
              choice: { type: 'string', description: "Specific technology, e.g. 'React + TypeScript', 'Node.js/Express', 'PostgreSQL', 'AWS (ECS + RDS)'." },
              rationale: { type: 'string', description: 'Why this choice fits the requirement, in one sentence.' },
            },
            required: ['layer', 'choice', 'rationale'],
          },
        },
        userFlow: {
          type: 'array',
          items: { type: 'string' },
          description: 'The primary end-to-end user journey through the system, as ordered, numbered-in-order steps (e.g. "1. Visitor enters name and host on the tablet" — write the step text without a leading number, ordering is implied by array position). Cover the main path a typical user takes from start to finish.',
        },
        dataModel: {
          type: 'array',
          description: 'Key entities this system needs to store.',
          items: {
            type: 'object',
            properties: {
              entity: { type: 'string', description: "e.g. 'Ticket', 'Vendor', 'Appointment'." },
              fields: { type: 'array', items: { type: 'string' }, description: 'Key fields on this entity.' },
              description: { type: 'string' },
            },
            required: ['entity', 'fields', 'description'],
          },
        },
        pageBehavior: {
          type: 'array',
          description: 'Screens or pages the user interacts with, and what happens on each.',
          items: {
            type: 'object',
            properties: {
              page: { type: 'string', description: "e.g. 'Ticket inbox', 'Vendor submission form'." },
              description: { type: 'string' },
            },
            required: ['page', 'description'],
          },
        },
        securityDesign: {
          type: 'array',
          items: { type: 'string' },
          description: 'Security measures specific to this build — auth, data handling, access control.',
        },
        deploymentAndOperations: {
          type: 'array',
          items: { type: 'string' },
          description: "Production-readiness plan: environments (dev/staging/prod), CI/CD pipeline, monitoring/alerting, backup and disaster recovery, rollback strategy. Treat this as what's needed to actually run the system in production, not just build it.",
        },
        costEstimate: {
          type: 'object',
          description: 'A rough but concrete cost estimate — order-of-magnitude figures are fine, but every field must have a real number or range, never left blank.',
          properties: {
            developmentEstimate: { type: 'string', description: "Rough dev effort/cost, e.g. '3-4 weeks, 1 full-stack engineer' or a $ range." },
            infrastructureMonthlyEstimate: { type: 'string', description: "Rough ongoing monthly infra cost at the stated scale, e.g. '$50-150/month (hosting, database, backups)'." },
            notes: { type: 'string', description: 'Anything that could swing the estimate up or down — e.g. compliance work, third-party API costs.' },
          },
          required: ['developmentEstimate', 'infrastructureMonthlyEstimate', 'notes'],
        },
        timeline: {
          type: 'array',
          description: 'Build phases in order, each with a rough duration — should sum to a realistic total delivery timeline.',
          items: {
            type: 'object',
            properties: {
              phase: { type: 'string', description: "e.g. 'Setup & data model', 'Core build', 'Security review & testing', 'UAT & launch'." },
              duration: { type: 'string', description: "e.g. '1 week'." },
              description: { type: 'string', description: 'What happens in this phase.' },
            },
            required: ['phase', 'duration', 'description'],
          },
        },
        assumptions: { type: 'array', items: { type: 'string' }, description: 'Assumptions made while writing this report.' },
        openQuestions: { type: 'array', items: { type: 'string' }, description: 'Anything still unresolved, for the build team to confirm.' },
      },
      required: [
        'conversationSummary',
        'objective',
        'architecture',
        'techStack',
        'userFlow',
        'dataModel',
        'pageBehavior',
        'securityDesign',
        'deploymentAndOperations',
        'costEstimate',
        'timeline',
        'assumptions',
        'openQuestions',
      ],
    },
  },
};

function buildChatSystemPrompt(originatorLabel, questionsAsked, userWantsOut) {
  return `You are the requirement-intake analyst inside Stark Digital's AI Software Factory. You're talking with ${originatorLabel}.

Your job is to interrogate the idea until it is genuinely buildable — you do NOT produce the final document yourself, someone else handles that. Everything downstream is built from this conversation alone, so anything you fail to ask about becomes a gap the engineering team hits later.

HOW TO ASK
- One question at a time, never a wall of questions.
- Keep it tight: at most 2-5 sentences. One short sentence reflecting back what you understood is welcome, then the single question.
- Write plain conversational text. The chat bubble renders literally, so no markdown, no **bold**, no bullet points, no headings — asterisks show up as asterisks.
- Reply in whatever language the requester is using. If they write in Hinglish, Marathi, or a mix, match it rather than switching to formal English.
- Ask about THIS domain, not a generic intake form. Work out what is actually being built, then ask what a competent analyst in that domain would ask. "Who are the primary users and what are their roles and responsibilities within the organization?" is a wasted turn. "Are riders asking for live train timings, or booking and paying for tickets inside the chat?" is not.
- When an answer is vague, one word, or rests on an assumption you cannot verify, drill into it before moving on. Do not quietly accept "it should be fast" or "the usual security".
- Favour questions whose answer would actually change what gets built.

WHAT TO COVER — track these and do not finalize while any is still blank:
1. Primary users, and the situation they are in when they use this.
2. The core flow step by step: what the user does, what the system does back.
3. Net-new vs integration — which existing systems, APIs, or data sources this must talk to, and whether access to them already exists.
4. Data sensitivity: PII, payment, location, or other regulated data, and how long it is kept.
5. Scale and deadline: rough volume, and any date this is tied to.
6. Failure and edge cases on the core flow — what should happen when a dependency is down or returns nothing.
7. Whether any AI-powered features are wanted inside the product itself.
8. Security or compliance requirements the requester already has in mind.
9. Separately from any AI features in the product: which AI model they want Stark Digital's factory to use to GENERATE THE CODE for this build. "No preference" is a valid answer.

WHEN TO STOP
You have asked ${questionsAsked} question${questionsAsked === 1 ? '' : 's'} so far. Ask at least ${MIN_CLARIFYING_QUESTIONS}, and do not finalize while any checklist item above is unanswered.
${userWantsOut ? 'The requester has asked to wrap up. Honour that — finalize now even if items remain unanswered.\n' : ''}Only once that bar is met, reply with EXACTLY this and nothing else — no punctuation, no extra words: ${READY_SENTINEL}`;
}

function buildReportSystemPrompt(originatorLabel) {
  return `You are the requirement-structuring analyst inside Stark Digital's AI Software Factory. Below is a finished intake conversation between an analyst and ${originatorLabel}. Read the whole conversation and call finalize_requirement with the structured requirement document it describes.

When filling recommendedModels: only include entries if the conversation described AI-powered features within the product itself; leave it empty otherwise.
When filling preferredCodeGenModel: this is about which model generates the CODE, not a product feature — use the client's stated preference, or "No preference — let Stark Digital choose" if they didn't name one.
When filling recommendedSecurityTools: draw on static analysis (SAST), secrets scanning, and dependency/SCA scanning, naming concrete tools rather than just categories.
Call finalize_requirement exactly once, structuring the full conversation as best you can — do not ask further questions.`;
}

// history[0] is the hardcoded opener the route seeds the session with, not
// something the model generated, so it does not count toward the floor.
function countQuestionsAsked(history) {
  const assistantTurns = history.filter((h) => h.role === 'assistant').length;
  const seededOpener = history.length && history[0].role === 'assistant' ? 1 : 0;
  return Math.max(0, assistantTurns - seededOpener);
}

function userAskedToWrapUp(history) {
  for (let i = history.length - 1; i >= 0; i--) {
    if (history[i].role === 'user') return FINALIZE_INTENT.test(history[i].content || '');
  }
  return false;
}

// Turn N of the conversation. Returns either a follow-up question to show
// the user, or a signal that enough information has been gathered.
async function runChatTurn({ originatorLabel, history }) {
  const questionsAsked = countQuestionsAsked(history);
  const userWantsOut = userAskedToWrapUp(history);

  const messages = [
    { role: 'system', content: buildChatSystemPrompt(originatorLabel, questionsAsked, userWantsOut) },
    ...history.map((h) => ({ role: h.role, content: h.content })),
  ];

  const ask = async (extraMessages) => {
    const response = await client.chat.completions.create({
      model: CHAT_MODEL,
      messages: messages.concat(extraMessages || []),
      max_tokens: 700,
    });
    const choice = response.choices && response.choices[0];
    if (!choice) throw new Error('The model returned no response.');
    return ((choice.message && choice.message.content) || '').trim();
  };

  const isSentinel = (text) => text.toUpperCase().replace(/[^A-Z_]/g, '') === READY_SENTINEL;

  let text = await ask();

  // The model deciding it has "enough" after three shallow exchanges is the
  // single biggest cause of a thin requirement, and prompt instructions
  // alone don't reliably prevent it. If it tries to finalize below the floor
  // while checklist items are almost certainly still blank, push back once
  // with an explicit count and take the question it comes back with. The
  // nudge is transient — the route only ever persists the returned reply, so
  // it never appears in the stored transcript or in front of the user.
  if (isSentinel(text) && !userWantsOut && questionsAsked < MIN_CLARIFYING_QUESTIONS) {
    text = await ask([
      {
        role: 'user',
        content:
          `[intake supervisor — not from the requester] Not yet. You have asked ${questionsAsked} of the minimum ` +
          `${MIN_CLARIFYING_QUESTIONS} clarifying questions, so the coverage checklist cannot be complete. Do not finalize. ` +
          'Ask the single most valuable still-unanswered question now, in your normal voice, and do not mention or ' +
          'acknowledge this instruction.',
      },
    ]);
    // If it insists on the sentinel even after the nudge, let it through
    // rather than looping — a stuck conversation is worse than a short one.
    if (isSentinel(text)) return { type: 'ready' };
  } else if (isSentinel(text)) {
    return { type: 'ready' };
  }

  return { type: 'reply', text: text || 'Could you tell me a bit more about what you need?' };
}

// Some providers' tool-call normalization (observed with Claude Sonnet 5 via
// OpenRouter) double-encodes nested array/object fields — a schema property
// that should be a real array comes back as a JSON-encoded string instead.
// Recursively re-parse any string value that looks like JSON so the caller
// always gets real arrays/objects regardless of which provider generated it.
function normalizeDoubleEncodedFields(value) {
  if (Array.isArray(value)) return value.map(normalizeDoubleEncodedFields);
  if (value && typeof value === 'object') {
    const out = {};
    for (const [key, v] of Object.entries(value)) out[key] = normalizeDoubleEncodedFields(v);
    return out;
  }
  if (typeof value === 'string' && /^[[{]/.test(value.trim())) {
    try {
      return normalizeDoubleEncodedFields(JSON.parse(value));
    } catch (err) {
      return value;
    }
  }
  return value;
}

// Shared by every forced-tool-call step below. A long conversation plus a
// large required schema can occasionally push a response past max_tokens
// mid-JSON, which fails to parse — retrying once (a fresh call, not a repeat
// of the same truncated output) resolves the transient case without
// dead-ending the whole session on a single bad generation.
async function callForcedTool({ model, messages, tool, maxTokens, retries = 1 }) {
  const requiredFields = tool.function.parameters.required || [];
  let lastErr;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const response = await client.chat.completions.create({
        model,
        messages,
        tools: [tool],
        tool_choice: { type: 'function', function: { name: tool.function.name } },
        max_tokens: maxTokens,
      });

      const choice = response.choices && response.choices[0];
      if (!choice) throw new Error('The model returned no response.');

      const toolCall = ((choice.message && choice.message.tool_calls) || [])[0];
      if (!toolCall) throw new Error('The model did not return a structured response.');

      const parsed = normalizeDoubleEncodedFields(JSON.parse(toolCall.function.arguments));

      // A large schema occasionally drops a required field (or leaves one
      // truncated) even when the JSON itself parses fine — catch that here
      // rather than shipping an incomplete document, and retry.
      const missing = requiredFields.filter((f) => !(f in parsed));
      if (missing.length) throw new Error(`the model's response was missing required fields: ${missing.join(', ')}`);

      return parsed;
    } catch (err) {
      lastErr = err;
    }
  }
  throw new Error('The model returned a malformed response after retrying: ' + lastErr.message);
}

// One-shot: reads the full finished conversation and structures it. Forces
// the tool call so the response can only ever be the structured document,
// never plain text.
async function runFinalize({ originatorLabel, history }) {
  const messages = [
    { role: 'system', content: buildReportSystemPrompt(originatorLabel) },
    ...history.map((h) => ({ role: h.role, content: h.content })),
  ];
  // A deeper intake conversation yields longer inScope/functional lists, so
  // budget above the schema's nominal size to avoid truncating mid-JSON.
  return callForcedTool({ model: REPORT_MODEL, messages, tool: FINALIZE_TOOL, maxTokens: 4000 });
}

function buildDetailedReportSystemPrompt(originatorLabel) {
  return `You are the BRD/FSD analyst inside Stark Digital's AI Software Factory. An MD/CEO-level approver has just cleared the summary below for ${originatorLabel}'s requirement — your job is to expand it into a genuinely production-ready FSD by calling generate_detailed_report. This document should be detailed enough that an engineering team could start building directly from it — no section should be vague, generic, or read like a placeholder.

First write conversationSummary: a narrative recap of the actual intake conversation, in the order topics came up, so a reader gets context before any technical detail.
Then base the rest of the report on the approved summary and the original intake conversation, both provided below. Be concrete everywhere: name real entities, real pages, real security measures, real technology choices — do not restate the summary's bullet points verbatim, add the next level of detail underneath them.

Specifically:
- architecture must describe the actual end-to-end request/data flow (e.g. "client submits → API validates → writes to DB → triggers notification"), not just list components.
- techStack must name a concrete, specific technology for every layer (frontend, backend, database, hosting, auth, CI/CD) — never "a suitable framework" or similarly vague.
- userFlow must be the real ordered steps a user actually takes, specific to this requirement.
- costEstimate and timeline must have real numbers/ranges/durations — order-of-magnitude estimates are fine, but never leave a field blank or say "TBD."
- deploymentAndOperations must cover how this actually runs in production: environments, CI/CD, monitoring, backups, rollback — not just how it's built.
Call generate_detailed_report exactly once.`;
}

// Runs once, right after an MD/CEO-level approval gate clears. Takes the
// already-approved summary plus the original conversation and expands it
// into a fuller BRD/FSD-style report — conversation recap, architecture,
// data model, page behavior, security design. This is the highest-stakes
// document in the pipeline, so it runs on Claude rather than the
// cheaper/faster model used for finalize and team-split.
async function runDetailedReport({ originatorLabel, summary, history }) {
  const messages = [
    { role: 'system', content: buildDetailedReportSystemPrompt(originatorLabel) },
    { role: 'user', content: 'Approved requirement summary:\n' + JSON.stringify(summary, null, 2) },
    ...history.map((h) => ({ role: h.role, content: h.content })),
    // history's last entry is the intake assistant's own closing line —
    // Claude rejects a forced tool call when the conversation ends on an
    // assistant turn ("assistant message prefill"), so close with a user
    // instruction regardless of what history ends on.
    { role: 'user', content: 'Generate the detailed report now by calling generate_detailed_report.' },
  ];
  // Claude Sonnet 5 uses extended thinking by default, which consumes part
  // of max_tokens before it ever emits the tool call — budget well above
  // the actual JSON's real size to leave room for that.
  // This schema is large enough (13 required fields, several nested) that a
  // single retry isn't always enough — budget an extra attempt.
  return callForcedTool({ model: DETAILED_REPORT_MODEL, messages, tool: DETAILED_REPORT_TOOL, maxTokens: 12000, retries: 2 });
}

// Same shape as DETAILED_REPORT_TOOL, plus a changeSummary field so the chat
// UI has something short to show back as the "assistant" reply — built by
// extending the existing schema rather than duplicating it.
const FSD_CHAT_EDIT_TOOL = {
  type: 'function',
  function: {
    name: 'apply_fsd_edit',
    description: "Apply the user's requested change to the detailed report and return the FULL updated report, plus a short confirmation of what changed.",
    parameters: {
      type: 'object',
      properties: Object.assign(
        {
          changeSummary: {
            type: 'string',
            description: "One or two sentences confirming what was changed, to show back to the user in the chat — e.g. 'Updated the security design to require two-factor authentication for admin accounts.'",
          },
        },
        DETAILED_REPORT_TOOL.function.parameters.properties
      ),
      required: ['changeSummary', ...DETAILED_REPORT_TOOL.function.parameters.required],
    },
  },
};

function buildFsdChatSystemPrompt(originatorLabel, actorRole) {
  return `You are the FSD editing assistant inside Stark Digital's AI Software Factory, helping ${actorRole} revise the detailed report for ${originatorLabel}'s requirement.

The next message describes a change they want. Apply ONLY that change (and anything it directly implies) to the report provided below — leave everything else as close to the original as possible, do not rewrite sections that weren't asked about. Then call apply_fsd_edit with the FULL updated report plus a short changeSummary confirming what you changed.
Call apply_fsd_edit exactly once.`;
}

// One turn of the FSD edit chat. Takes the current report plus the running
// edit conversation (which already ends on the newest user instruction) and
// returns the updated report plus a short confirmation to show in the chat.
async function runFsdChatEdit({ originatorLabel, actorRole, detailedReport, history }) {
  const messages = [
    { role: 'system', content: buildFsdChatSystemPrompt(originatorLabel, actorRole) },
    { role: 'user', content: 'Current detailed report:\n' + JSON.stringify(detailedReport, null, 2) },
    ...history.map((h) => ({ role: h.role, content: h.content })),
  ];
  return callForcedTool({ model: DETAILED_REPORT_MODEL, messages, tool: FSD_CHAT_EDIT_TOOL, maxTokens: 12000 });
}

// Fixed department taxonomy — every team split must use exactly these
// names, so team leads always know which queue to check regardless of what
// the requirement was.
const TEAM_DEPARTMENTS = ['QA', 'AI', 'Development', 'DevOps', 'Sales & Marketing'];

const TEAM_SPLIT_TOOL = {
  type: 'function',
  function: {
    name: 'split_team_reports',
    description: 'Split an approved detailed report into smaller, department-specific work packages for the team leads to pick up.',
    parameters: {
      type: 'object',
      properties: {
        teamReports: {
          type: 'array',
          description: `One entry per department actually needed, using ONLY these exact names: ${TEAM_DEPARTMENTS.join(', ')}. Do not invent a department or rename one. Skip any department with nothing to do.`,
          items: {
            type: 'object',
            properties: {
              team: { type: 'string', enum: TEAM_DEPARTMENTS, description: 'Must be exactly one of the fixed department names.' },
              objective: { type: 'string', description: 'What this department needs to deliver, in a sentence or two.' },
              tasks: { type: 'array', items: { type: 'string' }, description: 'Concrete, actionable build tasks for this department.' },
              relevantDataEntities: { type: 'array', items: { type: 'string' }, description: 'Entities from the data model this department touches, if any.' },
            },
            required: ['team', 'objective', 'tasks'],
          },
        },
      },
      required: ['teamReports'],
    },
  },
};

function buildTeamSplitSystemPrompt(originatorLabel) {
  return `You are the delivery lead inside Stark Digital's AI Software Factory. The detailed report below, for ${originatorLabel}'s requirement, has just cleared final approval — your job is to split it into smaller, department-specific work packages by calling split_team_reports.

Stark Digital has exactly five departments: ${TEAM_DEPARTMENTS.join(', ')}. Read the architecture, data model, page behavior, and security design in the report, then assign work to whichever of these five actually have something to do — skip any with nothing to do, but never invent a department outside this list. Each department's tasks should be concrete enough for its team lead to hand straight to their team.
Call split_team_reports exactly once.`;
}

// Runs once, right after the approval chain is fully cleared. Takes the
// already-generated detailed report and splits it into per-discipline work
// packages for team leads — the last step of Phase 1's demo scope.
async function runTeamSplit({ originatorLabel, detailedReport }) {
  const messages = [
    { role: 'system', content: buildTeamSplitSystemPrompt(originatorLabel) },
    { role: 'user', content: 'Approved detailed report:\n' + JSON.stringify(detailedReport, null, 2) },
  ];
  return callForcedTool({ model: REPORT_MODEL, messages, tool: TEAM_SPLIT_TOOL, maxTokens: 2500 });
}

module.exports = { runChatTurn, runFinalize, runDetailedReport, runTeamSplit, runFsdChatEdit };
