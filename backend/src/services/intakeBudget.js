// Pure decision logic for how long a guided-intake conversation may run.
// Split out from llm.service so it can be tested directly: llm.service
// pulls in @babel/core (ESM-only) and constructs live API clients at load,
// neither of which a unit test can or should do. Nothing here needs a
// model, so nothing here needs mocking.

// Floor. Paired with the "do not finalize while a checklist item is blank"
// rule in the intake prompt, this is what stops the model settling for a
// thin requirement after three shallow exchanges.
const MIN_CLARIFYING_QUESTIONS = 4;

// Ceiling, enforced in code rather than by prompt instruction. An intake
// that overruns is worse at its job — a requester answering "k" for the
// thirtieth time has stopped supplying information — and it carries a real
// bill: every turn re-sends the entire transcript, so cost grows with the
// SQUARE of the conversation length. A real 41-turn run billed ~95k input
// tokens against ~12.7k for the same intake held to ten. The prompt's
// coverage checklist is five items wide, so ten questions is roughly two
// per item: enough to map scope properly, not enough to wander.
const MAX_CLARIFYING_QUESTIONS = 10;

// The floor is a quality guard, not a trap — an explicit request to stop
// always wins over it, so nobody gets held in an interrogation they've
// asked to end.
const FINALIZE_INTENT = /\b(finali[sz]e|wrap (it )?up|that'?s (all|it)|i'?m done|we'?re done|submit it|go ahead|just proceed|enough (questions|detail|info)|no more questions|stop asking)\b/i;

// history[0] is the hardcoded opener the route seeds the session with, not
// something the model generated, so it does not count toward either bound.
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

// True once the conversation has spent its whole question budget and must
// finalize with whatever it has gathered.
function intakeBudgetSpent(history) {
  return countQuestionsAsked(history) >= MAX_CLARIFYING_QUESTIONS;
}

module.exports = {
  MIN_CLARIFYING_QUESTIONS,
  MAX_CLARIFYING_QUESTIONS,
  countQuestionsAsked,
  userAskedToWrapUp,
  intakeBudgetSpent,
};
