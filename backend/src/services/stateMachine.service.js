// Pure transition logic — no DB calls. Callers pass a plain object or a
// Mongoose document; this only reads/writes fields on it and returns it.

const TRANSITIONS = {
  draft: { submit: 'pending_approval' },
  clarifying: { submit: 'pending_approval' },
  revision_requested: { submit: 'pending_approval' },
  pending_approval: { approve: 'approved', reject: 'rejected', requestRevision: 'revision_requested' },
};

function transition(artifact, action, actor, options = {}) {
  const comment = options.comment || '';
  const fromStage = artifact.currentStage;
  const stageMap = TRANSITIONS[fromStage];

  if (!stageMap || !stageMap[action]) {
    throw new Error(`Cannot perform "${action}" from stage "${fromStage}"`);
  }

  if (fromStage === 'pending_approval') {
    const step = currentStep(artifact);
    if (!step) {
      throw new Error('No pending approval step found on this artifact.');
    }
    if (!step.approverTiers.includes(actor.tierId)) {
      throw new Error(
        `Unauthorized approver: "${actor.tierId}" cannot act on this step (expected one of: ${step.approverTiers.join(', ')}).`
      );
    }
    if (action === 'approve') {
      applyApproval(artifact, actor, step, comment);
      return artifact;
    }
  }

  if (action === 'submit') {
    if (!artifact.approvalChain || artifact.approvalChain.length === 0) {
      throw new Error('Artifact has no approval chain configured; resolve one before submitting.');
    }
    artifact.currentApprovalIndex = 0;
  }

  artifact.currentStage = stageMap[action];
  pushHistory(artifact, actor, action, comment, artifact.currentStage);
  return artifact;
}

function currentStep(artifact) {
  return artifact.approvalChain ? artifact.approvalChain[artifact.currentApprovalIndex] : undefined;
}

function applyApproval(artifact, actor, step, comment) {
  step.approvedBy = step.approvedBy || [];
  step.approvedBy.push({ userId: actor.userId, tierId: actor.tierId, timestamp: new Date() });

  const satisfied =
    step.mode === 'all'
      ? step.approverTiers.every((tierId) => step.approvedBy.some((a) => a.tierId === tierId))
      : true; // 'any' — the approval that just landed is enough

  if (satisfied) {
    artifact.currentApprovalIndex += 1;
    artifact.currentStage = artifact.currentApprovalIndex >= artifact.approvalChain.length ? 'approved' : 'pending_approval';
  }

  pushHistory(artifact, actor, 'approve', comment, artifact.currentStage);
}

function pushHistory(artifact, actor, action, comment, stage) {
  artifact.history = artifact.history || [];
  artifact.history.push({
    stage,
    actorId: actor.userId,
    actorTier: actor.tierId,
    action,
    comment,
    timestamp: new Date(),
  });
}

module.exports = { transition, TRANSITIONS };
