const OpenAI = require('openai');
const Anthropic = require('@anthropic-ai/sdk');
const babel = require('@babel/core');

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

// Code generation specifically goes straight to Anthropic's own API instead
// of through OpenRouter — every other stage (chat intake, report/FSD
// writing, self-testing's code review) stays on the client/OpenRouter
// above and is untouched by this. See runCodeGen and the fixer functions
// tied to it (runSecurityAutoFix, runBuildFix, runProjectDemoSynthesis,
// checkFrontendCoverage, patchFrontendCoverage) below.
const anthropicClient = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

// The rest of this file builds prompts as an OpenAI-style messages array
// ([{role:'system',...}, {role:'user',...}, ...]) — Anthropic's Messages
// API takes the system prompt as its own top-level `system` string instead
// of a message with role "system", so every direct-to-Claude call site
// splits it out with this rather than rebuilding prompts in a different shape.
function splitSystemMessage(messages) {
  const systemMsg = messages.find((m) => m.role === 'system');
  const rest = messages.filter((m) => m.role !== 'system').map((m) => ({ role: m.role, content: m.content }));
  return { system: systemMsg ? systemMsg.content : undefined, messages: rest };
}

// Three models for three different jobs. The conversational back-and-forth
// only ever needs plain natural language, so a small/cheap model is fine
// there. Turning the finished conversation into strict structured JSON is a
// much less forgiving task — small models are noticeably less reliable at
// emitting well-formed tool calls (observed: Llama 3.1 8B leaking a
// hand-written "finalize_requirement={...}" string into the chat instead of
// using the real tool-call format) — so that step runs on a stronger model.
// The post-approval detailed report/FSD uses DeepSeek for its stronger
// technical detail. The :nitro route tells OpenRouter to prefer the highest-
// throughput provider instead of its default price-weighted routing.
const CHAT_MODEL = 'anthropic/claude-haiku-4.5';
const REPORT_MODEL = 'openai/gpt-4o-mini';
const DETAILED_REPORT_MODEL = 'deepseek/deepseek-v3.2:nitro';

const READY_SENTINEL = 'READY_TO_FINALIZE';

// The intake conversation is the only place the requirement actually gets
// shaped — the summary, the FSD, and the team work packages are all derived
// from this transcript and nothing else, so whatever the analyst fails to
// ask about here is a gap the build team discovers later. Two levers keep it
// from wrapping up shallow: this floor on question count, enforced in code
// below because a model that decides it has "enough" will otherwise emit the
// sentinel early, and the coverage checklist in the system prompt, which is
// what makes those questions substantive rather than generic form-filling.
// How long a guided intake may run, and how its length is measured — see
// intakeBudget.js. Kept out of this file so the bounds can be unit tested
// without loading the API clients or @babel/core.
const {
  MIN_CLARIFYING_QUESTIONS,
  MAX_CLARIFYING_QUESTIONS,
  countQuestionsAsked,
  userAskedToWrapUp,
} = require('./intakeBudget');

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
              model: { type: 'string', description: "Name ONE specific real model or vendor for this capability — even for a specialized case (recommendation engines, visual/image search, OCR) with no single obvious industry standard. If the conversation didn't state a preference, choose the best-fit REAL option yourself (e.g. 'Amazon Personalize' or 'Google Cloud Vision API') rather than listing several alternatives — a single named pick is what lets a real cost be attached to it later; a range of options can't be costed. Never state a plausible-sounding product name you are not confident actually exists — if you're unsure whether a niche vendor is real, default to a major, verifiably real cloud provider's equivalent service instead of an invented boutique one." },
              rationale: { type: 'string' },
            },
            required: ['purpose', 'model', 'rationale'],
          },
        },
        preferredCodeGenModel: {
          type: 'string',
          description: "Which AI model the client wants Stark Digital's factory to use to GENERATE THE CODE for this build (not a feature of their product — the model doing the actual coding work). State the client's stated preference if they gave one, e.g. 'Claude Sonnet 5' or 'GPT-5'. If they had no preference, write 'No preference — let Stark Digital choose'.",
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
        'openQuestions',
      ],
    },
  },
};

// Shared between the main FSD's techStack and each department's own
// techStack in the team split — same anti-hallucination rule applies
// either way.
const TECH_STACK_CHOICE_DESCRIPTION = "Name ONE specific real technology or vendor for this layer, e.g. 'React + TypeScript', 'PostgreSQL' — including for a specialized AI/ML capability (recommendation engines, visual/image search, OCR) where several real vendors compete. If the client didn't state a preference, choose the single best-fit REAL, well-established option yourself (e.g. 'Amazon Rekognition' or 'Google Cloud Vision API') rather than listing multiple alternatives. A plausible-sounding but non-existent product name is a serious error — if you are not confident a named product actually exists, default to a major, verifiably real cloud vendor's equivalent service instead of a specific boutique product you're unsure about.";

