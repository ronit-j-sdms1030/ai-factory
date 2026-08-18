const express = require('express');
const Artifact = require('../models/artifact.model');
const { resolveApprovalChain, TIERS } = require('../config/hierarchy.config');
const { transition } = require('../services/stateMachine.service');
const { requireAuth } = require('../middleware/auth.middleware');
const { runIntakeTurn } = require('../services/llm.service');

function originatorLabelFor(actor, tierId) {
  if (actor.isClient) return 'an external client';
  const tier = TIERS[tierId];
  return `the ${tier ? tier.name : tierId} (${tierId.toUpperCase()})`;
}

const OPENING_LINES = {
  client: "Tell me what you're picturing — even rough is fine. I'll help shape it into something buildable.",
  internal: "Describe the capability or system you need. I'll ask a few follow-ups, then structure it into a requirement ready for review.",
};

const router = express.Router();

// Create + immediately submit. This framework doesn't yet implement the
// guided clarifying conversation, so artifacts go straight from draft to
// pending_approval on creation.
router.post('/', requireAuth, async (req, res) => {
  const { title, content, type } = req.body || {};
  if (!title || !content) {
    return res.status(400).json({ error: 'title and content are required' });
  }

  const actor = req.session.user;
  const originatorTierId = actor.isClient ? null : actor.tierId;

  let approvalChain;
  try {
    approvalChain = resolveApprovalChain(actor.isClient ? null : originatorTierId);
  } catch (err) {
    return res.status(400).json({ error: err.message });
  }

  const artifact = new Artifact({
    type: type === 'fsd' ? 'fsd' : 'idea',
    title,
    content,
    originator: { userId: actor.id, tierId: originatorTierId },
    currentStage: 'draft',
    approvalChain,
  });

  try {
    transition(artifact, 'submit', { userId: actor.id, tierId: originatorTierId }, { comment: 'Initial submission' });
  } catch (err) {
    return res.status(400).json({ error: err.message });
  }

  await artifact.save();
  res.status(201).json({ artifact });
});

// Mine, plus (for internal users) anything currently waiting on my tier.
router.get('/', requireAuth, async (req, res) => {
  const actor = req.session.user;
  const query = actor.isClient
    ? { 'originator.userId': actor.id }
    : {
        $or: [
          { 'originator.userId': actor.id },
          { currentStage: 'pending_approval', 'approvalChain.approverTiers': actor.tierId },
        ],
      };

  const artifacts = await Artifact.find(query).sort({ updatedAt: -1 });
  res.json({ artifacts });
});

// Guided intake chat — creates a draft artifact in the 'clarifying' stage and
// seeds it with a canned opener (no API call needed for the first turn).
router.post('/chat/start', requireAuth, async (req, res) => {
  const actor = req.session.user;
  const originatorTierId = actor.isClient ? null : actor.tierId;

  let approvalChain;
  try {
    approvalChain = resolveApprovalChain(originatorTierId);
  } catch (err) {
    return res.status(400).json({ error: err.message });
  }

  const opening = actor.isClient ? OPENING_LINES.client : OPENING_LINES.internal;

  const artifact = new Artifact({
    type: 'idea',
    title: 'Untitled requirement',
    content: {},
    originator: { userId: actor.id, tierId: originatorTierId },
    currentStage: 'clarifying',
    approvalChain,
    chatHistory: [{ role: 'assistant', content: opening }],
  });

  await artifact.save();
  res.status(201).json({ artifactId: artifact._id, reply: opening });
});

// One turn of the guided intake conversation. Either returns a follow-up
// question, or — once the model calls finalize_requirement — structures the
// conversation into the artifact's content and submits it into the pipeline.
router.post('/chat/:id/message', requireAuth, async (req, res) => {
  const { message } = req.body || {};
  if (!message || !message.trim()) {
    return res.status(400).json({ error: 'message is required' });
  }

  const actor = req.session.user;
  const artifact = await Artifact.findById(req.params.id);
  if (!artifact) return res.status(404).json({ error: 'Session not found' });
  if (artifact.originator.userId !== actor.id) {
    return res.status(403).json({ error: 'This is not your intake session' });
  }
  if (artifact.currentStage !== 'clarifying') {
    return res.status(400).json({ error: 'This session is no longer accepting messages' });
  }

  artifact.chatHistory.push({ role: 'user', content: message.trim() });

  let turn;
  try {
    turn = await runIntakeTurn({
      originatorLabel: originatorLabelFor(actor, artifact.originator.tierId),
      history: artifact.chatHistory.map((h) => ({ role: h.role, content: h.content })),
    });
  } catch (err) {
    return res.status(502).json({ error: 'AI intake service error: ' + err.message });
  }

  if (turn.type === 'finalize') {
    const doc = turn.document;
    artifact.title = doc.title || 'Untitled requirement';
    artifact.content = doc;
    artifact.chatHistory.push({ role: 'assistant', content: `Requirement finalized: ${artifact.title}` });

    try {
      transition(
        artifact,
        'submit',
        { userId: actor.id, tierId: artifact.originator.tierId },
        { comment: 'Finalized via guided intake' }
      );
    } catch (err) {
      return res.status(400).json({ error: err.message });
    }

    await artifact.save();
    return res.json({ done: true, artifact });
  }

  artifact.chatHistory.push({ role: 'assistant', content: turn.text });
  await artifact.save();
  res.json({ done: false, reply: turn.text });
});

router.post('/:id/:action', requireAuth, async (req, res) => {
  const { id, action } = req.params;
  if (!['approve', 'reject', 'requestRevision'].includes(action)) {
    return res.status(400).json({ error: 'Unknown action' });
  }

  const actor = req.session.user;
  if (actor.isClient) {
    return res.status(403).json({ error: 'Clients cannot act on approvals' });
  }

  const artifact = await Artifact.findById(id);
  if (!artifact) return res.status(404).json({ error: 'Artifact not found' });

  try {
    transition(artifact, action, { userId: actor.id, tierId: actor.tierId }, { comment: (req.body || {}).comment });
  } catch (err) {
    return res.status(400).json({ error: err.message });
  }

  await artifact.save();
  res.json({ artifact });
});

module.exports = router;
