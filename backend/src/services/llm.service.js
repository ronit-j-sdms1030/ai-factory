const OpenAI = require('openai');

// OpenRouter speaks the OpenAI chat-completions wire format, so the OpenAI
// SDK works unmodified against it — just point baseURL at OpenRouter and use
// an OpenRouter API key instead of an OpenAI one.
const client = new OpenAI({
  apiKey: process.env.OPENROUTER_API_KEY,
  baseURL: 'https://openrouter.ai/api/v1',
  defaultHeaders: {
    'HTTP-Referer': process.env.FRONTEND_ORIGIN || 'http://localhost:5173',
    'X-Title': 'Stark Digital AI Software Factory',
  },
});

// meta-llama/llama-3.1-8b-instruct is OpenRouter's current slug for Llama
// 3.1 8B Instruct. Swap in ":free" for the free-tier variant if preferred.
const MODEL = 'meta-llama/llama-3.1-8b-instruct';

const FINALIZE_TOOL = {
  type: 'function',
  function: {
    name: 'finalize_requirement',
    description:
      'Call this once the conversation has covered enough ground to produce a structured requirement document ready for the approval pipeline. Do not call this until you have asked at least two clarifying questions and have a clear picture of scope, users, and constraints.',
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
          description: 'Which AI models this build should use, if any AI-powered features were discussed. Empty array if none apply.',
          items: {
            type: 'object',
            properties: {
              purpose: { type: 'string', description: "What this model would be used for, e.g. 'code generation' or 'support chat'." },
              model: { type: 'string', description: "Specific recommended model, e.g. 'Claude Opus 5' or 'Claude Haiku 4.5'." },
              rationale: { type: 'string' },
            },
            required: ['purpose', 'model', 'rationale'],
          },
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
        'recommendedSecurityTools',
        'openQuestions',
      ],
    },
  },
};

function buildSystemPrompt(originatorLabel) {
  return `You are the requirement-intake analyst inside Stark Digital's AI Software Factory. You're talking with ${originatorLabel}.

Your job in this conversation:
1. Understand what they want built — the business goal, who uses it, and how.
2. Ask clarifying questions ONE AT A TIME, never a wall of questions. Across the conversation, cover: primary users/roles, functional requirements, whether this is net-new or integrates with an existing system, data sensitivity (PII, financial, or other regulated data), and any deadline or scale expectations.
3. Once you have enough to scope the work — usually after 2 to 4 exchanges — call finalize_requirement. Do not drag the conversation out past what's needed, and never call it before asking at least two clarifying questions.
4. When you finalize, also recommend which AI models this build should use if it has AI-powered features (leave recommendedModels empty if it doesn't), and which security tools apply — draw on static analysis (SAST), secrets scanning, and dependency/SCA scanning, naming concrete tools rather than just categories.
5. Keep conversational replies short: 2-4 sentences, one question.
6. When you are ready to finalize, respond ONLY with a call to the finalize_requirement tool — do not also write text.`;
}

async function runIntakeTurn({ originatorLabel, history }) {
  const messages = [
    { role: 'system', content: buildSystemPrompt(originatorLabel) },
    ...history.map((h) => ({ role: h.role, content: h.content })),
  ];

  const response = await client.chat.completions.create({
    model: MODEL,
    messages,
    tools: [FINALIZE_TOOL],
    max_tokens: 2000,
  });

  const choice = response.choices && response.choices[0];
  if (!choice) {
    throw new Error('The model returned no response.');
  }

  const message = choice.message || {};
  const toolCall = (message.tool_calls || []).find((tc) => tc.function && tc.function.name === 'finalize_requirement');

  if (toolCall) {
    let document;
    try {
      document = JSON.parse(toolCall.function.arguments);
    } catch (err) {
      throw new Error('The model returned a malformed requirement document.');
    }
    return { type: 'finalize', document };
  }

  const text = (message.content || '').trim();
  return { type: 'reply', text: text || 'Could you tell me a bit more about what you need?' };
}

module.exports = { runIntakeTurn };