const DETAILED_REPORT_TOOL = {
  type: 'function',
  function: {
    name: 'generate_detailed_report',
    description: 'Expand an already-approved requirement summary into a fuller BRD-style detailed report.',
    parameters: {
      type: 'object',
      properties: {
        objective: { type: 'string', description: 'The business objective this build serves, in a few sentences.' },
        architecture: {
          type: 'string',
          description: 'Technical architecture overview: components, integration points, how pieces talk to each other, and the end-to-end request/data flow through the system (e.g. client → API → database → response) described in prose.',
        },
        architectureDiagram: {
          type: 'string',
          description: "A Mermaid diagram (valid syntax, starting with 'flowchart TD' or 'graph LR') showing this build's actual architecture — every component named in techStack as a node, with labelled arrows for the real request/data flow between them (client, API, database, third-party services, etc.). Do not include the ```mermaid code fence — just the diagram body, starting with the diagram-type declaration.",
        },
        techStack: {
          type: 'array',
          description: 'The concrete technology choice for every layer of the build — no layer should be left vague.',
          items: {
            type: 'object',
            properties: {
              layer: { type: 'string', description: "e.g. 'Frontend', 'Backend/API', 'Database', 'Hosting/Infrastructure', 'Authentication', 'CI/CD'." },
              choice: { type: 'string', description: TECH_STACK_CHOICE_DESCRIPTION },
              rationale: {
                type: 'string',
                description:
                  'Why this choice fits THIS requirement specifically, then the leading alternative you considered and why you rejected it, then the main limitation or failure mode of the option you picked in this particular operating environment. ' +
                  'Three short sentences, not one. A rationale that only praises the choice is incomplete — every real engineering decision has a trade-off, and naming it here is what makes the document reviewable.',
              },
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
        dbSchemaDiagram: {
          type: 'string',
          description: "A Mermaid ER diagram (valid syntax, starting with 'erDiagram') showing the entities from dataModel and the relationships between them, same style as a database schema diagram. Do not include the ```mermaid code fence — just the diagram body.",
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
        'objective',
        'architecture',
        'architectureDiagram',
        'techStack',
        'userFlow',
        'dataModel',
        'dbSchemaDiagram',
        'pageBehavior',
        'securityDesign',
        'deploymentAndOperations',
        'timeline',
        'assumptions',
        'openQuestions',
      ],
    },
  },
};

function buildChatSystemPrompt(originatorLabel, questionsAsked, userWantsOut) {
  return `You are the requirement-intake analyst inside Stark Digital's AI Software Factory. You're talking with ${originatorLabel}.

Your job is to get the BROAD STROKES of the idea down — not every detail. There is a review/edit step right after this conversation where the requester can correct or add anything, so you are not the last line of defense on precision. Stay at product altitude; do not chase niche edge cases, exact field names, or hyper-specific implementation details — that level of detail belongs in the edit step or later design work, not this chat.

HOW TO ASK
- One question at a time, never a wall of questions.
- Keep it tight: at most 2-5 sentences. One short sentence reflecting back what you understood is welcome, then the single question.
- Write plain conversational text. The chat bubble renders literally, so no markdown, no **bold**, no bullet points, no headings — asterisks show up as asterisks.
- Reply in whatever language the requester is using. If they write in Hinglish, Marathi, or a mix, match it rather than switching to formal English.
- Ask about THIS domain, not a generic intake form, but keep it broad — "what should happen when someone searches for a product" is the right altitude; "should search match partial SKU codes or only exact ones" is too deep for this conversation.
- If an answer is vague or one word, ask ONE follow-up to sharpen it, then move on regardless of whether it's fully resolved — never circle back to the same point a second time. Good enough beats exhaustive.
- Favour questions whose answer would actually change what gets built. One question can cover more than one checklist item at once — don't ask them one-by-one just because they're listed separately.
- Never say "last one", "one more thing", "before we lock this in", "just to wrap up", or any other framing about how many questions remain — you cannot reliably judge that in the moment, and getting it wrong reads as dishonest. Just ask each question plainly, with no countdown language at all.

WHAT TO COVER — track these and do not finalize while any is still blank:
1. Primary users, and the core flow: what they do, what the system does back.
2. Net-new vs integration — which existing systems, APIs, or data sources this must talk to.
3. Data sensitivity and scale — regulated data (PII, payment, etc.), if any, and rough volume.
4. Whether any AI-powered features are wanted inside the product itself.
5. Separately from any AI features in the product: which AI model they want Stark Digital's factory to use to GENERATE THE CODE for this build. "No preference" is a valid answer.

WHEN TO STOP
You have a hard budget of ${MAX_CLARIFYING_QUESTIONS} questions for this entire conversation. You have asked ${questionsAsked}, so ${Math.max(0, MAX_CLARIFYING_QUESTIONS - questionsAsked)} remain. When the budget runs out the conversation ends automatically and the requirement is written from whatever you have gathered by then — so an item you never got to is simply missing from the finished document.

Plan against that budget from your very first question. Five checklist items across ${MAX_CLARIFYING_QUESTIONS} questions means roughly two questions per item, which is enough to cover scope properly but leaves no room to waste: do not spend a question on pleasantries, on restating something already answered, or on sharpening a detail that already has a broad-strokes answer. If at any point you have more unanswered items left than questions remaining, stop going one-by-one and cover several items in a single question — a slightly broad answer on every item beats a precise answer on half of them and silence on the rest.

Ask at least ${MIN_CLARIFYING_QUESTIONS}. Do not finalize while any checklist item above is still blank — but the moment all five have a broad-strokes answer, finalize immediately even if budget remains. Leftover budget is not something to spend; asking further questions once the checklist is covered adds cost and fatigue without improving the requirement.

Track the budget silently. This is for your own planning only — never mention it, never count down, and never signal how many questions are left (see the rule above about countdown language).
${userWantsOut ? 'The requester has asked to wrap up. Honour that — finalize now even if items remain unanswered.\n' : ''}Only once that bar is met, reply with EXACTLY this and nothing else — no punctuation, no extra words: ${READY_SENTINEL}`;
}

function buildReportSystemPrompt(originatorLabel) {
  return `You are the requirement-structuring analyst inside Stark Digital's AI Software Factory. Below is a finished intake conversation between an analyst and ${originatorLabel}. Read the whole conversation and call finalize_requirement with the structured requirement document it describes.

When filling recommendedModels: only include entries if the conversation described AI-powered features within the product itself; leave it empty otherwise.
When filling preferredCodeGenModel: this is about which model generates the CODE, not a product feature — use the client's stated preference, or "No preference — let Stark Digital choose" if they didn't name one.
Call finalize_requirement exactly once, structuring the full conversation as best you can — do not ask further questions.`;
}

// Turn N of the conversation. Returns either a follow-up question to show
// the user, or a signal that enough information has been gathered.
async function runChatTurn({ originatorLabel, history }) {
  const questionsAsked = countQuestionsAsked(history);
  const userWantsOut = userAskedToWrapUp(history);

  // The ceiling is enforced here, not left to the prompt. Every soft
  // instruction in this pipeline has eventually been ignored by some model
  // on some input, and this one has a real bill attached: a conversation
  // that overruns re-sends its whole transcript every turn. Returning before
  // building any request also means the turn that would have blown the
  // budget costs nothing at all, rather than paying for a question we then
  // discard.
  if (questionsAsked >= MAX_CLARIFYING_QUESTIONS) return { type: 'ready' };

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

  // Requiring the WHOLE reply to equal the sentinel exactly is brittle —
  // reproduced live: after a long, derailed conversation, the model tried to
  // signal ready but wrapped the token in a full sentence ("...with a budget
  // range for your project. READY_TO_FINALIZE") instead of replying with
  // only the token as instructed. The strict equality check missed that
  // entirely, so the route just displayed the leaked sentinel as normal chat
  // text and the conversation never transitioned — it looped in confused
  // pleasantries for 30+ turns until the requester explicitly typed
  // "finalize" themselves. A whole-word match anywhere in the reply catches
  // this: the token is distinctive enough (all-caps, underscored) that it
  // essentially never appears by accident, so leniency here has no
  // meaningful false-positive risk.
  const isSentinel = (text) => new RegExp('\\b' + READY_SENTINEL + '\\b', 'i').test(text);

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
function normalizeDoubleEncodedFields(value, key) {
  if (Array.isArray(value)) return value.map((v) => normalizeDoubleEncodedFields(v));
  if (value && typeof value === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(value)) out[k] = normalizeDoubleEncodedFields(v, k);
    return out;
  }
  // "content" is always raw file text across every tool schema in this
  // file — never parse it, even when the text itself happens to look like
  // JSON (a package.json's content, say). Confirmed by a real bug: fixing
  // package.json came back with `content` silently replaced by a parsed
  // object instead of the literal string, which then crashed the pipeline
  // runner's fs.writeFile with "data argument must be a string."
  if (key === 'content') return value;
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
// Forced tool-calling doesn't guarantee schema compliance (no `strict`
// mode in use here) — a large input or a tight token budget can make the
// model quietly drop a required field on one array item while the
// top-level JSON still parses fine. Checks one level deep: for every
// top-level property that's an array of objects with its own `required`
// list, every item must have each of those fields present.
function findMissingFields(parsed, params) {
  const missing = (params.required || []).filter((f) => !(f in parsed));
  Object.entries(params.properties || {}).forEach(([propName, propSchema]) => {
    if (propSchema.type !== 'array' || !propSchema.items || !propSchema.items.required) return;
    const arr = parsed[propName];
    if (!Array.isArray(arr)) return;
    arr.forEach((item, i) => {
      propSchema.items.required.forEach((field) => {
        if (!item || typeof item !== 'object' || !(field in item)) missing.push(`${propName}[${i}].${field}`);
      });
    });
  });
  return missing;
}

async function callForcedTool({ model, messages, tool, maxTokens, retries = 1, timeoutMs = 90000, reasoning }) {
  const params = tool.function.parameters;
  let lastErr;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const response = await client.chat.completions.create(
        {
          model,
          messages,
          tools: [tool],
          tool_choice: { type: 'function', function: { name: tool.function.name } },
          max_tokens: maxTokens,
          ...(reasoning ? { reasoning } : {}),
        },
        { timeout: timeoutMs, maxRetries: 0 }
      );

      const choice = response.choices && response.choices[0];
      if (!choice) throw new Error('The model returned no response.');

      const toolCall = ((choice.message && choice.message.tool_calls) || [])[0];
      if (!toolCall) throw new Error('The model did not return a structured response.');

      const parsed = normalizeDoubleEncodedFields(JSON.parse(toolCall.function.arguments));

      // A large schema occasionally drops a required field (or leaves one
      // truncated) even when the JSON itself parses fine — catch that here
      // rather than shipping an incomplete document, and retry.
      const missing = findMissingFields(parsed, params);
      if (missing.length) throw new Error(`the model's response was missing required fields: ${missing.join(', ')}`);

      return parsed;
    } catch (err) {
      lastErr = err;
    }
  }
  throw new Error('The model returned a malformed response after retrying: ' + lastErr.message);
}

// Same shape and purpose as callForcedTool above, against Anthropic's
// Messages API instead — used only by the code-gen-tied fixer functions
// (runSecurityAutoFix, runBuildFix, runProjectDemoSynthesis,
// checkFrontendCoverage, patchFrontendCoverage). Anthropic's tool_use
// content block already gives `input` as a parsed object, not a JSON
// string to re-parse — the OpenAI path needs JSON.parse, this doesn't.
async function callForcedToolClaude({ model, messages, tool, maxTokens, retries = 1, timeoutMs = 90000 }) {
  const params = tool.function.parameters;
  const anthropicTool = { name: tool.function.name, description: tool.function.description, input_schema: params };
  const { system, messages: claudeMessages } = splitSystemMessage(messages);

  let lastErr;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const response = await anthropicClient.messages.create(
        {
          model,
          max_tokens: maxTokens,
          system,
          messages: claudeMessages,
          tools: [anthropicTool],
          tool_choice: { type: 'tool', name: tool.function.name },
        },
        { timeout: timeoutMs, maxRetries: 0 }
      );

      const toolUse = (response.content || []).find((c) => c.type === 'tool_use');
      if (!toolUse) throw new Error('Claude did not return a structured response.');

      const parsed = normalizeDoubleEncodedFields(toolUse.input);
      const missing = findMissingFields(parsed, params);
      if (missing.length) throw new Error(`Claude's response was missing required fields: ${missing.join(', ')}`);

      return parsed;
    } catch (err) {
      lastErr = err;
    }
  }
  throw new Error('Claude returned a malformed response after retrying: ' + lastErr.message);
}

// Dispatches to whichever provider the given code-gen model actually
// belongs to. The code-gen-tied fixers (security auto-fix, build fix,
// project demo synthesis, frontend coverage check/patch) run against
// whatever model a department's job was originally generated with — a job
// generated on an OpenRouter model (e.g. Gemini 2.5 Flash) must not have its
// "fix with AI" step hardcoded to Claude, or it breaks for anyone who hasn't
// configured ANTHROPIC_API_KEY even though that job never needed it.
async function callForcedToolAny({ model, messages, tool, maxTokens, retries, timeoutMs }) {
  const chosenModel = CODE_GEN_MODELS[model] ? model : DEFAULT_CODE_GEN_MODEL;
  if (ANTHROPIC_DIRECT_MODELS.has(chosenModel)) {
    return callForcedToolClaude({ model: chosenModel, messages, tool, maxTokens, retries, timeoutMs });
  }
  return callForcedTool({ model: chosenModel, messages, tool, maxTokens, retries, timeoutMs });
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

Start the report with objective, then base the remaining sections on the approved summary and the original intake conversation. Be concrete everywhere: name real entities, real pages, real security measures, and real technology choices — do not restate the summary's bullet points verbatim; add the next level of detail underneath them.

Specifically:
- architecture must describe the actual end-to-end request/data flow (e.g. "client submits → API validates → writes to DB → triggers notification"), not just list components.
- architectureDiagram must be a real, valid Mermaid flowchart (flowchart TD or graph LR) with a node for every component named in techStack and labelled arrows for the actual data flow between them — not a generic three-box sketch.
- techStack must name a concrete, specific technology for every layer (frontend, backend, database, hosting, auth, CI/CD) — never "a suitable framework" or similarly vague. For specialized AI/ML capabilities, choose one real, established vendor rather than listing alternatives. If unsure about a niche product, default to a major cloud provider's real equivalent.
- userFlow must be the real ordered steps a user actually takes, specific to this requirement.
- dbSchemaDiagram must be a real, valid Mermaid erDiagram covering every entity in dataModel and the relationships between them (use ||--o{, ||--||, etc. as appropriate) — mirror the entities/fields already described in dataModel rather than inventing new ones.
- timeline must have real durations that sum to a realistic total delivery timeline.
- deploymentAndOperations must cover how this actually runs in production: environments, CI/CD, monitoring, backups, rollback — not just how it's built.

DESIGN RIGOUR — being specific is not the same as being correct, and a confidently-named wrong component is worse than a vague one. Before committing to any technology, work through these:
- Fitness for THIS environment, not generic suitability. Ask how the component behaves under this build's actual physical conditions, scale, duty cycle, and real user behaviour. A part that is the obvious choice in one setting is often the wrong choice one setting over, and the difference is usually a property of the environment the requirement already told you about.
- Name the standard. If an established industry standard, protocol, or published specification already governs this problem domain, either build on it or state explicitly why you are not. Reaching for a general-purpose or hobbyist-tier component where a mature domain standard exists is a serious design error, not a cost saving.
- Deliver what was actually promised. Re-read the approved summary and check each capability it promises is genuinely delivered by this design — not a weaker cousin of it. If the design can only deliver a reduced version, do not quietly narrow the scope: say so plainly in openQuestions.
- No fictional precision. Every field in dataModel must be something the chosen hardware, service, or data source can actually produce. Inventing a field the design has no way to populate makes the whole document untrustworthy.
- Failure and safety. State what happens when the system fails or loses power, and what the safe state is. Where the build touches physical systems, public spaces, money, or regulated data, name the specific safety or compliance constraint that applies and how the design honours it.
Call generate_detailed_report exactly once.`;
}

// The finalized `summary` already distills the conversation's substance, so
// the raw transcript is supporting context, not the primary source — an
// unusually long guided intake (a user who kept chatting past the normal
// ~10-16 message range) shouldn't blow up prompt size and generation time
// proportionally. Keeps the opening messages (they set the original framing)
// and the most recent ones (closest to the final, refined answers); drops
// the uninformative middle rather than trimming from one end.
const MAX_HISTORY_FOR_DETAILED_REPORT = 30;
function capHistory(history, max) {
  if (history.length <= max) return history;
  const keepStart = Math.floor(max * 0.2);
  const keepEnd = max - keepStart;
  return history.slice(0, keepStart).concat(history.slice(history.length - keepEnd));
}

// Runs once, right after an MD/CEO-level approval gate clears. Takes the
// already-approved summary plus the original conversation and expands it
// into a fuller BRD/FSD-style report — conversation recap, architecture,
// data model, page behavior, security design. This is the highest-stakes
// document in the pipeline, so it runs on Claude rather than the
// cheaper/faster model used for finalize and team-split.
async function runDetailedReport({ originatorLabel, summary, history }) {
  const boundedHistory = capHistory(history, MAX_HISTORY_FOR_DETAILED_REPORT);
  const messages = [
    { role: 'system', content: buildDetailedReportSystemPrompt(originatorLabel) },
    { role: 'user', content: 'Approved requirement summary:\n' + JSON.stringify(summary, null, 2) },
    ...boundedHistory.map((h) => ({ role: h.role, content: h.content })),
    // history's last entry is the intake assistant's own closing line —
    // Claude rejects a forced tool call when the conversation ends on an
    // assistant turn ("assistant message prefill"), so close with a user
    // instruction regardless of what history ends on.
    { role: 'user', content: 'Generate the detailed report now by calling generate_detailed_report.' },
  ];
  // Costing and conversation-recap fields were removed, so the current
  // schema fits comfortably below this budget. Disable extended reasoning
  // because the full structured FSD itself is already a large output, and
  // keep one bounded attempt instead of multiplying latency with retries.
  let report = await callForcedTool({
    model: DETAILED_REPORT_MODEL,
    messages,
    tool: DETAILED_REPORT_TOOL,
    maxTokens: 10000,
    retries: 0,
    timeoutMs: 90000,
    reasoning: { enabled: false },
  });

  // Prompt instructions alone don't reliably produce a domain-appropriate
  // design — the same lesson as scanAndAutoFix and checkFrontendCoverage
  // above. A single generation pass is under pressure to commit to
  // something specific for every layer, which reliably yields confidently
  // named components that are wrong for the operating environment (observed
  // live: PIR motion sensors specified for a banquet hall, where guests sit
  // still through dinner and PIR reports the room empty). Reviewing the
  // finished document in a FRESH call is what catches that: the critic sees
  // only the artifact, with none of the reasoning that produced it, so it
  // reads the design the way an outside engineer would.
  const MAX_CRITIQUE_ROUNDS = 2;
  let previousFindingCount = Infinity;
  for (let round = 1; round <= MAX_CRITIQUE_ROUNDS; round++) {
    let critique;
    try {
      critique = await runDesignCritique({ summary, report });
    } catch (err) {
      break; // a failed review must never cost us the report we already have
    }
    const actionable = (critique.findings || []).filter((f) => f.severity === 'high' || f.severity === 'medium');
    if (!actionable.length) break;
    // Plateau guard, same shape scanAndAutoFix uses, so a critic that keeps
    // restating the same objection can't loop forever.
    if (actionable.length >= previousFindingCount) {
      report = withUnresolvedFindings(report, actionable);
      break;
    }
    previousFindingCount = actionable.length;
    try {
      const patched = await patchDetailedReport({ summary, report, findings: actionable });
      // callForcedTool already rejects a response missing required fields,
      // so anything returned here is structurally complete.
      if (patched && patched.objective) report = patched;
    } catch (err) {
      report = withUnresolvedFindings(report, actionable);
      break;
    }
  }
  return report;
}

// A failed auto-fix must degrade to "flagged for a human" rather than
// "silently dropped". The rewrite step returns the whole document at once,
// and a large structured payload occasionally comes back with malformed
// JSON escaping (observed live, on a report carrying two escape-heavy
// Mermaid diagrams) — but the findings themselves are real and already
// paid for, so they go where a reviewer will actually read them.
function withUnresolvedFindings(report, findings) {
  const notes = findings.map(
    (f) => 'Unresolved design review finding (' + f.severity + ', ' + f.section + '): ' + f.issue + ' Suggested fix: ' + f.recommendation
  );
  return { ...report, openQuestions: (report.openQuestions || []).concat(notes) };
}

const DESIGN_CRITIQUE_TOOL = {
  type: 'function',
  function: {
    name: 'report_design_critique',
    description: 'Report fitness-for-purpose defects in a proposed technical design, judged against the requirement it is meant to satisfy.',
    parameters: {
      type: 'object',
      properties: {
        findings: {
          type: 'array',
          description: 'One entry per real defect. Empty if the design is genuinely sound — do not invent findings to appear thorough.',
          items: {
            type: 'object',
            properties: {
              section: { type: 'string', description: "Which part of the report is wrong, by field name — e.g. 'techStack', 'architecture', 'dataModel', 'securityDesign'." },
              severity: {
                type: 'string',
                description: "'high' = the design will fail at its core job, misses a governing industry standard, or omits a safety/compliance obligation. 'medium' = it works but a materially better-fitting approach exists, or a promised capability is only partly delivered. 'low' = cosmetic or stylistic; these are ignored, so only use it for genuinely minor notes.",
              },
              issue: { type: 'string', description: 'The specific defect and the concrete circumstance under which it bites — name the real-world condition that breaks it, not a generic concern.' },
              recommendation: { type: 'string', description: 'The specific change to make, naming the replacement approach, standard, or component.' },
            },
            required: ['section', 'severity', 'issue', 'recommendation'],
          },
        },
      },
      required: ['findings'],
    },
  },
};

// Deliberately given ONLY the requirement and the finished document — no
// intake transcript, no generation context. Judging the artifact cold is
// the entire point; a critic carrying the author's reasoning tends to
// ratify it.
async function runDesignCritique({ summary, report }) {
  const messages = [
    {
      role: 'system',
      content:
        'You are a senior engineer with deep domain experience, reviewing a proposed technical design before it goes to a build team. You did not write it. Your job is to find where it will fail in the real world — not to praise it, and not to nitpick wording. ' +
        'Judge it on fitness for purpose, specifically: (1) will each chosen component actually work under this build\'s real operating conditions — the physical environment, scale, duty cycle, and user behaviour the requirement describes? (2) does an established industry standard, protocol, or published specification already govern this problem domain, and has the design ignored it in favour of a general-purpose or hobbyist-tier substitute? (3) does the design genuinely deliver every capability the requirement promises, or has a promise been quietly downgraded to something weaker? (4) does the data model claim fields the chosen components physically cannot produce? (5) where the build touches physical systems, public spaces, money, or regulated data, has it defined failure/safe-state behaviour and named the applicable safety or compliance constraint? ' +
        'Report only defects you can tie to a concrete failure circumstance. If the design is genuinely sound, return an empty findings array — a clean review is a valid outcome and inventing filler findings is worse than none. ' +
        'Call report_design_critique exactly once.',
    },
    {
      role: 'user',
      content:
        'Requirement this design must satisfy:\n' + JSON.stringify(summary, null, 2) +
        '\n\nProposed design to review:\n' + JSON.stringify(report, null, 2),
    },
  ];
  return callForcedTool({
    model: DETAILED_REPORT_MODEL,
    messages,
    tool: DESIGN_CRITIQUE_TOOL,
    maxTokens: 2500,
    retries: 0,
    timeoutMs: 60000,
    reasoning: { enabled: false },
  });
}

// Reuses DETAILED_REPORT_TOOL rather than defining a patch-shaped schema,
// so a revised report is structurally identical to a freshly generated one
// and needs no special handling downstream.
async function patchDetailedReport({ summary, report, findings }) {
  const findingsText = findings
    .map((f) => '- [' + f.severity + '] ' + f.section + ': ' + f.issue + '\n  Fix: ' + f.recommendation)
    .join('\n');
  const messages = [
    {
      role: 'system',
      content:
        'You are revising a technical design document to resolve defects a senior reviewer found in it. Return the COMPLETE corrected document by calling generate_detailed_report — every field, not just the ones you changed. ' +
        'Apply each finding properly rather than superficially: if a component is being replaced, update every place it appears — architecture prose, architectureDiagram, techStack, dataModel, timeline, and deploymentAndOperations must all stay consistent with each other afterwards. A document that swaps a component in one section and leaves the old one referenced elsewhere is worse than the original. ' +
        'Preserve everything the reviewer did not object to, exactly as written. ' +
        'Where a finding cannot be fully resolved without information nobody has yet — a site survey, a hardware specification, a client decision — make the best-supported choice available and record what still needs confirming in openQuestions. Never silently drop a finding: anything you could not fully address must be visible in openQuestions or assumptions. ' +
        'Keep the same trade-off discipline as the original: each techStack rationale still needs the alternative considered and the limitation of the choice made.',
    },
    {
      role: 'user',
      content:
        'Requirement this design must satisfy:\n' + JSON.stringify(summary, null, 2) +
        '\n\nCurrent design:\n' + JSON.stringify(report, null, 2) +
        '\n\nReviewer findings to resolve:\n' + findingsText +
        '\n\nReturn the complete corrected document now by calling generate_detailed_report.',
    },
  ];
  // One retry, unlike the initial generation: this payload is the whole
  // document including both Mermaid diagrams, and a bad JSON escape
  // anywhere in it fails the parse (seen live). A resample usually doesn't
  // repeat the same escaping mistake, and losing the rewrite entirely is
  // the worse outcome.
  return callForcedTool({
    model: DETAILED_REPORT_MODEL,
    messages,
    tool: DETAILED_REPORT_TOOL,
    maxTokens: 10000,
    retries: 1,
    timeoutMs: 90000,
    reasoning: { enabled: false },
  });
}

// FSD edits use small path-based operations over the complete saved
// document. This keeps edits fast while allowing the same prompt to modify
// any title, requirement, detailed-report, nested-object, or array field.
const FSD_CHAT_EDIT_TOOL = {
  type: 'function',
  function: {
    name: 'apply_fsd_edit',
    description: "Apply the user's requested changes by returning small path-based operations plus a short confirmation.",
    parameters: {
      type: 'object',
      properties: {
        changeSummary: {
          type: 'string',
          description: "One or two sentences confirming exactly what changed.",
        },
        operations: {
          type: 'array',
          description: 'Minimal edits to apply. Use dot paths and numeric array indexes for nested values.',
          items: {
            type: 'object',
            properties: {
              target: {
                type: 'string',
                enum: ['title', 'requirement', 'detailedReport'],
                description: 'Which stored part of the FSD this operation changes.',
              },
              path: {
                type: 'string',
                description: "Dot path inside the target, e.g. 'preferredCodeGenModel', 'techStack.1.choice', or 'functionalRequirements'. Use an empty string only for target title.",
              },
              value: {
                description: 'Complete replacement value at this path. May be a string, number, boolean, object, or array.',
              },
            },
            required: ['target', 'path', 'value'],
          },
        },
      },
      required: ['changeSummary', 'operations'],
    },
  },
};

function buildFsdChatSystemPrompt(originatorLabel, actorRole) {
  return `You are the FSD editing assistant inside Stark Digital's AI Software Factory, helping ${actorRole} revise the detailed report for ${originatorLabel}'s requirement.

The next message describes one or more changes. Apply exactly what the user asks by returning the smallest possible operations array. The complete editable document has three targets:
- title: the visible FSD heading; use path "".
- requirement: the original summary and fields shown before the detailed sections.
- detailedReport: objective, architecture, diagrams, stack, flows, data model, pages, security, operations, timeline, assumptions, and open questions.

Paths use dot notation and numeric array indexes. Examples: requirement.preferredCodeGenModel is target=requirement/path=preferredCodeGenModel; the second technology choice is target=detailedReport/path=techStack.1.choice. Replace a whole array when the user asks to add, remove, or substantially rewrite its items. For a rename, update title and every text field in which the old product name must also change. Do not return unchanged values. Provide a short changeSummary that truthfully describes the operations.

CONSISTENCY RULES (mandatory):
- If architecture, techStack, authentication, an integration, service, component, or end-to-end flow changes, also return a complete updated detailedReport.architectureDiagram operation reflecting that change.
- If dataModel changes, also return a complete updated detailedReport.dbSchemaDiagram operation containing every entity and current relationship.
- If a diagram itself is requested, return the complete valid Mermaid source for that diagram, beginning with flowchart/graph or erDiagram as appropriate.
- Never claim a related diagram was updated unless its operation is present.
Call apply_fsd_edit exactly once.`;
}

// One turn of the FSD edit chat. Takes the current report plus the running
// edit conversation (which already ends on the newest user instruction) and
// returns a small patch plus a short confirmation to show in the chat.
async function runFsdChatEdit({ originatorLabel, actorRole, title, requirement, detailedReport, history }) {
  // Only the newest user instruction is actionable. Replaying the complete
  // edit history made the model repeat old changes (for example changing
  // preferredCodeGenModel again while processing a later OTP request).
  const latestInstruction = [...history].reverse().find((entry) => entry.role === 'user');
  const messages = [
    { role: 'system', content: buildFsdChatSystemPrompt(originatorLabel, actorRole) },
    {
      role: 'user',
      content:
        'Current FSD title: ' + title +
        '\n\nCurrent original requirement:\n' + JSON.stringify(requirement, null, 2) +
        '\n\nCurrent detailed report:\n' + JSON.stringify(detailedReport, null, 2),
    },
    { role: 'user', content: (latestInstruction && latestInstruction.content) || 'Apply the requested edit.' },
  ];

  let result = await callForcedTool({ model: REPORT_MODEL, messages, tool: FSD_CHAT_EDIT_TOOL, maxTokens: 5000, retries: 2 });
  const paths = (result.operations || []).map((operation) => {
    // Tolerate the common non-strict shape target="detailedReport.foo"
    // when the intended schema was target="detailedReport", path="foo".
    if (typeof operation.target === 'string' && operation.target.includes('.')) {
      const dot = operation.target.indexOf('.');
      return { target: operation.target.slice(0, dot), path: operation.target.slice(dot + 1) };
    }
    return { target: operation.target, path: operation.path };
  });
  const reportPaths = paths.filter((item) => item.target === 'detailedReport').map((item) => item.path);
  const changesArchitecture = reportPaths.some((path) =>
    ['architecture', 'techStack', 'userFlow'].some((section) => path === section || path.startsWith(section + '.'))
  );
  const changesDataModel = reportPaths.some((path) => path === 'dataModel' || path.startsWith('dataModel.'));
  const missingArchitectureDiagram = changesArchitecture && !reportPaths.includes('architectureDiagram');
  const missingDbDiagram = changesDataModel && !reportPaths.includes('dbSchemaDiagram');

  if (missingArchitectureDiagram || missingDbDiagram) {
    const missing = [
      missingArchitectureDiagram ? 'a complete architectureDiagram operation' : null,
      missingDbDiagram ? 'a complete dbSchemaDiagram operation' : null,
    ].filter(Boolean).join(' and ');
    result = await callForcedTool({
      model: REPORT_MODEL,
      messages: messages.concat({
        role: 'user',
        content: `[editor supervisor] Regenerate the edit operations. The requested change requires ${missing}. Include the requested text/data changes and the corresponding complete valid Mermaid diagram updates in the same response. Do not repeat any older edit request.`,
      }),
      tool: FSD_CHAT_EDIT_TOOL,
      maxTokens: 7000,
      retries: 2,
    });
  }
  return result;
}

// Fixed department taxonomy — every team split must use exactly these
// names, so team leads always know which queue to check regardless of what
// the requirement was.
const TEAM_DEPARTMENTS = ['QA', 'AI', 'Development', 'DevOps', 'Sales & Marketing'];

// Each department receives an execution-focused mini-FSD containing its
// objective, scoped architecture, tech stack, phased plan, and dependencies.
// Shared diagrams and system-wide detail remain in the main approved FSD.
const TEAM_SPLIT_TOOL = {
  type: 'function',
  function: {
    name: 'split_team_reports',
    description: "Split an approved detailed report (FSD) into smaller, department-specific FSDs — one per department that has real work here, each following the FSD's own structure but scoped to only that department's slice of the system.",
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
              architecture: {
                type: 'string',
                description: "The end-to-end architecture and data flow for the slice of the system this department owns, at the same level of concrete detail as the main FSD's architecture section — described in prose, scoped to just this department's responsibility, not the whole system.",
              },
              techStack: {
                type: 'array',
                description: 'Only the technology choices this department is directly responsible for — skip layers another department owns.',
                items: {
                  type: 'object',
                  properties: {
                    layer: { type: 'string' },
                    choice: { type: 'string', description: TECH_STACK_CHOICE_DESCRIPTION },
                    rationale: { type: 'string', description: 'Why this choice fits, in one sentence.' },
                  },
                  required: ['layer', 'choice', 'rationale'],
                },
              },
              plan: {
                type: 'array',
                description: 'The ordered, phased implementation plan for this department — 3 to 6 phases, each a concrete milestone the team lead can hand straight to their team. This is the real deliverable: do not collapse it into a single flat task list.',
                items: {
                  type: 'object',
                  properties: {
                    phase: { type: 'string', description: 'Short phase name, e.g. "Phase 1: Schema & data model".' },
                    description: { type: 'string', description: 'What this phase accomplishes and why it comes at this point in the sequence.' },
                    tasks: { type: 'array', items: { type: 'string' }, description: 'Concrete, actionable build tasks that make up this phase.' },
                  },
                  required: ['phase', 'tasks'],
                },
              },
              dataModel: {
                type: 'array',
                description:
                  'Every entity this department reads or writes — including ones another department owns, not just the ones it creates. ' +
                  'Copy each entity VERBATIM from the approved FSD\'s dataModel: identical entity name, identical field names, identical spelling and casing, character for character. Do not rename, re-case, pluralise, abbreviate, or "tidy" anything, and do not invent an entity the FSD does not define. ' +
                  'Departments generate their code independently and never see each other\'s output, so these names are the only thing making the finished modules fit together — if two departments describe the same entity differently, their code will not combine.',
                items: {
                  type: 'object',
                  properties: {
                    entity: { type: 'string', description: "The entity name exactly as the FSD's dataModel spells it." },
                    fields: { type: 'array', items: { type: 'string' }, description: 'The field names exactly as the FSD spells them — at minimum every field this department touches, plus the identifier other departments join on.' },
                    ownedByThisDepartment: { type: 'boolean', description: 'True if this department creates and owns the entity; false if it only reads or references an entity another department owns.' },
                  },
                  required: ['entity', 'fields', 'ownedByThisDepartment'],
                },
              },
              dependencies: { type: 'array', items: { type: 'string' }, description: 'What this department needs from another department, external vendor, or approval before it can start. Name the specific interface where one exists — the endpoint, entity, or event, using the same names as the FSD. Empty array only if this department genuinely depends on nothing.' },
            },
            required: ['team', 'objective', 'architecture', 'techStack', 'plan', 'dataModel', 'dependencies'],
          },
        },
      },
      required: ['teamReports'],
    },
  },
};

function buildTeamSplitSystemPrompt(originatorLabel) {
  return `You are the delivery lead inside Stark Digital's AI Software Factory. The detailed report (FSD) below, for ${originatorLabel}'s requirement, has just cleared final approval — your job is to split it into smaller, department-specific FSDs by calling split_team_reports.

Stark Digital has exactly five departments: ${TEAM_DEPARTMENTS.join(', ')}. Assign work to whichever of these five actually have something to do — skip any with nothing to do, but never invent a department outside this list.

Each department's entry is an execution-focused mini-FSD: provide its objective, scoped architecture, concrete tech stack, phased implementation plan (3-6 sequential phases with actionable tasks), the data model it touches, and cross-department dependencies. Keep each package concise and specific to that department — the approved FSD already holds the shared diagrams, pages, and security design, so do not copy those large sections into every package.

THESE PACKAGES MUST RECOMBINE INTO ONE WORKING PRODUCT. Each department will generate its code from its own package alone, in isolation, never seeing another department's package or output. Whatever you write here is the only thing keeping the finished modules compatible, so:
- Shared entities must be described identically everywhere. If two departments both touch an entity, its name and field names must match character for character in both packages, copied verbatim from the FSD's dataModel. "Return_Items" in one package and "ReturnItems" in another produces two modules whose foreign keys do not resolve — a broken build, not a cosmetic mismatch.
- Include entities a department only reads. A department that consumes another's data still needs that entity in its dataModel, marked as not owned by it, or it will invent its own incompatible version of the same thing.
- Every entity needs exactly one owner — never zero. Set ownedByThisDepartment true in precisely one package and false everywhere else. An entity marked read-only in every package is the worst outcome: every department assumes someone else creates that table, so nobody generates its schema and the combined build has no such table at all. Before you finish, check each distinct entity you have mentioned anywhere and confirm exactly one package claims it.
- Name the seams. Where one department calls another's API, consumes its events, or reads its tables, state that in dependencies using the same endpoint and entity names both sides will use.
Call split_team_reports exactly once.`;
}

// Runs once, right after the approval chain is fully cleared. Takes the
// already-generated detailed report and splits it into per-discipline
// mini-FSDs for team leads — the last step of Phase 1's demo scope.
async function runTeamSplit({ originatorLabel, detailedReport }) {
  const messages = [
    { role: 'system', content: buildTeamSplitSystemPrompt(originatorLabel) },
    { role: 'user', content: 'Approved detailed report:\n' + JSON.stringify(detailedReport, null, 2) },
  ];
  // Five department-scoped mini-FSDs need headroom, but this remains an
  // interactive production-release action. Bound it to one request so a
  // provider stall cannot hold the VP/TL handoff for several minutes.
  const result = await callForcedTool({
    model: REPORT_MODEL,
    messages,
    tool: TEAM_SPLIT_TOOL,
    maxTokens: 8000,
    retries: 1,
    timeoutMs: 90000,
  });
  result.teamReports = normalizeEntityOwnership(result.teamReports || []);
  return result;
}

// Ownership has to come out of the split with exactly one owner per entity,
// and the prompt asking for that is not enough on its own (observed live:
// four entities every department listed as read-only and none claimed).
// Both failure modes break the combined build in the same way — an entity
// nobody owns means nobody generates its schema, and one two departments
// both own means two conflicting definitions of the same table — so both
// are repaired here deterministically rather than spending another model
// call on it.
function normalizeEntityOwnership(teamReports) {
  const entriesFor = (entity) =>
    teamReports.flatMap((t) => (t.dataModel || []).filter((e) => e.entity === entity).map((e) => ({ team: t.team, entry: e })));

  const allEntities = [...new Set(teamReports.flatMap((t) => (t.dataModel || []).map((e) => e.entity).filter(Boolean)))];

  for (const entity of allEntities) {
    const rows = entriesFor(entity);
    const owners = rows.filter((r) => r.entry.ownedByThisDepartment);

    if (owners.length === 1) continue;

    if (owners.length > 1) {
      // Keep the first claim, demote the rest.
      owners.slice(1).forEach((r) => { r.entry.ownedByThisDepartment = false; });
      continue;
    }

    // Unowned. Development owns the product's core application and database
    // in this taxonomy, so it is the sensible default when it touches the
    // entity at all; otherwise fall back to whichever department listed it
    // first, which at least guarantees the schema gets generated once.
    const chosen = rows.find((r) => r.team === FRONTEND_OWNING_DEPARTMENT) || rows[0];
    if (chosen) chosen.entry.ownedByThisDepartment = true;
  }

  return teamReports;
}

const TEAM_REPORT_EDIT_TOOL = {
  type: 'function',
  function: {
    name: 'apply_team_report_edit',
    description: 'Apply the Team Lead request and return the complete updated department work package.',
    parameters: {
      type: 'object',
      properties: {
        changeSummary: { type: 'string', description: 'Short confirmation of exactly what changed.' },
        updatedReport: {
          type: 'object',
          properties: TEAM_SPLIT_TOOL.function.parameters.properties.teamReports.items.properties,
          required: ['team', 'objective', 'architecture', 'techStack', 'plan'],
        },
      },
      required: ['changeSummary', 'updatedReport'],
    },
  },
};

async function runTeamReportChatEdit({ department, report, message }) {
  return callForcedTool({
    model: REPORT_MODEL,
    messages: [
      {
        role: 'system',
        content:
          `You edit the ${department} Team Lead's production work package. Apply only the requested change. ` +
          'Preserve the exact team name and all unaffected content. Return the complete updated package and a truthful short confirmation.',
      },
      { role: 'user', content: 'Current package:\n' + JSON.stringify(report, null, 2) },
      { role: 'user', content: message },
    ],
    tool: TEAM_REPORT_EDIT_TOOL,
    maxTokens: 6000,
    retries: 1,
    timeoutMs: 60000,
  });
}

