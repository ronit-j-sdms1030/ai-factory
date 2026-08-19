// Pure transition logic — no DB calls. Callers pass a plain object or a
// Mongoose document; this only reads/writes fields on it and returns it.

const TRANSITIONS = {
  draft: { submit: 'pending_approval' },
  clarifying: { submit: 'pending_approval' },
  revision_requested: { submit: 'pending_approval' },
  // The gate-0 reviewer proposes their own edit instead of asking the
  // originator to redo it; the originator just has to accept it, which
  // sends it back to the same gate for signoff.
  pending_client_review: { acceptChanges: 'pending_approval' },
  pending_approval: {
    approve: 'approved',
    reject: 'rejected',
    requestRevision: 'revision_requested',
    proposeChanges: 'pending_client_review',
  },
  fsd_review: { sendFsdToClient: 'fsd_pending_client' },
  fsd_pending_client: { approveFsd: 'fsd_final_approval' },
  // Placeholder — giveFinalFsdApproval's real target is computed dynamically
  // in transition() below, since it depends on whether the chain has a real
  // next gate (client: yes, VP) or is already exhausted (everyone else).
  fsd_final_approval: { giveFinalFsdApproval: 'approved' },
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

  // The FSD review loop's stages aren't chain-indexed like pending_approval,
  // so they need their own authorization — but it's still driven by the
  // artifact's own chain, not a hardcoded tier: whoever was eligible to
  // approve gate 0 is the reviewer pool for the whole loop. This is what
  // makes the same loop work identically for a client (gate 0: md/ceo) and
  // a PM or TL (gate 0: md/ceo/vp) without special-casing each one.
  if (fromStage === 'fsd_review' || fromStage === 'fsd_final_approval') {
    const reviewerPool = artifact.approvalChain[0].approverTiers;
    if (!reviewerPool.includes(actor.tierId)) {
      throw new Error(`Unauthorized: "${actor.tierId}" is not part of this requirement's reviewer pool (${reviewerPool.join(', ')}).`);
    }
  }
  if (fromStage === 'fsd_pending_client' || fromStage === 'pending_client_review') {
    if (actor.userId !== artifact.originator.userId) {
      throw new Error('Unauthorized: only the originator can act on this step.');
    }
  }

  if (action === 'submit') {
    if (!artifact.approvalChain || artifact.approvalChain.length === 0) {
      throw new Error('Artifact has no approval chain configured; resolve one before submitting.');
    }
    artifact.currentApprovalIndex = 0;
  }

  // Client's approvalChain has 2 real steps (MD/CEO, then VP) — gate 0 was
  // already consumed when the FSD loop started, so currentApprovalIndex is
  // already 1, meaning VP's gate genuinely still lies ahead. Every other
  // originator's chain has exactly 1 step, already exhausted at this point
  // (index 1 >= length 1) — finish straight to 'approved'.
  // A self-originated MD/CEO requirement has the same person on both ends of
  // the FSD client-review loop, so "send it to the client for review" has
  // nobody to send to. Once they're done editing in fsd_review it goes
  // straight to the next real gate instead — VP, who signs off before the
  // team split runs.
  if (action === 'sendFsdToClient' && isSelfOriginMdCeo(artifact)) {
    artifact.currentStage =
      artifact.currentApprovalIndex >= artifact.approvalChain.length ? 'approved' : 'pending_approval';
    pushHistory(artifact, actor, action, comment, artifact.currentStage);
    return artifact;
  }

  if (action === 'giveFinalFsdApproval') {
    artifact.currentStage = artifact.currentApprovalIndex >= artifact.approvalChain.length ? 'approved' : 'pending_approval';
    pushHistory(artifact, actor, action, comment, artifact.currentStage);
    return artifact;
  }

  artifact.currentStage = stageMap[action];
  pushHistory(artifact, actor, action, comment, artifact.currentStage);
  return artifact;
}

function isSelfOriginMdCeo(artifact) {
  return artifact.originator && (artifact.originator.tierId === 'md' || artifact.originator.tierId === 'ceo');
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

module.exports = { transition, TRANSITIONS, pushHistory };
