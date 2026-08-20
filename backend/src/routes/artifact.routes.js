const express = require('express');
const Artifact = require('../models/artifact.model');
const User = require('../models/user.model');
const { resolveApprovalChain, TIERS } = require('../config/hierarchy.config');
const { transition, pushHistory } = require('../services/stateMachine.service');
const { requireAuth } = require('../middleware/auth.middleware');
const { runChatTurn, runFinalize, runDetailedReport, runTeamSplit, runFsdChatEdit } = require('../services/llm.service');

function originatorLabelFor(actor, tierId) {
  if (actor.isClient) return 'an external client';
  const tier = TIERS[tierId];
  return `the ${tier ? tier.name : tierId} (${tierId.toUpperCase()})`;
}

// True once an approval step whose approverTiers includes MD or CEO has been
// satisfied — the trigger point for generating the detailed report. Every
// current originator's gate 0 includes md or ceo (client/VP: md/ceo; PM/TL:
// md/ceo/vp; MD/CEO self-origin: their own tier), so this always fires.
function clearedAnMdCeoGate(step) {
  return !!step && step.approverTiers.some((t) => t === 'md' || t === 'ceo');
}

// Defensive fallback only — every APPROVAL_RULES chain today includes md or
// ceo somewhere, so this shouldn't fire, but guards against a future chain
// that doesn't.
function chainNeverHasMdCeoGate(chain) {
  return !chain.some((step) => step.approverTiers.some((t) => t === 'md' || t === 'ceo'));
}

function isSelfOriginMdCeo(artifact) {
  return artifact.originator.tierId === 'md' || artifact.originator.tierId === 'ceo';
}

// Mirrors the visibility rules in GET / — originator, gate-0 reviewer pool,
// MD/CEO mutual visibility, a TL's own department, or a previous discussion
// invite. Shared by the discussion thread's post/invite actions so access
// can never drift out of sync with what GET / actually shows someone.
function hasDiscussionAccess(artifact, actor) {
  return (
    artifact.originator.userId === actor.id ||
    artifact.approvalChain[0].approverTiers.includes(actor.tierId) ||
    (['md', 'ceo'].includes(actor.tierId) && ['md', 'ceo'].includes(artifact.originator.tierId)) ||
    (artifact.teamReports || []).some((t) => t.team === actor.department) ||
    (artifact.discussionRecipients || []).includes(actor.id)
  );
}

// Best-effort team split, shared by both places the chain can finish
// (a normal 'approve', and giveFinalFsdApproval when the chain is already
// exhausted) — a failure here never undoes the approval that already saved.
async function maybeGenerateTeamSplit(artifact) {
  if (!(artifact.currentStage === 'approved' && artifact.detailedReport && (!artifact.teamReports || artifact.teamReports.length === 0))) {
    return undefined;
  }
  try {
    const split = await runTeamSplit({
      originatorLabel: originatorLabelFor({ isClient: !artifact.originator.tierId }, artifact.originator.tierId),
      detailedReport: artifact.detailedReport,
    });
    artifact.teamReports = split.teamReports;
    artifact.teamReportsGeneratedAt = new Date();
    await artifact.save();
    return undefined;
  } catch (err) {
    return err.message;
  }
}

// Shared by every place an 'approve' actually lands — generates the
// detailed report once a gate clears, routes into the FSD review loop
// unless this is MD/CEO's own self-approved idea, and triggers the team
// split once the chain is genuinely finished. Returns any best-effort
// errors to surface to the caller (never throws — a generation failure
// never undoes the approval that already saved).
async function maybeGenerateDetailedReportAndSplit(artifact, stepBeingActedOn) {
  const readyForReport =
    clearedAnMdCeoGate(stepBeingActedOn) ||
    (artifact.currentStage === 'approved' && chainNeverHasMdCeoGate(artifact.approvalChain));

  let detailedReportError;
  if (!artifact.detailedReport && readyForReport) {
    try {
      artifact.detailedReport = await runDetailedReport({
        originatorLabel: originatorLabelFor({ isClient: !artifact.originator.tierId }, artifact.originator.tierId),
        summary: artifact.content,
        history: artifact.chatHistory.map((h) => ({ role: h.role, content: h.content })),
      });
      artifact.detailedReportGeneratedAt = new Date();

      // Every requirement gets the FSD review loop, including MD/CEO's own.
      // Self-origination used to stay 'approved' here on the reasoning that
      // there was nobody left to loop with — but that also skipped the only
      // stage that exposes "Edit FSD", so a self-approved requirement landed
      // with a finished report and no way to revise it. Routing it through
      // the loop costs the originator a couple of clicks and gives them the
      // same editing pass everyone else gets; the team split still runs at
      // the end of the loop, from giveFinalFsdApproval.
      artifact.currentStage = 'fsd_review';

      await artifact.save();
    } catch (err) {
      detailedReportError = err.message;
    }
  }

  const teamSplitError = await maybeGenerateTeamSplit(artifact);
  return { detailedReportError, teamSplitError };
}