// ─── Code Generation ──────────────────────────────────────────────────────────

// Models available for the TL's plug-and-play model selector. The Claude
// entries use Anthropic's own native model IDs and are called directly
// against Anthropic's API (see anthropicClient above); every other entry is
// an OpenRouter model ID ("vendor/model") and goes through the OpenRouter
// client instead — ANTHROPIC_DIRECT_MODELS below is what runCodeGen checks
// to decide which one to use.
const CODE_GEN_MODELS = {
  'claude-sonnet-5': 'Claude Sonnet 5 (default)',
  'claude-opus-5': 'Claude Opus 5',
  'claude-haiku-4-5-20251001': 'Claude Haiku 4.5',
  'deepseek/deepseek-v3.2:nitro': 'DeepSeek V3.2',
  'anthropic/claude-sonnet-4.5': 'Claude Sonnet 4.5 (via OpenRouter)',
  'openai/gpt-4o': 'GPT-4o',
  'openai/gpt-4o-mini': 'GPT-4o Mini',
  'google/gemini-2.5-flash': 'Gemini 2.5 Flash',
};

const DEFAULT_CODE_GEN_MODEL = 'claude-sonnet-5';
const ANTHROPIC_DIRECT_MODELS = new Set(['claude-sonnet-5', 'claude-opus-5', 'claude-haiku-4-5-20251001']);

