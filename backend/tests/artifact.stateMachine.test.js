const { transition } = require('../src/services/stateMachine.service');
const { resolveApprovalChain } = require('../src/config/hierarchy.config');

function makeArtifact(originatorTierId) {
  return {
    title: 'Untitled',
    currentStage: 'draft',
    originator: { userId: 'u-' + (originatorTierId || 'client'), tierId: originatorTierId },
    approvalChain: resolveApprovalChain(originatorTierId),
    currentApprovalIndex: 0,
    history: [],
  };
}

describe('submit -> pending_approval -> approved', () => {
  test('PM needs one MD/CEO/VP approval, while TL has MD/CEO then VP gates', () => {
    const pmArtifact = makeArtifact('pm');
    transition(pmArtifact, 'submit', { userId: 'u-pm', tierId: 'pm' });
    transition(pmArtifact, 'approve', { userId: 'u-vp', tierId: 'vp' }, { comment: 'looks good' });
    expect(pmArtifact.currentStage).toBe('approved');

    const tlArtifact = makeArtifact('tl');
    transition(tlArtifact, 'submit', { userId: 'u-tl', tierId: 'tl' });
    transition(tlArtifact, 'approve', { userId: 'u-md', tierId: 'md' });
    expect(tlArtifact.currentStage).toBe('pending_approval');
    expect(tlArtifact.currentApprovalIndex).toBe(1);
    expect(tlArtifact.approvalChain[1].approverTiers).toEqual(['vp']);
  });

  test('PM can no longer approve anything — not even a TL submission', () => {
    const artifact = makeArtifact('tl');
    transition(artifact, 'submit', { userId: 'u-tl', tierId: 'tl' });
    expect(() => transition(artifact, 'approve', { userId: 'u-pm', tierId: 'pm' })).toThrow(/Unauthorized approver/);
  });

  test('VP submission needs only one of MD or CEO (either approves)', () => {
    const artifact = makeArtifact('vp');
    const originator = { userId: 'u-vp', tierId: 'vp' };

    transition(artifact, 'submit', originator);
    transition(artifact, 'approve', { userId: 'u-ceo', tierId: 'ceo' });

    expect(artifact.currentStage).toBe('approved');
    // MD never had to act — the CEO's approval alone satisfied the 'any' step
    expect(artifact.approvalChain[0].approvedBy).toHaveLength(1);
    expect(artifact.approvalChain[0].approvedBy[0].tierId).toBe('ceo');
  });

  test('VP FSD goes MD/CEO -> VP -> production without returning for final approval', () => {
    const artifact = makeArtifact('vp');
    transition(artifact, 'submit', { userId: 'u-vp', tierId: 'vp' });
    transition(artifact, 'approve', { userId: 'u-md', tierId: 'md' });

    // The route generates the FSD and gives MD/CEO their editing pass.
    artifact.currentStage = 'fsd_review';
    transition(artifact, 'sendFsdToClient', { userId: 'u-md', tierId: 'md' });
    expect(artifact.currentStage).toBe('fsd_pending_client');

    // VP sends it directly to TL production/team splitting.
    transition(artifact, 'approveFsd', { userId: 'u-vp', tierId: 'vp' });
    expect(artifact.currentStage).toBe('approved');
  });

  test('VP can release a legacy artifact already stuck at final FSD approval', () => {
    const artifact = makeArtifact('vp');
    transition(artifact, 'submit', { userId: 'u-vp', tierId: 'vp' });
    transition(artifact, 'approve', { userId: 'u-ceo', tierId: 'ceo' });
    artifact.currentStage = 'fsd_final_approval';

    transition(artifact, 'giveFinalFsdApproval', { userId: 'u-vp', tierId: 'vp' });
    expect(artifact.currentStage).toBe('approved');
  });

  test('MD and CEO self-approve gate 0, then VP still gates the FSD', () => {
    const mdSubmission = makeArtifact('md');
    transition(mdSubmission, 'submit', { userId: 'u-md', tierId: 'md' });
    // CEO cannot approve MD's own idea — only MD can.
    expect(() => transition(mdSubmission, 'approve', { userId: 'u-ceo', tierId: 'ceo' })).toThrow(/Unauthorized approver/);
    transition(mdSubmission, 'approve', { userId: 'u-md', tierId: 'md' });
    // Self-approval clears gate 0 but does NOT finish the chain — VP's gate
    // still lies ahead, and is what releases the work to the team leads.
    expect(mdSubmission.currentStage).toBe('pending_approval');
    expect(mdSubmission.currentApprovalIndex).toBe(1);
    expect(mdSubmission.approvalChain[1].approverTiers).toEqual(['vp']);

    const ceoSubmission = makeArtifact('ceo');
    transition(ceoSubmission, 'submit', { userId: 'u-ceo', tierId: 'ceo' });
    expect(() => transition(ceoSubmission, 'approve', { userId: 'u-md', tierId: 'md' })).toThrow(/Unauthorized approver/);
    transition(ceoSubmission, 'approve', { userId: 'u-ceo', tierId: 'ceo' });
    expect(ceoSubmission.currentStage).toBe('pending_approval');
    expect(ceoSubmission.currentApprovalIndex).toBe(1);
  });

  test("a self-originated MD requirement routes FSD -> VP -> approved, skipping the client loop", () => {
    const artifact = makeArtifact('md');
    transition(artifact, 'submit', { userId: 'u-md', tierId: 'md' });
    transition(artifact, 'approve', { userId: 'u-md', tierId: 'md' });

    // The route generates the detailed report here and moves it into the
    // review loop, where MD gets their editing pass.
    artifact.currentStage = 'fsd_review';

    // There is no client to send to, so this hands straight to VP rather
    // than bouncing through fsd_pending_client / fsd_final_approval.
    transition(artifact, 'sendFsdToClient', { userId: 'u-md', tierId: 'md' });
    expect(artifact.currentStage).toBe('pending_approval');
    expect(artifact.currentApprovalIndex).toBe(1);

    // Only VP can clear this gate — MD cannot wave their own work through.
    expect(() => transition(artifact, 'approve', { userId: 'u-md', tierId: 'md' })).toThrow(/Unauthorized approver/);

    transition(artifact, 'approve', { userId: 'u-vp', tierId: 'vp' }, { comment: 'FSD signed off' });
    // 'approved' + a detailed report present is exactly the condition
    // maybeGenerateTeamSplit() waits for before splitting to the TLs.
    expect(artifact.currentStage).toBe('approved');
    expect(artifact.currentApprovalIndex).toBe(2);
  });

  test('an MD cannot reroute the reviewed FSD to CEO instead of VP', () => {
    const artifact = makeArtifact('md');
    transition(artifact, 'submit', { userId: 'u-md', tierId: 'md' });
    transition(artifact, 'approve', { userId: 'u-md', tierId: 'md' });
    artifact.currentStage = 'fsd_review';

    expect(() =>
      transition(
        artifact,
        'sendFsdToClient',
        { userId: 'u-md', tierId: 'md' },
        { finalApproverTier: 'ceo' }
      )
    ).toThrow(/always route to VP/);

    // The rejected attempt must not have mutated the chain — VP is still
    // the only next gate.
    expect(artifact.approvalChain[1].approverTiers).toEqual(['vp']);

    transition(artifact, 'sendFsdToClient', { userId: 'u-md', tierId: 'md' });
    expect(artifact.currentStage).toBe('pending_approval');
    transition(artifact, 'approve', { userId: 'u-vp', tierId: 'vp' });
    expect(artifact.currentStage).toBe('approved');
  });

  test('VP cannot reject an FSD sent by MD or CEO', () => {
    const artifact = makeArtifact('md');
    transition(artifact, 'submit', { userId: 'u-md', tierId: 'md' });
    transition(artifact, 'approve', { userId: 'u-md', tierId: 'md' });
    artifact.currentStage = 'fsd_review';
    transition(artifact, 'sendFsdToClient', { userId: 'u-md', tierId: 'md' });

    expect(() => transition(artifact, 'reject', { userId: 'u-vp', tierId: 'vp' })).toThrow(/cannot reject/);
    expect(artifact.currentStage).toBe('pending_approval');
  });

  test('a TL idea follows MD/CEO FSD review -> VP -> production', () => {
    const artifact = makeArtifact('tl');
    transition(artifact, 'submit', { userId: 'u-tl', tierId: 'tl' });
    expect(() => transition(artifact, 'approve', { userId: 'u-vp', tierId: 'vp' })).toThrow(/Unauthorized approver/);
    transition(artifact, 'approve', { userId: 'u-ceo', tierId: 'ceo' });
    expect(artifact.currentApprovalIndex).toBe(1);
    artifact.currentStage = 'fsd_review';

    transition(artifact, 'sendFsdToClient', { userId: 'u-ceo', tierId: 'ceo' });
    expect(artifact.currentStage).toBe('pending_approval');
    expect(artifact.approvalChain).toHaveLength(2);
    expect(artifact.approvalChain[1].approverTiers).toEqual(['vp']);
    expect(artifact.currentApprovalIndex).toBe(1);

    expect(() => transition(artifact, 'approve', { userId: 'u-md', tierId: 'md' })).toThrow(/Unauthorized approver/);
    transition(artifact, 'approve', { userId: 'u-vp', tierId: 'vp' }, { comment: 'Sent to production' });
    expect(artifact.currentStage).toBe('approved');
  });

  test('client submissions clear an MD/CEO gate, then a separate VP gate', () => {
    const artifact = makeArtifact(null);
    const originator = { userId: 'u-client', tierId: null };

    transition(artifact, 'submit', originator);
    expect(artifact.currentStage).toBe('pending_approval');
    expect(artifact.currentApprovalIndex).toBe(0);

    transition(artifact, 'approve', { userId: 'u-md', tierId: 'md' });
    // first gate cleared, still waiting — now on the VP gate
    expect(artifact.currentStage).toBe('pending_approval');
    expect(artifact.currentApprovalIndex).toBe(1);

    // MD/CEO can no longer act on this artifact; only VP can now
    expect(() => transition(artifact, 'approve', { userId: 'u-ceo', tierId: 'ceo' })).toThrow(/Unauthorized approver/);

    transition(artifact, 'approve', { userId: 'u-vp', tierId: 'vp' });
    expect(artifact.currentStage).toBe('approved');
  });
});

