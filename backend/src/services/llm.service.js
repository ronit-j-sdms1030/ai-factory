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

// Intake, BRD, FSD editing and the team split were migrated to the Python
// agent service (../../agent-service). What remains here is code generation
// and its tied fixers, which have not been migrated.
module.exports = {
  runCodeGen, CODE_GEN_MODELS, DEFAULT_CODE_GEN_MODEL, runCodeReview, runSecurityAutoFix,
  FRONTEND_OWNING_DEPARTMENT, runProjectDemoSynthesis, runBuildFix,
};