// Generates production-ready code for a team work package.
// Returns { files: [{ path: string, content: string }] }
// The model produces one JSON object listing every file to generate, then we
// run individual file-generation passes so the caller can stream progress.
// Only the department that actually owns the product's UI gets the extra
// sandbox-ready frontend + database files below — QA/AI/DevOps/Sales &
// Marketing keep generating exactly whatever their own scope calls for.
const FRONTEND_OWNING_DEPARTMENT = 'Development';

// The sandbox/project-demo files are the one generated artifact that never
// goes through any of the pipeline's other checks (Semgrep, SonarQube, the
// real `node --check`/build in pipelineRunner.service.js all operate on
// each department's normal source files, not on JSX inlined inside an
// HTML <script> tag meant for browser-side Babel). A malformed string
// literal here previously meant the page silently rendered blank with no
// signal anywhere except a browser console nobody was looking at. This
// actually parses the generated script server-side — using the same
// preset-react transform Babel Standalone applies in-browser — so a syntax
// error is caught and retried before the file is ever saved, not
// discovered by someone clicking "UI Demo" and staring at a blank page.
function extractBabelScript(html) {
  const match = /<script[^>]*type=["']text\/babel["'][^>]*>([\s\S]*?)<\/script>/i.exec(html || '');
  return match ? match[1] : null;
}

function validateSandboxScript(html) {
  const script = extractBabelScript(html);
  if (!script || !script.trim()) return { valid: false, error: 'No <script type="text/babel"> block was found in the generated HTML.' };
  try {
    babel.transformSync(script, {
      presets: [['@babel/preset-react', { runtime: 'classic' }]],
      filename: 'sandbox.jsx',
      babelrc: false,
      configFile: false,
    });
    return { valid: true };
  } catch (err) {
    return { valid: false, error: err.message };
  }
}

// A plain Vite/webpack React app can't run in the static-preview sandbox
// (browsers can't execute raw .tsx, and there's no build step there by
// design — see securityScanner.service.js's "no execution" stance, which
// extends to the sandbox too). React loaded from a CDN with Babel Standalone
// transforming JSX live, in one self-contained file, sidesteps that
// entirely: zero build step, real React, runs directly in any browser —
// including inside the sandboxed iframe the UAT preview already uses.
// otherDepartments: [{ team, objective }, ...] for every OTHER department
// on this same requirement — so the one sandbox file can demonstrate the
// whole project's planned features (an AI recommendation panel, a DevOps
// status widget, etc.), not just this department's own slice. Everything
// still stays mocked/simulated in this one file — this does not mean
// pulling in or executing any other department's actual generated code.
// Reused by both a department's own sandbox file and the cross-department
// project-demo synthesis below — the recurring failure mode wasn't broken
// code, it was code that RAN but looked like a wireframe: bare unstyled
// divs, literal "---" placeholders instead of plausible numbers, a generic
// h1 and nothing else. Spelling out concrete visual expectations (not just
// "make it nice") is what actually moves the output.
const VISUAL_POLISH_GUIDANCE =
  'Make this look like a real, professionally designed product, not a wireframe — a proper layout (e.g. a sidebar or top nav, a content area with real spacing/padding), a cohesive color palette (2-3 colors plus neutrals, not browser defaults), readable typography with clear visual hierarchy (headings, body text, labels sized and weighted differently), and styled interactive elements (buttons, inputs, cards with subtle shadows/borders/rounded corners). ' +
  'Every number, name, date, and status shown must be a specific, plausible value (e.g. "1,204 units", "Jane Cooper", "Shipped 2 days ago") — never a literal placeholder like "---", "N/A", "Lorem ipsum", or "TBD". If real content isn\'t available yet, invent realistic-looking mock content rather than leaving a visible placeholder.';

// The recurring failure mode here wasn't visual — it was code that LOOKED
// finished but wasn't: a button with no onClick, a component importing a
// file that was never generated, JSX with an unescaped quote that breaks
// the parser. This is checked mechanically too (see validateSandboxScript
// below, which actually parses the output before it ships) — but catching
// it after the fact means one wasted round-trip; naming the failure modes
// up front is what avoids that round-trip in the first place.
const FUNCTIONAL_COMPLETENESS_GUIDANCE =
  'This file must actually work when opened, not just look right: every button, link, and interactive element MUST have a real onClick/onChange handler that does something observable (updates state, shows a result, toggles a view) — never a decorative element with no handler at all. ' +
  'Every component you reference (e.g. <Foo />) must be defined in this same file — never reference a component, function, or import that doesn\'t exist anywhere in the file you\'re writing. ' +
  'Double-check every string literal is properly closed and quotes inside strings are escaped (e.g. write 27\\" or use a template literal, never leave a stray unescaped quote) — a single malformed string breaks the entire script and the whole page renders blank.';

// Observed live: asked to "represent every department," models default to
// one top-level nav tab per department, literally named after the internal
// team ("QA", "DevOps", "AI", "Development") — the result reads as an
// internal org chart, not a believable product. This is what actually keeps
// it feeling like ONE cohesive project instead of N bolted-together demos.
const DEPARTMENT_INTEGRATION_GUIDANCE =
  'Organize navigation and layout around real user-facing workflows for this actual product (e.g. for a returns portal: Dashboard, Returns, Warranty Claims) — never around internal department/team names. Do NOT add a separate top-level tab, nav item, or page literally named after an internal department (e.g. "QA", "DevOps", "AI", "Development") — that exposes internal org structure instead of a believable product. ' +
  'Instead, weave each department\'s actual capability into wherever a real user would naturally encounter it — an AI-powered feature (e.g. damage assessment) belongs inside the workflow it assists (e.g. shown while reviewing a return), not as its own menu item. ' +
  'Departments whose work is purely internal engineering (e.g. QA test results, CI/CD pipeline status, sprint/task tracking) are not something a real customer-facing product would show end users at all — if that department must be represented, fold it into a single, clearly-internal area (e.g. one small "System Health" panel) instead of giving it equal billing next to real product features.';

function buildFrontendSandboxInstructions(otherDepartments) {
  const otherSection = (otherDepartments && otherDepartments.length)
    ? '\n\nThis is a full-project prototype, not just this department\'s own slice — the other departments on this same requirement are building:\n' +
      otherDepartments.map((d) => '- ' + d.team + ': ' + (d.objective || '')).join('\n') +
      '\nRepresent each of these in the UI too, with realistic mock data/behavior standing in for that department\'s feature — actually build a working, clickable mock of each, not just a mention. ' +
      DEPARTMENT_INTEGRATION_GUIDANCE
    : '';

  return (
    'This department owns the product\'s UI, so the file plan MUST also include exactly these two additional files:\n' +
    '1. "frontend/index.html" — a SELF-CONTAINED React demo of the ACTUAL product UI for this requirement (real screens/components implied by the Data Model and Architecture below — not a generic placeholder), built with React 18 + ReactDOM loaded from the unpkg CDN, with JSX transformed in-browser via Babel Standalone (also from CDN) — NOT a Vite/webpack/Next.js setup, since this file must run directly in a plain browser with zero build step. ' +
    'Structure: <script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>, <script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>, <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>, then exactly ONE inline <script type="text/babel"> tag containing the ENTIRE app — every component, hook, and mock data, all in this one file. Do not reference any separate .js/.jsx file from this HTML; nothing outside this one script tag. ' +
    'The app must make NO real network requests — instead of fetching a backend, define an in-memory mock dataset directly in this same script (matching the Data Model below) and use React state/hooks to read and update it, so the whole thing is fully self-contained and works with the file opened directly, no server required. ' +
    VISUAL_POLISH_GUIDANCE + ' ' +
    FUNCTIONAL_COMPLETENESS_GUIDANCE +
    otherSection + '\n' +
    '2. A real database schema file appropriate to this stack (e.g. "database/schema.sql" for a relational database, or an equivalent schema/model definition file for the chosen database) — the actual DDL/schema for the Data Model below, as a genuine deliverable. This file is NOT executed by the sandbox preview — it\'s real code for whoever provisions the actual database.'
  );
}

// Applied to EVERY department's own code generation (not just the frontend
// owner) — the goal isn't a shared demo file here, it's that 5
// independently-generated codebases don't each invent their own
// incompatible conventions for the same underlying product. A department
// still only builds its own scope; this just steers HOW it builds it.
function buildCrossDepartmentConsistencyInstructions(otherDepartments) {
  if (!otherDepartments || !otherDepartments.length) return '';

  // The entities other departments OWN. Style rules alone were not enough:
  // agreeing on camelCase does not stop one department creating a table
  // "Return_Items" while another writes a foreign key against
  // "ReturnItems" (observed live — the two modules could not be combined).
  // Naming the owned entities explicitly is what removes the ambiguity.
  const ownedElsewhere = otherDepartments.flatMap((d) =>
    (d.dataModel || [])
      .filter((e) => e.ownedByThisDepartment && e.entity)
      .map((e) => '- ' + e.entity + ' (owned by ' + d.team + ')' + (e.fields && e.fields.length ? ', fields: ' + e.fields.join(', ') : ''))
  );

  return '\n\nThis department\'s code is one piece of a single combined product, not a standalone project — the other departments building the rest of it are:\n' +
    otherDepartments.map((d) => '- ' + d.team + ': ' + (d.objective || '') + (d.architecture ? ' — ' + d.architecture : '')).join('\n') +
    (ownedElsewhere.length
      ? '\n\nThese entities are OWNED AND DEFINED BY ANOTHER DEPARTMENT. Reference them by exactly these names and field names — character for character — and do NOT emit your own schema, migration, or CREATE TABLE for them; that department is already generating it, and a second conflicting definition breaks the combined build:\n' +
        ownedElsewhere.join('\n')
      : '') +
    '\nWrite this department\'s own code so it genuinely interoperates with theirs: reuse the exact entity and field names given in your own data model rather than re-spelling them, and follow the conventions a reasonable engineering team would agree on across the whole product — consistent naming (camelCase JSON fields, an "id" field on every entity, timestamps as ISO 8601) and standard REST conventions (resource-based paths, JSON bodies, conventional HTTP status codes) for any API this department exposes. Do not invent a bespoke, incompatible convention purely for this one module\'s own convenience.';
}

const FRONTEND_COVERAGE_CHECK_TOOL = {
  type: 'function',
  function: {
    name: 'report_frontend_coverage',
    description: 'Report which of the given other departments have NO real, working mock UI section yet in the given frontend/index.html.',
    parameters: {
      type: 'object',
      properties: {
        missingDepartments: {
          type: 'array',
          description: 'Team names (exactly as given) with no genuine clickable mock section in this file yet — a passing mention in text does not count. Empty if every department is represented.',
          items: { type: 'string' },
        },
      },
      required: ['missingDepartments'],
    },
  },
};

const FRONTEND_COVERAGE_FIX_TOOL = {
  type: 'function',
  function: {
    name: 'patch_frontend_coverage',
    description: 'Return the complete, revised frontend/index.html with working mock sections added for the missing departments.',
    parameters: {
      type: 'object',
      properties: {
        content: { type: 'string', description: 'The COMPLETE revised file content — the whole file, still one self-contained React+Babel+CDN HTML file, not a diff or snippet.' },
      },
      required: ['content'],
    },
  },
};

// The plan/per-file prompts already ask for this, but — like the security
// auto-fix pass below — compliance isn't reliable: the model sometimes
// builds the other departments' features as separate, never-loaded
// component files instead of folding them into the one file the sandbox
// actually runs. This checks the ACTUAL generated file and patches in
// whatever got left out, the same verify-then-fix shape as scanAndAutoFix.
async function checkFrontendCoverage({ html, otherDepartments, model }) {
  const deptList = otherDepartments.map((d) => '- ' + d.team + ': ' + (d.objective || '')).join('\n');
  const messages = [
    {
      role: 'system',
      content:
        'You are reviewing a generated UAT sandbox file for a multi-department product. Determine which of the listed OTHER departments do NOT yet have a real, working mock UI representation in this file (a genuine clickable feature with plausible mock data — not just a mention in text or a comment). ' +
        'A department counts as represented whether its capability appears as its own section OR is woven into an existing workflow (e.g. an AI feature shown inside a review screen) — do not require a dedicated top-level tab per department. ' +
        'Call report_frontend_coverage exactly once.',
    },
    { role: 'user', content: 'Other departments to check for:\n' + deptList + '\n\nfrontend/index.html:\n```html\n' + html + '\n```' },
  ];
  return callForcedToolAny({ model, messages, tool: FRONTEND_COVERAGE_CHECK_TOOL, maxTokens: 500, timeoutMs: 60000 });
}

async function patchFrontendCoverage({ html, otherDepartments, missingDepartments, model }) {
  const missingDetail = otherDepartments
    .filter((d) => missingDepartments.includes(d.team))
    .map((d) => '- ' + d.team + ': ' + (d.objective || ''))
    .join('\n');
  const messages = [
    {
      role: 'system',
      content:
        'You are extending a self-contained React+Babel+CDN sandbox demo (frontend/index.html) for a multi-department product. ' +
        'Add a real, working, clickable mock UI representation for EACH of the missing departments below — realistic mock data/behavior standing in for that department\'s feature — while preserving every existing section and behavior exactly as-is. ' +
        DEPARTMENT_INTEGRATION_GUIDANCE + ' ' +
        'The result must remain ONE self-contained file: React 18 + ReactDOM + Babel Standalone from the unpkg CDN, one inline <script type="text/babel"> tag, no separate .js/.jsx file references, no real network requests — everything in-memory mock data. ' +
        'Call patch_frontend_coverage exactly once with the complete revised file.',
    },
    { role: 'user', content: 'Missing departments to add:\n' + missingDetail + '\n\nCurrent frontend/index.html:\n```html\n' + html + '\n```' },
  ];
  return callForcedToolAny({ model, messages, tool: FRONTEND_COVERAGE_FIX_TOOL, maxTokens: 12000, timeoutMs: 120000 });
}

// Split into two fields, with the script FIRST, instead of one giant
// "content" field — observed live: asked for one monolithic file, models
// (Gemini 2.5 Flash via OpenRouter in particular) reliably front-load a
// long, richly-detailed <style> block (chasing VISUAL_POLISH_GUIDANCE) and
// then cut off before ever reaching the actual <script type="text/babel">
// app logic, even well within the token budget — 3 separate real attempts
// each stopped partway through the stylesheet. Declaring appScript as the
// first required property gets the part that actually matters written
// while the model still has its full output budget ahead of it.
const PROJECT_DEMO_TOOL = {
  type: 'function',
  function: {
    name: 'write_project_demo',
    description: 'Return the two pieces of one self-contained HTML file synthesizing every department\'s actual work into a single cohesive product demo.',
    parameters: {
      type: 'object',
      properties: {
        appScript: { type: 'string', description: 'The COMPLETE React/JSX app — everything that goes inside the <script type="text/babel"> tag, with no wrapping <script> tags of your own. A <div id="root"> already exists in the page before this script runs — do NOT define your own container element anywhere in your JSX; mount into the existing one, e.g. ReactDOM.render(<App />, document.getElementById("root")) or createRoot(document.getElementById("root")).render(<App />). Write this FIRST, before stylesAndHead.' },
        stylesAndHead: { type: 'string', description: 'The CDN <script> tags for React/ReactDOM/Babel Standalone plus one <style> block with all CSS — everything that comes BEFORE the app script in the final file. Do NOT include a <div id="root"> yourself — one is already provided.' },
      },
      required: ['appScript', 'stylesAndHead'],
    },
  },
};

// The combine-everything-into-one-demo step, run once (and cached) after
// every department has finished generating. Unlike buildFrontendSandboxInstructions
// above (one department's own file, at its own generation time, mocking the
// others from just a one-line objective), this reads what every department
// ACTUALLY built — its real file list and, for the frontend-owning
// department, its actual generated sandbox file as a concrete reference —
// and writes a fresh file grounded in that, not a guess made before any of
// it existed.
// departments: [{ team, objective, files: [{path, description}], referenceHtml? }]
async function runProjectDemoSynthesis({ title, departments, model }) {
  const departmentsText = departments.map((d) => {
    const fileList = (d.files || []).map((f) => '  - ' + f.path + (f.description ? ': ' + f.description : '')).join('\n');
    return '### ' + d.team + '\nObjective: ' + (d.objective || '') + '\nFiles actually built:\n' + fileList;
  }).join('\n\n');

  const referenceEntry = departments.find((d) => d.referenceHtml);
  const referenceSection = referenceEntry
    ? '\n\nOne department (' + referenceEntry.team + ') already built its own working self-contained React+Babel+CDN demo, reproduced below. Use it as your starting point and structural reference — extend and restyle it so every other department\'s capability is genuinely reflected too, integrated the way described above rather than bolted on as new top-level tabs:\n```html\n' + referenceEntry.referenceHtml.slice(0, 12000) + '\n```'
    : '';

  const messages = [
    {
      role: 'system',
      content:
        'You are synthesizing ONE combined product demo for "' + (title || 'this project') + '" from what several departments actually built, each in their own separate codebase (different tech stacks — you cannot run or import their real code). ' +
        'Write a SELF-CONTAINED React demo: React 18 + ReactDOM loaded from the unpkg CDN, JSX transformed in-browser via Babel Standalone (also CDN) — NOT a Vite/webpack/Next.js setup, zero build step. ' +
        'You will return it as two separate pieces (see the tool schema) so write appScript FIRST: the complete React/JSX app, with no wrapping <script> tag — just the component code, hooks, and mock dataset. Then write stylesAndHead: the three CDN <script src="..."> tags (react, react-dom, @babel/standalone) plus one <style> block with the CSS. A <div id="root"></div> is already placed for you right before the app script runs — mount into that existing element (document.getElementById("root")); do not declare a container element of your own anywhere. ' +
        'Make it genuinely represent EVERY department listed below with plausible mock data reflecting what that department\'s own file list shows they actually built (not a generic guess) — e.g. if a department\'s files show an "inventory forecast" component, include a real forecast-looking panel with plausible numbers, not just a label. ' +
        DEPARTMENT_INTEGRATION_GUIDANCE + ' ' +
        'The app must make NO real network requests — use an in-memory mock dataset and React state/hooks only. ' +
        VISUAL_POLISH_GUIDANCE + ' ' +
        FUNCTIONAL_COMPLETENESS_GUIDANCE +
        referenceSection +
        '\n\nCall write_project_demo exactly once with both fields.',
    },
    { role: 'user', content: 'Departments and what each actually built:\n\n' + departmentsText },
  ];

  // The mount div is injected here, deterministically, rather than trusted
  // to the model — leaving it to the model meant it sometimes never wrote
  // one outside the script at all, instead defining its own <div id="root">
  // as part of the App component's OWN rendered JSX. That's a real
  // chicken-and-egg bug: nothing with that id exists in the actual DOM
  // until AFTER a successful render, so document.getElementById("root") at
  // render time finds nothing and ReactDOM throws "Target container is not
  // a DOM element" (error #200) — reproduced live. Guaranteeing the div
  // exists in the assembled HTML, before the script tag, removes the whole
  // failure class regardless of what the model does.
  function assemble(fields) {
    return (fields.stylesAndHead || '') + '\n<div id="root"></div>\n<script type="text/babel">\n' + (fields.appScript || '') + '\n</script>';
  }

  const MAX_ATTEMPTS = 3;
  let fields = await callForcedToolAny({ model, messages, tool: PROJECT_DEMO_TOOL, maxTokens: 14000, timeoutMs: 150000 });
  let validation = validateSandboxScript(assemble(fields));
  for (let attempt = 2; attempt <= MAX_ATTEMPTS && !validation.valid; attempt++) {
    messages.push(
      { role: 'assistant', content: JSON.stringify(fields) },
      { role: 'user', content: 'That file does not parse: ' + validation.error + '\n\nCall write_project_demo again with the COMPLETE corrected appScript and stylesAndHead.' }
    );
    fields = await callForcedToolAny({ model, messages, tool: PROJECT_DEMO_TOOL, maxTokens: 14000, timeoutMs: 150000 });
    validation = validateSandboxScript(assemble(fields));
  }
  // Silently returning here would let a still-broken result (an
  // under-length or off-topic response the model returned instead of the
  // real file) look like a success to the caller. Only hand back content
  // that has actually been verified to parse.
  if (!validation.valid) throw new Error('The model could not produce a working project demo after ' + MAX_ATTEMPTS + ' attempts: ' + validation.error);

  // Parsing fine isn't the same as actually representing every department —
  // "make this genuinely cover all four departments" is just a prompt
  // instruction, and models routinely settle for the one or two most
  // obvious tabs (e.g. a Development-only "Returns" table with the AI/QA/
  // DevOps sections left as literal placeholder text) and stop. Reuse the
  // same verify-then-patch mechanism that already guards the per-department
  // sandbox file's coverage, checked against ALL departments this time
  // rather than "every OTHER department" since there's no single owner here.
  let html = assemble(fields);
  const MAX_COVERAGE_ROUNDS = 2;
  let previousMissingCount = Infinity;
  for (let round = 1; round <= MAX_COVERAGE_ROUNDS; round++) {
    let coverage;
    try {
      coverage = await checkFrontendCoverage({ html, otherDepartments: departments, model });
    } catch (err) {
      break;
    }
    const missing = coverage.missingDepartments || [];
    if (!missing.length || missing.length >= previousMissingCount) break;
    previousMissingCount = missing.length;
    try {
      const patch = await patchFrontendCoverage({ html, otherDepartments: departments, missingDepartments: missing, model });
      if (patch && patch.content && validateSandboxScript(patch.content).valid) html = patch.content;
    } catch (err) {
      break;
    }
  }
  return { content: html };
}

async function runCodeGen({ teamReport, artifact, model, onFileProgress, otherDepartments }) {
  const chosenModel = CODE_GEN_MODELS[model] ? model : DEFAULT_CODE_GEN_MODEL;
  const useAnthropic = ANTHROPIC_DIRECT_MODELS.has(chosenModel);
  const isFrontendOwner = teamReport.team === FRONTEND_OWNING_DEPARTMENT;
  const frontendSandboxInstructions = isFrontendOwner ? buildFrontendSandboxInstructions(otherDepartments) : '';
  const consistencyInstructions = buildCrossDepartmentConsistencyInstructions(otherDepartments);

  // Step 1 — Ask the model to plan out the file tree
  const planMessages = [
    {
      role: 'system',
      content:
        'You are an expert software engineer inside Stark Digital\'s AI Software Factory. ' +
        'You are given a team work package (department-scoped requirements, tech stack, plan, data model). ' +
        'Your task is to produce a complete, production-ready codebase for this package. ' +
        'First, output a JSON object listing every file you will generate. ' +
        'Format: { "files": [ { "path": "relative/path/to/file.ext", "description": "one-line purpose" }, ... ] } ' +
        'Include ALL files: package.json, config, source files, tests, Dockerfile, README.md, etc. ' +
        (isFrontendOwner ? frontendSandboxInstructions + ' ' : '') +
        consistencyInstructions + ' ' +
        'Output ONLY valid JSON — no markdown fences, no commentary before or after.',
    },
    {
      role: 'user',
      content:
        '## Project: ' + (artifact.title || 'Untitled') + '\n\n' +
        '## Objective\n' + (teamReport.objective || '') + '\n\n' +
        '## Architecture\n' + (teamReport.architecture || '') + '\n\n' +
        '## Tech Stack\n' + JSON.stringify(teamReport.techStack || [], null, 2) + '\n\n' +
        '## Data Model\n' + JSON.stringify(teamReport.dataModel || [], null, 2) + '\n\n' +
        '## Implementation Plan\n' + JSON.stringify(teamReport.plan || [], null, 2) + '\n\n' +
        '## Dependencies\n' + JSON.stringify(teamReport.dependencies || [], null, 2) + '\n\n' +
        '## Security Design\n' + JSON.stringify(teamReport.securityDesign || [], null, 2) + '\n\n' +
        'List every file you will generate as the JSON object described.',
    },
  ];

  let filePlan;
  let planRaw;
  if (useAnthropic) {
    const planSplit = splitSystemMessage(planMessages);
    const planResponse = await anthropicClient.messages.create(
      { model: chosenModel, max_tokens: 3000, system: planSplit.system, messages: planSplit.messages },
      { timeout: 60000, maxRetries: 0 }
    );
    planRaw = ((planResponse.content[0] && planResponse.content[0].text) || '').trim();
  } else {
    const planResponse = await client.chat.completions.create(
      { model: chosenModel, messages: planMessages, max_tokens: 3000 },
      { timeout: 60000, maxRetries: 0 }
    );
    planRaw = ((planResponse.choices[0].message && planResponse.choices[0].message.content) || '').trim();
  }
  // Strip markdown fences if present
  const jsonStr = planRaw.replace(/^```(?:json)?\n?/i, '').replace(/\n?```$/i, '').trim();
  try {
    filePlan = JSON.parse(jsonStr);
  } catch (err) {
    // Fallback: construct a sensible default plan from tech stack
    filePlan = {
      files: [
        { path: 'README.md', description: 'Project overview' },
        { path: 'package.json', description: 'Node.js project manifest' },
        { path: 'src/index.js', description: 'Application entry point' },
        { path: 'src/config.js', description: 'Environment configuration' },
      ],
    };
  }

  const files = Array.isArray(filePlan.files) ? filePlan.files : [];

  // Step 2 — Generate each file individually
  const generated = [];
  for (let i = 0; i < files.length; i++) {
    const fileSpec = files[i];
    if (onFileProgress) onFileProgress({ index: i, total: files.length, path: fileSpec.path, status: 'generating' });

    // This call sees only this one file's own prompt — it never sees the
    // planning step's instructions — so anything that must shape the actual
    // code has to be repeated in full here too, specifically for this file.
    // That includes the cross-department integration rules: the planning
    // step knowing about the other departments is worth nothing if the step
    // that actually writes the lines does not, and this is the step that
    // decides real table names, real field spellings, and real endpoint
    // paths. Leaving it out is how one department ends up creating
    // "Return_Items" while another writes a foreign key to "ReturnItems".
    const isSandboxEntryFile = isFrontendOwner && fileSpec.path === 'frontend/index.html';

    const fileMessages = [
      {
        role: 'system',
        content:
          'You are an expert software engineer. Generate ONLY the complete, production-ready source code ' +
          'for the file specified below. Output ONLY the raw file content — no markdown fences, ' +
          'no explanations, no commentary. The output must be valid, runnable code exactly as it ' +
          'would appear saved to disk.' +
          consistencyInstructions +
          (isSandboxEntryFile ? '\n\n' + frontendSandboxInstructions : ''),
      },
      {
        role: 'user',
        content:
          '## Project: ' + (artifact.title || 'Untitled') + '\n' +
          '## Tech Stack: ' + (teamReport.techStack || []).map((t) => t.choice).join(', ') + '\n\n' +
          '## File to generate\n' +
          'Path: ' + fileSpec.path + '\n' +
          'Purpose: ' + (fileSpec.description || '') + '\n\n' +
          // ownedByThisDepartment travels with each entity: false means
          // another department generates that schema, and this file must
          // reference it rather than define a second, conflicting one.
          '## Context (data model — entities marked ownedByThisDepartment:false are defined by another department; reference them, never redeclare their schema)\n' +
          JSON.stringify(teamReport.dataModel || [], null, 2) + '\n\n' +
          '## Context (architecture)\n' + (teamReport.architecture || '') + '\n\n' +
          '## Context (integration points this department must honour)\n' + JSON.stringify(teamReport.dependencies || [], null, 2) + '\n\n' +
          'Generate the complete contents of this file now:',
      },
    ];

    let content = '';
    let attemptMessages = fileMessages;
    // Only the sandbox entry file gets validated and retried — it's the one
    // generated artifact nothing else in the pipeline ever parses or runs,
    // so a syntax error here would otherwise ship silently (see
    // validateSandboxScript above).
    const maxAttempts = isSandboxEntryFile ? 3 : 1;
    let lastValidationError = null;

    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      let attemptContent = '';
      try {
        // Streamed rather than awaited whole — lets the IDE panel show the
        // file actually being written token-by-token instead of a static
        // "Generating…" placeholder that snaps to full content once done.
        // The sandbox entry file has to fit an entire React app (every
        // component, every hook, the mock dataset) in one script block —
        // the usual 4000-token budget runs out mid-file for anything but
        // a trivial screen.
        const maxTokens = isSandboxEntryFile ? 12000 : 4000;
        const timeoutMs = isSandboxEntryFile ? 120000 : 90000;
        if (useAnthropic) {
          const attemptSplit = splitSystemMessage(attemptMessages);
          const stream = await anthropicClient.messages.create(
            { model: chosenModel, max_tokens: maxTokens, system: attemptSplit.system, messages: attemptSplit.messages, stream: true },
            { timeout: timeoutMs, maxRetries: 0 }
          );
          for await (const event of stream) {
            if (event.type !== 'content_block_delta' || !event.delta || event.delta.type !== 'text_delta') continue;
            const delta = event.delta.text || '';
            if (!delta) continue;
            attemptContent += delta;
            if (onFileProgress) onFileProgress({ index: i, total: files.length, path: fileSpec.path, status: 'streaming', partialContent: attemptContent });
          }
        } else {
          const stream = await client.chat.completions.create(
            { model: chosenModel, messages: attemptMessages, max_tokens: maxTokens, stream: true },
            { timeout: timeoutMs, maxRetries: 0 }
          );
          for await (const chunk of stream) {
            const delta = (chunk.choices[0] && chunk.choices[0].delta && chunk.choices[0].delta.content) || '';
            if (!delta) continue;
            attemptContent += delta;
            if (onFileProgress) onFileProgress({ index: i, total: files.length, path: fileSpec.path, status: 'streaming', partialContent: attemptContent });
          }
        }
        attemptContent = attemptContent.trim().replace(/^```[a-z]*\n?/i, '').replace(/\n?```$/i, '').trim();
      } catch (err) {
        content = '// Generation failed for this file: ' + err.message;
        break;
      }
      content = attemptContent;

      if (!isSandboxEntryFile) break;
      const validation = validateSandboxScript(content);
      if (validation.valid) break;

      lastValidationError = validation.error;
      if (attempt < maxAttempts) {
        if (onFileProgress) onFileProgress({ index: i, total: files.length, path: fileSpec.path, status: 'validation-retry', message: lastValidationError });
        attemptMessages = fileMessages.concat([
          { role: 'assistant', content: attemptContent },
          { role: 'user', content: 'That file does not parse: ' + lastValidationError + '\n\nReturn the COMPLETE corrected file — the whole file again, not just the fix.' },
        ]);
      } else if (onFileProgress) {
        onFileProgress({ index: i, total: files.length, path: fileSpec.path, status: 'validation-failed', message: lastValidationError });
      }
    }

    generated.push({ path: fileSpec.path, description: fileSpec.description || '', content });
    if (onFileProgress) onFileProgress({ index: i, total: files.length, path: fileSpec.path, status: 'done' });
  }

  // Verify the sandbox entry file actually represents every other
  // department, and patch in whatever it left out — see
  // checkFrontendCoverage/patchFrontendCoverage above for why this can't
  // just be a stronger prompt.
  if (isFrontendOwner && otherDepartments && otherDepartments.length) {
    const entry = generated.find((f) => f.path === 'frontend/index.html');
    if (entry && entry.content) {
      const MAX_COVERAGE_ROUNDS = 2;
      let previousMissingCount = Infinity;
      for (let round = 1; round <= MAX_COVERAGE_ROUNDS; round++) {
        let coverage;
        try {
          coverage = await checkFrontendCoverage({ html: entry.content, otherDepartments, model: chosenModel });
        } catch (err) {
          if (onFileProgress) onFileProgress({ index: files.length, total: files.length, path: 'frontend/index.html', status: 'coverage-error', message: err.message });
          break;
        }
        const missing = coverage.missingDepartments || [];
        if (!missing.length || missing.length >= previousMissingCount) break;
        previousMissingCount = missing.length;
        if (onFileProgress) onFileProgress({ index: files.length, total: files.length, path: 'frontend/index.html', status: 'coverage-fix', missing, round });
        try {
          const patch = await patchFrontendCoverage({ html: entry.content, otherDepartments, missingDepartments: missing, model: chosenModel });
          if (patch && patch.content) entry.content = patch.content;
        } catch (err) {
          if (onFileProgress) onFileProgress({ index: files.length, total: files.length, path: 'frontend/index.html', status: 'coverage-error', message: err.message });
          break;
        }
      }
    }
  }

  return { files: generated };
}