describe('reject and revision paths', () => {
  test('reject moves straight to rejected and stops there', () => {
    const artifact = makeArtifact('tl');
    transition(artifact, 'submit', { userId: 'u-tl', tierId: 'tl' });
    transition(artifact, 'reject', { userId: 'u-md', tierId: 'md' }, { comment: 'not scoped correctly' });

    expect(artifact.currentStage).toBe('rejected');
    expect(() => transition(artifact, 'approve', { userId: 'u-md', tierId: 'md' })).toThrow(/Cannot perform "approve"/);
  });

  test('requestRevision sends it back, and resubmitting restarts the approval chain', () => {
    const artifact = makeArtifact('vp');
    transition(artifact, 'submit', { userId: 'u-vp', tierId: 'vp' });
    transition(artifact, 'requestRevision', { userId: 'u-md', tierId: 'md' }, { comment: 'needs more detail' });

    expect(artifact.currentStage).toBe('revision_requested');

    transition(artifact, 'submit', { userId: 'u-vp', tierId: 'vp' });
    expect(artifact.currentStage).toBe('pending_approval');
    expect(artifact.currentApprovalIndex).toBe(0);
  });
});

describe('authorization and invalid transitions', () => {
  test('an approver outside the current step is rejected', () => {
    const artifact = makeArtifact('pm');
    transition(artifact, 'submit', { userId: 'u-pm', tierId: 'pm' });

    expect(() => transition(artifact, 'approve', { userId: 'u-tl', tierId: 'tl' })).toThrow(/Unauthorized approver/);
    expect(artifact.currentStage).toBe('pending_approval');
  });

  test('approving a draft throws instead of silently skipping pending_approval', () => {
    const artifact = makeArtifact('pm');
    expect(() => transition(artifact, 'approve', { userId: 'u-vp', tierId: 'vp' })).toThrow(/Cannot perform "approve" from stage "draft"/);
  });
});
