const express = require('express');
const Artifact = require('../models/artifact.model');
const User = require('../models/user.model');
const { resolveApprovalChain, TIERS } = require('../config/hierarchy.config');
const { transition, pushHistory } = require('../services/stateMachine.service');
const { requireAuth } = require('../middleware/auth.middleware');
const { runChatTurn, runFinalize, runDetailedReport, runTeamSplit, runFsdChatEdit, runTeamReportChatEdit } = require('../services/llm.service');

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

// Department task breakdowns are Team Lead material — the requester and
// every approver above them (MD/CEO/VP) already had their say at the FSD
// stage; the split is what a TL executes against, not something the
// originator or reviewer chain needs to read. This used to be a frontend-only
// filter (getVisibleTeamReports in app.html) — meaning the full array was
// sent to every viewer over the wire regardless of role, and only the UI
// chose not to render it. Anyone who opened the network tab, or called the
// API directly, could already read every department's internal package.
// Redacting here closes that: the data itself never leaves the server for
// anyone who isn't the right TL.
function visibleTeamReportsFor(artifact, actor) {
  if (!actor || actor.isClient || actor.tierId !== 'tl' || !actor.department) return [];
  const explicitlySharedTeams = (artifact.discussionShares || [])
    .filter((share) => share.toUserId === actor.id && share.sharedTeam)
    .map((share) => share.sharedTeam);
  return (artifact.teamReports || []).filter(
    (report) => report.team === actor.department || explicitlySharedTeams.includes(report.team)
  );
}

function redactArtifactForActor(artifact, actor) {
  const obj = typeof artifact.toObject === 'function' ? artifact.toObject() : artifact;
  const canJoinGroupDiscussion =
    actor && !actor.isClient && (
      (actor.tierId === 'tl' && actor.department && (artifact.teamReports || []).some((report) => report.team === actor.department)) ||
      (['md', 'ceo', 'vp'].includes(actor.tierId) && (artifact.discussionMessages || []).length > 0)
    );
  return Object.assign({}, obj, {
    teamReports: visibleTeamReportsFor(artifact, actor),
    teamReportEditHistory:
      actor && actor.tierId === 'tl' && actor.department
        ? (obj.teamReportEditHistory || []).filter((entry) => entry.department === actor.department)
        : [],
    discussionMessages: canJoinGroupDiscussion ? (obj.discussionMessages || []) : [],
  });
}

function respondWithArtifact(res, artifact, actor, errors) {
  const nonEmpty = Object.fromEntries(Object.entries(errors || {}).filter(([, v]) => v));
  const payload = redactArtifactForActor(artifact, actor);
  res.json(Object.keys(nonEmpty).length ? Object.assign({ artifact: payload }, nonEmpty) : { artifact: payload });
}