// ─── Post-generation: code review (security is real Semgrep + SonarQube now
// — see securityScanner.service.js — not an LLM guess) ────────────────────────
// A read-only review pass over already-generated code — no dependency
// installation, no code execution, so a generated codebase can never
// actually run on this server.

const CODE_REVIEW_TOOL = {
  type: 'function',
  function: {
    name: 'report_code_review',
    description: 'Report a code-quality review of a generated codebase — bugs, missing pieces, inconsistencies between files, anything that would fail at build or runtime.',
    parameters: {
      type: 'object',
      properties: {
        verdict: { type: 'string', enum: ['pass', 'needs_fixes'] },
        summary: { type: 'string', description: 'One or two sentences on the overall code quality/completeness.' },
        findings: {
          type: 'array',
          description: 'Leave empty if the code genuinely looks correct and complete — do not invent a finding to fill this out.',
          items: {
            type: 'object',
            properties: {
              file: { type: 'string', description: 'The actual file this finding is in.' },
              issue: { type: 'string', description: 'What is wrong, concretely.' },
              severity: { type: 'string', enum: ['low', 'medium', 'high'] },
            },
            required: ['file', 'issue', 'severity'],
          },
        },
      },
      required: ['verdict', 'summary', 'findings'],
    },
  },
};

// Bounds how much generated code gets sent to a review call — a full
// codebase can easily blow past a reasonable token budget. Notes when
// files were left out rather than silently truncating without saying so.
function buildFilesContext(files, maxChars) {
  const parts = [];
  let used = 0;
  let omitted = 0;
  for (const f of files) {
    if (!f.content) continue;
    const chunk = '### ' + f.path + '\n```\n' + f.content + '\n```\n\n';
    if (used + chunk.length > maxChars) { omitted++; continue; }
    parts.push(chunk);
    used += chunk.length;
  }
  if (omitted) parts.push('(' + omitted + ' additional file(s) omitted from this review for length.)');
  return parts.join('');
}

