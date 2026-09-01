const {
  MIN_CLARIFYING_QUESTIONS,
  MAX_CLARIFYING_QUESTIONS,
  countQuestionsAsked,
  userAskedToWrapUp,
  intakeBudgetSpent,
} = require('../src/services/intakeBudget');

// history[0] is the hardcoded opener the route seeds the session with.
function buildHistory(questionsAsked, lastUserMessage) {
  const history = [{ role: 'assistant', content: 'Describe the capability you need.' }];
  for (let i = 0; i < questionsAsked; i++) {
    history.push({ role: 'user', content: 'an answer' });
    history.push({ role: 'assistant', content: 'a clarifying question' });
  }
  if (lastUserMessage) history.push({ role: 'user', content: lastUserMessage });
  return history;
}

describe('guided intake budget', () => {
  test('does not count the seeded opener as a question the model asked', () => {
    expect(countQuestionsAsked(buildHistory(0))).toBe(0);
    expect(countQuestionsAsked(buildHistory(3))).toBe(3);
  });

  test('leaves a conversation inside its budget running', () => {
    expect(intakeBudgetSpent(buildHistory(MAX_CLARIFYING_QUESTIONS - 1))).toBe(false);
  });

  // An unbounded intake is a cost bug, not just a UX one: every turn
  // re-sends the whole transcript, so a runaway conversation bills
  // quadratically. Both the boundary and well past it must terminate.
  test('stops the conversation once the question budget is spent', () => {
    expect(intakeBudgetSpent(buildHistory(MAX_CLARIFYING_QUESTIONS))).toBe(true);
    expect(intakeBudgetSpent(buildHistory(41))).toBe(true);
  });

  test('leaves room to cover the five-item checklist', () => {
    expect(MAX_CLARIFYING_QUESTIONS).toBeGreaterThanOrEqual(MIN_CLARIFYING_QUESTIONS);
    expect(MAX_CLARIFYING_QUESTIONS).toBeGreaterThanOrEqual(5);
  });

  describe('explicit requests to stop', () => {
    test('are honoured so nobody is held in an interrogation', () => {
      expect(userAskedToWrapUp(buildHistory(2, 'just proceed'))).toBe(true);
      expect(userAskedToWrapUp(buildHistory(2, "that's all"))).toBe(true);
      expect(userAskedToWrapUp(buildHistory(2, 'no more questions'))).toBe(true);
    });

    test('are judged on the latest message only, not the whole transcript', () => {
      const history = buildHistory(2, 'go ahead');
      history.push({ role: 'assistant', content: 'a clarifying question' });
      history.push({ role: 'user', content: 'we have about 200 staff' });
      expect(userAskedToWrapUp(history)).toBe(false);
    });

    test('are not triggered by an ordinary answer', () => {
      expect(userAskedToWrapUp(buildHistory(2, 'mostly warehouse staff'))).toBe(false);
    });
  });
});