// Applies the AI editor's small dot-path replacements without permitting
// prototype keys. The document is cloned first so a rejected operation can
// never leave a partially-mutated Mongoose value behind.
function applyEditOperation(root, path, value) {
  const parts = path === '' ? [] : path.split('.');
  const unsafe = new Set(['__proto__', 'prototype', 'constructor']);
  if (parts.some((part) => !part || unsafe.has(part))) throw new Error(`Invalid edit path: "${path}"`);
  if (parts.length === 0) return value;

  const clone = root == null ? {} : JSON.parse(JSON.stringify(root));
  let cursor = clone;
  for (let i = 0; i < parts.length - 1; i++) {
    const key = Array.isArray(cursor) ? Number(parts[i]) : parts[i];
    if ((Array.isArray(cursor) && !Number.isInteger(key)) || cursor[key] == null || typeof cursor[key] !== 'object') {
      throw new Error(`Edit path does not exist: "${path}"`);
    }
    cursor = cursor[key];
  }
  const last = Array.isArray(cursor) ? Number(parts[parts.length - 1]) : parts[parts.length - 1];
  if (Array.isArray(cursor) && !Number.isInteger(last)) throw new Error(`Invalid array index in edit path: "${path}"`);
  cursor[last] = value;
  return clone;
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
  respondWithArtifact(res, artifact, actor, errors);
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
          ...(actor.tierId === 'vp' ? [{ currentStage: 'team_revision_requested' }] : []),
          // VP oversees code-gen/production for every approved requirement,
          // not just the ones where VP happened to be the approving gate —
          // an MD or CEO can self-approve and route straight to their peer,
          // never touching VP's queue at all, yet VP still needs to see its
          // code-gen status. Without this, an artifact VP never gated on
          // would be invisible to them everywhere, including there.
          ...(actor.tierId === 'vp' ? [{ currentStage: 'approved', teamReportsGeneratedAt: { $exists: true, $ne: null } }] : []),
          ...(['md', 'ceo', 'vp'].includes(actor.tierId) ? [{ 'discussionMessages.0': { $exists: true } }] : []),
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
  res.json({ artifacts: artifacts.map((a) => redactArtifactForActor(a, actor)) });
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
    return res.json({ reviewReady: true, artifact: redactArtifactForActor(artifact, actor) });
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
  } else if (!actor.isClient && actor.tierId === 'vp' && artifact.currentStage === 'team_revision_requested') {
    actorRole = 'the Vice President (VP), revising the FSD after Team Lead feedback';
  } else if (
    !actor.isClient &&
    actor.tierId === 'vp' &&
    artifact.currentStage === 'pending_approval' &&
    artifact.currentApprovalIndex > 0 &&
    ['md', 'ceo', 'tl'].includes(artifact.originator.tierId) &&
    artifact.detailedReport
  ) {
    actorRole = 'the Vice President (VP), editing the FSD before sending it to Team Leads';
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
      title: artifact.title,
      requirement: artifact.content,
      detailedReport: artifact.detailedReport,
      history: artifact.fsdChatHistory.map((h) => ({ role: h.role, content: h.content })),
    });
  } catch (err) {
    return res.status(502).json({ error: 'AI edit service error: ' + err.message });
  }

  const { changeSummary, operations } = result;
  if (!Array.isArray(operations) || operations.length === 0) {
    return res.status(502).json({ error: 'AI edit service returned no report changes' });
  }
  const normalizedOperations = operations.map((rawOperation) => {
    const operation = Object.assign({}, rawOperation);
    if (typeof operation.target === 'string' && operation.target.includes('.')) {
      const dot = operation.target.indexOf('.');
      const targetPrefix = operation.target.slice(0, dot);
      if (['requirement', 'detailedReport'].includes(targetPrefix)) {
        const embeddedPath = operation.target.slice(dot + 1);
        operation.target = targetPrefix;
        operation.path = operation.path ? embeddedPath + '.' + operation.path : embeddedPath;
      }
    }
    return operation;
  });
  const reportPaths = normalizedOperations
    .filter((operation) => operation.target === 'detailedReport')
    .map((operation) => operation.path);
  const changesArchitecture = reportPaths.some((path) =>
    ['architecture', 'techStack', 'userFlow'].some((section) => path === section || path.startsWith(section + '.'))
  );
  const changesDataModel = reportPaths.some((path) => path === 'dataModel' || path.startsWith('dataModel.'));
  if (changesArchitecture && !reportPaths.includes('architectureDiagram')) {
    return res.status(502).json({ error: 'AI edit was incomplete: architecture changes must also update the architecture diagram. Please send the edit again.' });
  }
  if (changesDataModel && !reportPaths.includes('dbSchemaDiagram')) {
    return res.status(502).json({ error: 'AI edit was incomplete: data-model changes must also update the database schema diagram. Please send the edit again.' });
  }
  try {
    let nextTitle = artifact.title;
    let nextRequirement = artifact.content;
    let nextDetailedReport = artifact.detailedReport;
    for (const operation of normalizedOperations) {
      if (operation.target === 'title') {
        if (operation.path !== '' || typeof operation.value !== 'string' || !operation.value.trim()) {
          throw new Error('A title edit requires a non-empty string and an empty path');
        }
        nextTitle = operation.value.trim();
      } else if (operation.target === 'requirement') {
        nextRequirement = applyEditOperation(nextRequirement, operation.path, operation.value);
      } else if (operation.target === 'detailedReport') {
        nextDetailedReport = applyEditOperation(nextDetailedReport, operation.path, operation.value);
      } else {
        throw new Error(`Unknown edit target: "${operation.target}"`);
      }
    }
    artifact.title = nextTitle;
    artifact.content = nextRequirement;
    artifact.detailedReport = nextDetailedReport;
  } catch (err) {
    return res.status(502).json({ error: 'AI edit service produced an invalid change: ' + err.message });
  }
  artifact.fsdChatHistory.push({ role: 'assistant', content: changeSummary });

  await artifact.save();
  res.json({ reply: changeSummary, artifact: redactArtifactForActor(artifact, actor) });
});