async function runCodeReview({ department, files }) {
  const context = buildFilesContext(files, 40000);
  const messages = [
    {
      role: 'system',
      content:
        `You are a senior engineer inside Stark Digital's AI Software Factory, reviewing the ${department} department's generated codebase below for bugs, missing pieces, and inconsistencies between files — the kind of thing that would fail at build or runtime. ` +
        'Call report_code_review exactly once. Be concrete: name the real file. Do not invent a finding just to have something to report — an empty findings list is the correct, honest result when the code is actually fine.',
    },
    { role: 'user', content: context || 'No file content available to review.' },
  ];
  return callForcedTool({ model: REPORT_MODEL, messages, tool: CODE_REVIEW_TOOL, maxTokens: 2000, timeoutMs: 60000 });
}

const SECURITY_FIX_TOOL = {
  type: 'function',
  function: {
    name: 'apply_security_fixes',
    description: 'Return corrected, complete file contents that resolve the given security findings.',
    parameters: {
      type: 'object',
      properties: {
        files: {
          type: 'array',
          description: 'One entry per file that needed a change to resolve a finding — omit any file that was already fine.',
          items: {
            type: 'object',
            properties: {
              path: { type: 'string', description: 'Must exactly match one of the file paths given.' },
              content: { type: 'string', description: 'The COMPLETE corrected file content — the whole file, not a diff or snippet.' },
            },
            required: ['path', 'content'],
          },
        },
      },
      required: ['files'],
    },
  },
};