function respondWithArtifact(res, artifact, errors) {
  const nonEmpty = Object.fromEntries(Object.entries(errors || {}).filter(([, v]) => v));
  res.json(Object.keys(nonEmpty).length ? Object.assign({ artifact }, nonEmpty) : { artifact });
}

// After a 'submit' transition lands on pending_approval, MD/CEO's own idea
// is immediately self-approved — they're the only eligible approver anyway,
// so requiring a separate manual click is pure friction. This runs the same
// approve → detailed-report → (skip the loop) → team-split pipeline a real
// approve action would.
async function submitAndMaybeSelfApprove(artifact, actor) {
  await artifact.save();
  if (!isSelfOriginMdCeo(artifact) || artifact.currentStage !== 'pending_approval') {
    return {};
  }
  const stepBeingActedOn = artifact.approvalChain[artifact.currentApprovalIndex];
  transition(artifact, 'approve', { userId: actor.id, tierId: artifact.originator.tierId }, { comment: 'Self-approved on submission' });
  await artifact.save();
  return maybeGenerateDetailedReportAndSplit(artifact, stepBeingActedOn);
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

  const errors = await submitAndMaybeSelfApprove(artifact, actor);
  res.status(201);
  respondWithArtifact(res, artifact, errors);
});

// Mine, plus (for internal users) anything currently waiting on my tier —
// and anything I've previously acted on, so e.g. an MD who approved gate 0
// doesn't lose visibility once the chain moves on to VP. TLs additionally
// see every fully-approved artifact's team split, since that's their work
// queue.
router.get('/', requireAuth, async (req, res) => {
  const actor = req.session.user;
  const query = actor.isClient
    ? { 'originator.userId': actor.id }
    : {
        $or: [
          { 'originator.userId': actor.id },
          { currentStage: 'pending_approval', 'approvalChain.approverTiers': actor.tierId },
          { 'history.actorId': actor.id },
          // fsd_review / fsd_final_approval visibility follows the same
          // gate-0 reviewer pool the backend authorization uses — md/ceo for
          // client & VP submissions, plus vp for PM/TL submissions.
          ...(['md', 'ceo', 'vp'].includes(actor.tierId)
            ? [{ currentStage: { $in: ['fsd_review', 'fsd_final_approval'] }, 'approvalChain.0.approverTiers': actor.tierId }]
            : []),
          // MD and CEO can always see each other's self-approved ideas, even
          // though neither gates the other's anymore.
          ...(actor.tierId === 'md' || actor.tierId === 'ceo' ? [{ 'originator.tierId': { $in: ['md', 'ceo'] } }] : []),
          // TL only sees packages assigned to their own department.
          ...(actor.tierId === 'tl' && actor.department ? [{ 'teamReports.team': actor.department }] : []),
          // Anyone invited into a discussion thread can see that artifact,
          // regardless of role or department.
          { discussionRecipients: actor.id },
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
  const originatorLabel = originatorLabelFor(actor, artifact.originator.tierId);
  const transcript = () => artifact.chatHistory.map((h) => ({ role: h.role, content: h.content }));

  let turn;
  try {
    turn = await runChatTurn({ originatorLabel, history: transcript() });
  } catch (err) {
    return res.status(502).json({ error: 'AI intake service error: ' + err.message });
  }

  if (turn.type === 'ready') {
    let doc;
    try {
      doc = await runFinalize({ originatorLabel, history: transcript() });
    } catch (err) {
      return res.status(502).json({ error: 'AI report generation error: ' + err.message });
    }

    // Structure the conversation into content, but stay in 'clarifying' —
    // the originator reviews this summary and explicitly sends it via the
    // separate /:id/submit action below, rather than auto-submitting here.
    artifact.title = doc.title || 'Untitled requirement';
    artifact.content = doc;
    artifact.chatHistory.push({
      role: 'assistant',
      content: "Here's a summary of what I've captured. Review it below and send it when you're ready.",
    });
    await artifact.save();
    return res.json({ reviewReady: true, artifact });
  }

  artifact.chatHistory.push({ role: 'assistant', content: turn.text });
  await artifact.save();
  res.json({ done: false, reply: turn.text });
});

// Chat-based FSD editing — MD/CEO (during fsd_review) or the client (during
// fsd_pending_client) describe a change in plain language and the AI
// applies it. Registered ahead of the generic /:id/:action route since both
// patterns would otherwise match the same URL.
router.post('/:id/fsdChat', requireAuth, async (req, res) => {
  const { message } = req.body || {};
  if (!message || !message.trim()) {
    return res.status(400).json({ error: 'message is required' });
  }

  const actor = req.session.user;
  const artifact = await Artifact.findById(req.params.id);
  if (!artifact) return res.status(404).json({ error: 'Artifact not found' });

  let actorRole;
  if (artifact.originator.userId === actor.id && artifact.currentStage === 'fsd_pending_client') {
    actorRole = actor.isClient ? 'the client' : 'the originator';
  } else if (
    !actor.isClient &&
    artifact.currentStage === 'fsd_review' &&
    artifact.approvalChain[0].approverTiers.includes(actor.tierId)
  ) {
    const tier = TIERS[actor.tierId];
    actorRole = `the ${tier.name} (${actor.tierId.toUpperCase()})`;
  } else {
    return res.status(403).json({ error: 'The detailed report is not open for your edits right now' });
  }

  artifact.fsdChatHistory = artifact.fsdChatHistory || [];
  artifact.fsdChatHistory.push({ role: 'user', content: message.trim() });

  let result;
  try {
    result = await runFsdChatEdit({
      originatorLabel: originatorLabelFor({ isClient: !artifact.originator.tierId }, artifact.originator.tierId),
      actorRole,
      detailedReport: artifact.detailedReport,
      history: artifact.fsdChatHistory.map((h) => ({ role: h.role, content: h.content })),
    });
  } catch (err) {
    return res.status(502).json({ error: 'AI edit service error: ' + err.message });
  }

  const { changeSummary, ...updatedReport } = result;
  artifact.detailedReport = updatedReport;
  artifact.fsdChatHistory.push({ role: 'assistant', content: changeSummary });

  await artifact.save();
  res.json({ reply: changeSummary, artifact });
});

// Any internal role who already has visibility on this report can share it
// with any other internal colleague for discussion, even if that colleague
// wouldn't otherwise have access (e.g. a different department's TL, or a
// PM who was never in the reviewer pool).
router.post('/:id/shareForDiscussion', requireAuth, async (req, res) => {
  const actor = req.session.user;
  if (actor.isClient) {
    return res.status(403).json({ error: 'Only internal roles can share a report for discussion' });
  }

  const { toUserId, note } = req.body || {};
  if (!toUserId) {
    return res.status(400).json({ error: 'toUserId is required' });
  }

  const artifact = await Artifact.findById(req.params.id);
  if (!artifact) return res.status(404).json({ error: 'Artifact not found' });

  if (!hasDiscussionAccess(artifact, actor)) {
    return res.status(403).json({ error: 'You do not have access to this report' });
  }

  const toUser = await User.findById(toUserId);
  if (!toUser || toUser.isClient) {
    return res.status(400).json({ error: 'Recipient must be an internal colleague' });
  }
  if (toUser._id.toString() === actor.id) {
    return res.status(400).json({ error: 'Cannot share a report with yourself' });
  }

  const alreadyIn = (artifact.discussionRecipients || []).includes(toUserId);
  artifact.discussionRecipients = artifact.discussionRecipients || [];
  if (!alreadyIn) {
    artifact.discussionRecipients.push(toUserId);
  }
  artifact.discussionShares = artifact.discussionShares || [];
  artifact.discussionShares.push({
    fromUserId: actor.id,
    fromName: actor.name,
    toUserId,
    toName: toUser.name,
    note: (note || '').trim(),
    timestamp: new Date(),
  });

  await artifact.save();
  res.json({ artifact });
});

router.post('/:id/:action', requireAuth, async (req, res) => {
  const { id, action } = req.params;
  const KNOWN_ACTIONS = [
    'approve',
    'reject',
    'requestRevision',
    'resubmit',
    'submit',
    'proposeChanges',
    'acceptChanges',
    'editFsd',
    'sendFsdToClient',
    'approveFsd',
    'giveFinalFsdApproval',
    'regenerateFsd',
  ];
  if (!KNOWN_ACTIONS.includes(action)) {
    return res.status(400).json({ error: 'Unknown action' });
  }

  const actor = req.session.user;
  const artifact = await Artifact.findById(id);
  if (!artifact) return res.status(404).json({ error: 'Artifact not found' });

  // Submit: the originator reviews the summary the guided intake chat
  // produced — optionally editing it first — and explicitly sends it into
  // the approval pipeline.
  if (action === 'submit') {
    if (artifact.originator.userId !== actor.id) {
      return res.status(403).json({ error: 'Only the originator can send this requirement' });
    }
    if (artifact.currentStage !== 'clarifying') {
      return res.status(400).json({ error: 'This requirement is not ready to send' });
    }

    const { title: editedTitle, content: editedContent } = req.body || {};
    if (editedTitle) artifact.title = editedTitle;
    if (editedContent !== undefined) artifact.content = editedContent;

    try {
      transition(
        artifact,
        'submit',
        { userId: actor.id, tierId: artifact.originator.tierId },
        { comment: 'Reviewed and sent via guided intake' }
      );
    } catch (err) {
      return res.status(400).json({ error: err.message });
    }

    const errors = await submitAndMaybeSelfApprove(artifact, actor);
    return respondWithArtifact(res, artifact, errors);
  }

  // Resubmit: the originator edits a requirement that was sent back for
  // revision, then re-enters the approval chain at the top.
  if (action === 'resubmit') {
    if (artifact.originator.userId !== actor.id) {
      return res.status(403).json({ error: 'Only the originator can edit this submission' });
    }
    if (artifact.currentStage !== 'revision_requested') {
      return res.status(400).json({ error: 'Only submissions with requested revisions can be edited' });
    }

    const { title, content } = req.body || {};
    if (title) artifact.title = title;
    if (content !== undefined) artifact.content = content;

    try {
      transition(
        artifact,
        'submit',
        { userId: actor.id, tierId: artifact.originator.tierId },
        { comment: 'Edited and resubmitted' }
      );
    } catch (err) {
      return res.status(400).json({ error: err.message });
    }

    const errors = await submitAndMaybeSelfApprove(artifact, actor);
    return respondWithArtifact(res, artifact, errors);
  }

  // Accept changes: the originator confirms the reviewer's proposed edit,
  // sending it back to the same gate for formal signoff.
  if (action === 'acceptChanges') {
    try {
      transition(artifact, 'acceptChanges', { userId: actor.id, tierId: actor.isClient ? null : actor.tierId }, { comment: 'Originator accepted proposed changes' });
    } catch (err) {
      return res.status(400).json({ error: err.message });
    }
    await artifact.save();
    return res.json({ artifact });
  }

  // Approve FSD: the originator signs off on the detailed report.
  if (action === 'approveFsd') {
    try {
      transition(artifact, 'approveFsd', { userId: actor.id, tierId: actor.isClient ? null : actor.tierId }, { comment: 'Originator approved the detailed report' });
    } catch (err) {
      return res.status(400).json({ error: err.message });
    }
    await artifact.save();
    return res.json({ artifact });
  }

  // Regenerate FSD: recovery path for when generation failed after approval
  // and there's no remaining approve/final-approve step left to naturally
  // retry it — MD/CEO self-origination auto-approves in one step, so a
  // failure there has no other way to retry.
  if (action === 'regenerateFsd') {
    if (artifact.detailedReport) {
      return res.status(400).json({ error: 'A detailed report already exists for this requirement' });
    }
    if (!['approved', 'fsd_review'].includes(artifact.currentStage)) {
      return res.status(400).json({ error: 'This requirement is not in a state where a detailed report applies' });
    }
    const isOriginator = artifact.originator.userId === actor.id;
    const isReviewer = !actor.isClient && artifact.approvalChain[0].approverTiers.includes(actor.tierId);
    if (!isOriginator && !isReviewer) {
      return res.status(403).json({ error: 'You are not authorized to regenerate this report' });
    }

    try {
      artifact.detailedReport = await runDetailedReport({
        originatorLabel: originatorLabelFor({ isClient: !artifact.originator.tierId }, artifact.originator.tierId),
        summary: artifact.content,
        history: artifact.chatHistory.map((h) => ({ role: h.role, content: h.content })),
      });
      artifact.detailedReportGeneratedAt = new Date();
      await artifact.save();
    } catch (err) {
      return res.status(502).json({ error: err.message });
    }

    const teamSplitError = await maybeGenerateTeamSplit(artifact);
    return respondWithArtifact(res, artifact, { teamSplitError });
  }

  if (actor.isClient) {
    return res.status(403).json({ error: 'Clients cannot act on approvals' });
  }

  // VP sending a client submission back doesn't go through the generic
  // client-edits-and-resubmits loop — by this point the FSD (not just the
  // original summary) is what's under review, so it goes back to MD/CEO to
  // revise the FSD instead.
  if (
    action === 'requestRevision' &&
    artifact.currentStage === 'pending_approval' &&
    artifact.currentApprovalIndex > 0 &&
    artifact.detailedReport
  ) {
    const step = artifact.approvalChain[artifact.currentApprovalIndex];
    if (!step || !step.approverTiers.includes(actor.tierId)) {
      return res.status(400).json({
        error: `Unauthorized approver: "${actor.tierId}" cannot act on this step (expected one of: ${(step && step.approverTiers.join(', ')) || 'none'}).`,
      });
    }
    artifact.currentStage = 'fsd_review';
    pushHistory(
      artifact,
      { userId: actor.id, tierId: actor.tierId },
      'requestRevision',
      (req.body || {}).comment || 'Sent back to MD/CEO for FSD revision',
      'fsd_review'
    );
    await artifact.save();
    return res.json({ artifact });
  }

  // Propose changes: the gate-0 approver (MD/CEO) edits the summary
  // themselves — rather than asking the client to redo it — and sends
  // their version to the client to confirm.
  if (action === 'proposeChanges') {
    const { title, content } = req.body || {};
    if (title) artifact.title = title;
    if (content !== undefined) artifact.content = content;

    try {
      transition(artifact, 'proposeChanges', { userId: actor.id, tierId: actor.tierId }, { comment: (req.body || {}).comment || 'Proposed changes' });
    } catch (err) {
      return res.status(400).json({ error: err.message });
    }

    await artifact.save();
    return res.json({ artifact });
  }

  // Edit FSD: MD/CEO tweaks the generated detailed report before sending it
  // to the client. Doesn't change stage — stays in fsd_review until they
  // explicitly send it on.
  if (action === 'editFsd') {
    if (artifact.currentStage !== 'fsd_review') {
      return res.status(400).json({ error: 'The detailed report is not open for editing' });
    }
    if (!artifact.approvalChain[0].approverTiers.includes(actor.tierId)) {
      return res.status(403).json({ error: 'Only this requirement\'s reviewers can edit the detailed report' });
    }
    const { detailedReport } = req.body || {};
    if (!detailedReport || typeof detailedReport !== 'object') {
      return res.status(400).json({ error: 'detailedReport is required' });
    }
    artifact.detailedReport = Object.assign({}, artifact.detailedReport, detailedReport);
    pushHistory(artifact, { userId: actor.id, tierId: actor.tierId }, 'editFsd', 'Edited the detailed report', artifact.currentStage);
    await artifact.save();
    return res.json({ artifact });
  }

  // Send FSD to originator: the reviewer pool is done reviewing/editing,
  // hands it to whoever originated the requirement for their approval.
  if (action === 'sendFsdToClient') {
    const { comment: sendComment, finalApproverTier } = req.body || {};
    try {
      transition(
        artifact,
        'sendFsdToClient',
        { userId: actor.id, tierId: actor.tierId },
        {
          comment: finalApproverTier
            ? `Routed to ${finalApproverTier.toUpperCase()} for final approval`
            : sendComment || 'Sent to originator for approval',
          finalApproverTier,
        }
      );
    } catch (err) {
      return res.status(400).json({ error: err.message });
    }
    await artifact.save();
    return res.json({ artifact });
  }

  // Give final FSD approval: the reviewer pool's last signoff. For client
  // submissions this still leaves VP's real second gate ahead; for every
  // other originator the chain is already exhausted, so this finishes the
  // whole thing — trigger the team split right here rather than waiting for
  // a VP approval that will never come.
  if (action === 'giveFinalFsdApproval') {
    try {
      transition(artifact, 'giveFinalFsdApproval', { userId: actor.id, tierId: actor.tierId }, { comment: (req.body || {}).comment || 'Final FSD approval' });
    } catch (err) {
      return res.status(400).json({ error: err.message });
    }
    await artifact.save();

    const teamSplitError = await maybeGenerateTeamSplit(artifact);
    return res.json(teamSplitError ? { artifact, teamSplitError } : { artifact });
  }

  const stepBeingActedOn = action === 'approve' ? artifact.approvalChain[artifact.currentApprovalIndex] : null;

  try {
    transition(artifact, action, { userId: actor.id, tierId: actor.tierId }, { comment: (req.body || {}).comment });
  } catch (err) {
    return res.status(400).json({ error: err.message });
  }

  await artifact.save();

  // Best-effort: generate the detailed report once a gate has cleared (or,
  // for chains without one, on full approval), without letting a
  // generation failure undo the approval that already saved above.
  const errors = action === 'approve' ? await maybeGenerateDetailedReportAndSplit(artifact, stepBeingActedOn) : {};
  respondWithArtifact(res, artifact, errors);
});

module.exports = router;