router.post('/:id/teamReportChat', requireAuth, async (req, res) => {
  const actor = req.session.user;
  const message = ((req.body || {}).message || '').trim();
  if (!message) return res.status(400).json({ error: 'message is required' });
  if (actor.isClient || actor.tierId !== 'tl' || !actor.department) {
    return res.status(403).json({ error: 'Only an assigned Team Lead can edit a team package' });
  }

  const artifact = await Artifact.findById(req.params.id);
  if (!artifact) return res.status(404).json({ error: 'Artifact not found' });
  if (artifact.currentStage !== 'approved') {
    return res.status(400).json({ error: 'This package is not currently open for Team Lead edits' });
  }
  const reportIndex = (artifact.teamReports || []).findIndex((report) => report.team === actor.department);
  if (reportIndex < 0) return res.status(403).json({ error: 'No package is assigned to your department' });

  let result;
  try {
    result = await runTeamReportChatEdit({
      department: actor.department,
      report: artifact.teamReports[reportIndex],
      message,
    });
  } catch (err) {
    return res.status(502).json({ error: 'AI package edit service error: ' + err.message });
  }
  const requiredPackageFields = ['team', 'objective', 'architecture', 'techStack', 'plan'];
  if (
    !result.updatedReport ||
    result.updatedReport.team !== actor.department ||
    requiredPackageFields.some((field) => !(field in result.updatedReport))
  ) {
    return res.status(502).json({ error: 'AI package edit attempted to change the assigned department' });
  }

  artifact.teamReports[reportIndex] = result.updatedReport;
  artifact.markModified('teamReports');
  artifact.teamReportEditHistory = artifact.teamReportEditHistory || [];
  artifact.teamReportEditHistory.push(
    { department: actor.department, role: 'user', content: message, timestamp: new Date() },
    { department: actor.department, role: 'assistant', content: result.changeSummary, timestamp: new Date() }
  );
  await artifact.save();
  res.json({ reply: result.changeSummary, artifact: redactArtifactForActor(artifact, actor) });
});

// This exists specifically to discuss team-report content, which is now
// Team Lead-only material (see visibleTeamReportsFor) — so both ends of the
// share have to be a TL, not just "any internal colleague," or this would
// hand non-TL roles a side channel into content they're no longer sent by
// GET / or any other route.
router.post('/:id/shareForDiscussion', requireAuth, async (req, res) => {
  const actor = req.session.user;
  if (actor.isClient || actor.tierId !== 'tl') {
    return res.status(403).json({ error: 'Only Team Leads can share a team report for discussion' });
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
  if (!toUser || toUser.isClient || toUser.tierId !== 'tl') {
    return res.status(400).json({ error: 'Recipient must be a Team Lead' });
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
    sharedTeam: actor.department,
    note: (note || '').trim(),
    timestamp: new Date(),
  });

  await artifact.save();
  res.json({ artifact: redactArtifactForActor(artifact, actor) });
});