// Runs automatically right after a module's code finishes generating, only
// when the security review that follows it actually found something —
// rewrites just the flagged files to resolve the specific findings, leaving
// everything else about them untouched. Still no code execution: this is
// the AI editing text based on the AI's own prior read of that text.
async function runSecurityAutoFix({ department, files, findings, model }) {
  const flaggedPaths = [...new Set(findings.map((f) => f.file))];
  const relevantFiles = files.filter((f) => flaggedPaths.includes(f.path) && f.content);
  if (!relevantFiles.length) return [];

  const context = relevantFiles.map((f) => '### ' + f.path + '\n```\n' + f.content + '\n```').join('\n\n');
  const findingsText = findings.map((f) => `- [${f.severity}] ${f.file}: ${f.issue}`).join('\n');

  const messages = [
    {
      role: 'system',
      content:
        `You are a security engineer inside Stark Digital's AI Software Factory, fixing flagged issues in the ${department} department's generated codebase. ` +
        'Rewrite ONLY what is necessary to resolve each finding below — preserve every file\'s existing structure, style, and unrelated logic exactly as-is. ' +
        'Call apply_security_fixes exactly once with the complete corrected content of every file that needed a change.',
    },
    { role: 'user', content: 'Findings to fix:\n' + findingsText + '\n\nFiles:\n' + context },
  ];
  // Every flagged file's COMPLETE content has to fit in the response, not
  // just a diff — with findings spread across several real files (a
  // Dockerfile plus multiple Python modules, say), 6000 tokens routinely
  // wasn't enough room, so the model would silently return only one file
  // and the rest of the findings would look like the fix "didn't work."
  const result = await callForcedToolAny({ model, messages, tool: SECURITY_FIX_TOOL, maxTokens: 16000, timeoutMs: 90000 });
  return result.files || [];
}

const BUILD_FIX_TOOL = {
  type: 'function',
  function: {
    name: 'apply_build_fixes',
    description: 'Return corrected, complete file contents that resolve the given CI build/test failure.',
    parameters: {
      type: 'object',
      properties: {
        files: {
          type: 'array',
          description: 'One entry per file that needed a change to fix the failure — this can include files not directly named in the error (e.g. package.json needs a missing dependency added, or a config file needs to be created) as well as ones that are.',
          items: {
            type: 'object',
            properties: {
              path: { type: 'string', description: 'The file\'s path — reuse an existing path exactly to edit it, or a new path to add a file that was missing entirely.' },
              content: { type: 'string', description: 'The COMPLETE file content — the whole file, not a diff or snippet.' },
            },
            required: ['path', 'content'],
          },
        },
      },
      required: ['files'],
    },
  },
};

// "Fix with AI" previously only ever touched security findings — if
// security was already clean but the real CI build/test execution itself
// failed (a missing dependency, a wrong import path, a missing build
// config), there was no automated fix path at all, only "fix manually in
// the IDE." This reads the actual failed command's real stdout/stderr —
// not a guess — and asks the model to fix whatever's actually broken,
// which can mean editing files never mentioned in the error (package.json
// needs a dependency the code already imports) or files it directly names.
async function runBuildFix({ department, files, ciExecution, model }) {
  const failedChecks = ((ciExecution && ciExecution.checks) || []).filter((c) => !c.passed);
  if (!failedChecks.length) return [];

  const failureText = failedChecks.map((c) =>
    `### Command: ${c.command}\nExit code: ${c.exitCode}\nSTDOUT:\n${(c.stdout || '(empty)').slice(-3000)}\nSTDERR:\n${(c.stderr || '(empty)').slice(-3000)}`
  ).join('\n\n');
  const context = buildFilesContext(files, 40000);

  const messages = [
    {
      role: 'system',
      content:
        `You are fixing a real CI build/test failure in the ${department} department's generated codebase. ` +
        'The failure output below is from actually running the build — treat it as ground truth, not a guess. Diagnose the real cause (missing dependency, wrong file path, a config file that was never generated, an incompatible file layout, etc.) and fix it. ' +
        'Preserve everything else about the codebase exactly as-is — only change what\'s needed to make the build succeed. ' +
        'Call apply_build_fixes exactly once with the complete corrected content of every file that needs to change (including a genuinely new file if one is missing entirely, like a build config).',
    },
    { role: 'user', content: 'Failed command(s):\n' + failureText + '\n\nFiles:\n' + context },
  ];
  const result = await callForcedToolAny({ model, messages, tool: BUILD_FIX_TOOL, maxTokens: 16000, timeoutMs: 90000 });
  return result.files || [];
}

module.exports = {
  runChatTurn, runFinalize, runDetailedReport, runTeamSplit, runFsdChatEdit, runTeamReportChatEdit,
  runCodeGen, CODE_GEN_MODELS, DEFAULT_CODE_GEN_MODEL, runCodeReview, runSecurityAutoFix,
  FRONTEND_OWNING_DEPARTMENT, runProjectDemoSynthesis, runBuildFix,
};