// Requirement-level group discussion for every TL assigned a generated
// package on this artifact. Discussion does not change workflow state;
// governed changes still use Request revision -> VP.
router.post('/:id/discussionMessage', requireAuth, async (req, res) => {
  const actor = req.session.user;
  const message = ((req.body || {}).message || '').trim();
  if (!message) return res.status(400).json({ error: 'message is required' });
  if (actor.isClient || !['tl', 'vp', 'ceo', 'md'].includes(actor.tierId)) {
    return res.status(403).json({ error: 'Only assigned Team Leads and leadership can join requirement discussions' });
  }

  const artifact = await Artifact.findById(req.params.id);
  if (!artifact) return res.status(404).json({ error: 'Artifact not found' });
  const isAssignedTl = actor.tierId === 'tl' && actor.department &&
    (artifact.teamReports || []).some((report) => report.team === actor.department);
  const isLeadershipJoiningOpenedDiscussion = ['md', 'ceo', 'vp'].includes(actor.tierId) &&
    (artifact.discussionMessages || []).length > 0;
  if (!isAssignedTl && !isLeadershipJoiningOpenedDiscussion) {
    return res.status(403).json({ error: 'An assigned Team Lead must open this discussion first' });
  }

  artifact.discussionMessages = artifact.discussionMessages || [];
  artifact.discussionMessages.push({
    userId: actor.id,
    name: actor.name,
    department: actor.department || actor.tierId.toUpperCase(),
    message,
    timestamp: new Date(),
  });
  await artifact.save();
  res.json({ artifact: redactArtifactForActor(artifact, actor) });
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
    'regenerateTeamSplit',
    'requestTeamRevision',
  ];
  if (!KNOWN_ACTIONS.includes(action)) {
    return res.status(400).json({ error: 'Unknown action' });
  }

  const actor = req.session.user;
  const artifact = await Artifact.findById(id);
  if (!artifact) return res.status(404).json({ error: 'Artifact not found' });

  if (action === 'requestTeamRevision') {
    const comment = ((req.body || {}).comment || '').trim();
    if (actor.isClient || actor.tierId !== 'tl' || !actor.department) {
      return res.status(403).json({ error: 'Only an assigned Team Lead can request a production-package revision' });
    }
    if (artifact.currentStage !== 'approved' || !(artifact.teamReports || []).some((report) => report.team === actor.department)) {
      return res.status(400).json({ error: 'No approved team package is assigned to your department' });
    }
    if (!comment) return res.status(400).json({ error: 'Please describe the revision your team needs' });

    artifact.teamRevisionRequests = artifact.teamRevisionRequests || [];
    artifact.teamRevisionRequests.push({
      department: actor.department,
      requestedBy: actor.id,
      requestedByName: actor.name,
      comment,
      status: 'open',
      timestamp: new Date(),
    });
    artifact.currentStage = 'team_revision_requested';
    pushHistory(
      artifact,
      { userId: actor.id, tierId: actor.tierId },
      'requestTeamRevision',
      `${actor.department}: ${comment}`,
      artifact.currentStage
    );
    await artifact.save();
    return res.json({ artifact: redactArtifactForActor(artifact, actor) });
  }

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
    return respondWithArtifact(res, artifact, actor, errors);
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
    return respondWithArtifact(res, artifact, actor, errors);
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
    return res.json({ artifact: redactArtifactForActor(artifact, actor) });
  }

  // Approve FSD: the originator signs off on the detailed report.
  if (action === 'approveFsd') {
    try {
      transition(artifact, 'approveFsd', { userId: actor.id, tierId: actor.isClient ? null : actor.tierId }, { comment: 'Originator approved the detailed report' });
    } catch (err) {
      return res.status(400).json({ error: err.message });
    }
    await artifact.save();
    const teamSplitError = await maybeGenerateTeamSplit(artifact);
    return respondWithArtifact(res, artifact, actor, { teamSplitError });
  }

  // Regenerate FSD: recovery path for when generation failed after approval
  // and there's no remaining approve/final-approve step left to naturally
  // retry it — MD/CEO self-origination auto-approves in one step, so a
  // failure there has no other way to retry.
  if (action === 'regenerateFsd') {
    if (artifact.detailedReport) {
      return res.status(400).json({ error: 'A detailed report already exists for this requirement' });
    }
    // Gate 0 clearing is what makes a report apply — checking the approval
    // index rather than a fixed stage list means this also covers a
    // self-origin MD/CEO artifact stuck at pending_approval (gate 0 cleared,
    // waiting on VP) after a slow/failed generation attempt, not just
    // 'approved'/'fsd_review'.
    if (artifact.currentApprovalIndex < 1) {
      return res.status(400).json({ error: 'This requirement has not cleared its first approval gate yet' });
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
    return respondWithArtifact(res, artifact, actor, { teamSplitError });
  }

  // Regenerate team split: lets anyone with a stake in this requirement
  // re-run the department split — e.g. to pick up a schema/prompt
  // improvement (like the phased-plan format) on a requirement that was
  // split before that change shipped. Overwrites the existing packages.
  if (action === 'regenerateTeamSplit') {
    if (!['approved', 'team_revision_requested'].includes(artifact.currentStage) || !artifact.detailedReport) {
      return res.status(400).json({ error: 'Team packages can only be generated once this requirement is fully approved with a detailed report' });
    }
    const isOriginator = artifact.originator.userId === actor.id;
    const isReviewer = !actor.isClient && artifact.approvalChain[0].approverTiers.includes(actor.tierId);
    const isProductionVp = !actor.isClient && actor.tierId === 'vp';
    const isInvolvedTl = !actor.isClient && actor.tierId === 'tl' && actor.department &&
      (artifact.teamReports || []).some((t) => t.team === actor.department);
    if (!isOriginator && !isReviewer && !isProductionVp && !isInvolvedTl) {
      return res.status(403).json({ error: 'You are not authorized to regenerate this team split' });
    }

    try {
      const split = await runTeamSplit({
        originatorLabel: originatorLabelFor({ isClient: !artifact.originator.tierId }, artifact.originator.tierId),
        detailedReport: artifact.detailedReport,
      });
      artifact.teamReports = split.teamReports;
      artifact.teamReportsGeneratedAt = new Date();
      artifact.currentStage = 'approved';
      (artifact.teamRevisionRequests || []).forEach((request) => {
        if (request.status === 'open') {
          request.status = 'resolved';
          request.resolvedAt = new Date();
        }
      });
      pushHistory(
        artifact,
        { userId: actor.id, tierId: actor.tierId },
        'regenerateTeamSplit',
        'Generated and sent updated work packages to Team Leads',
        artifact.currentStage
      );
      await artifact.save();
    } catch (err) {
      return res.status(502).json({ error: err.message });
    }
    return res.json({ artifact: redactArtifactForActor(artifact, actor) });
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
    if (actor.tierId === 'vp' && ['md', 'ceo', 'tl'].includes(artifact.originator.tierId)) {
      return res.status(403).json({ error: 'VP should edit this FSD directly, then send it to TL for production' });
    }
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
    return res.json({ artifact: redactArtifactForActor(artifact, actor) });
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
    return res.json({ artifact: redactArtifactForActor(artifact, actor) });
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
    return res.json({ artifact: redactArtifactForActor(artifact, actor) });
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
        { comment: sendComment || 'Sent to originator for approval', finalApproverTier }
      );
    } catch (err) {
      return res.status(400).json({ error: err.message });
    }
    await artifact.save();
    return res.json({ artifact: redactArtifactForActor(artifact, actor) });
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
    const payload = redactArtifactForActor(artifact, actor);
    return res.json(teamSplitError ? { artifact: payload, teamSplitError } : { artifact: payload });
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
  respondWithArtifact(res, artifact, actor, errors);
});

module.exports = router;
