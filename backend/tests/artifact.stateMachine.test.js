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
  test('PM and TL submissions both need only one of MD, CEO, or VP', () => {
    // PM is no longer an approver of anything — both PM's and TL's own
    // ideas go to the same single gate, any of MD/CEO/VP.
    const pmArtifact = makeArtifact('pm');
    transition(pmArtifact, 'submit', { userId: 'u-pm', tierId: 'pm' });
    transition(pmArtifact, 'approve', { userId: 'u-vp', tierId: 'vp' }, { comment: 'looks good' });
    expect(pmArtifact.currentStage).toBe('approved');

    const tlArtifact = makeArtifact('tl');
    transition(tlArtifact, 'submit', { userId: 'u-tl', tierId: 'tl' });
    transition(tlArtifact, 'approve', { userId: 'u-md', tierId: 'md' });
    expect(tlArtifact.currentStage).toBe('approved');
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

  test('a TL idea finishes straight through by default, with no second gate', () => {
    const artifact = makeArtifact('tl');
    transition(artifact, 'submit', { userId: 'u-tl', tierId: 'tl' });
    transition(artifact, 'approve', { userId: 'u-vp', tierId: 'vp' });
    artifact.currentStage = 'fsd_review';

    transition(artifact, 'sendFsdToClient', { userId: 'u-vp', tierId: 'vp' });
    expect(artifact.currentStage).toBe('fsd_pending_client');
  });

  test('a TL idea can optionally be routed to a chosen final approver (VP, CEO, or MD)', () => {
    const artifact = makeArtifact('tl');
    transition(artifact, 'submit', { userId: 'u-tl', tierId: 'tl' });
    transition(artifact, 'approve', { userId: 'u-vp', tierId: 'vp' });
    expect(artifact.currentApprovalIndex).toBe(1);
    artifact.currentStage = 'fsd_review';

    // The reviewer sending it chooses CEO for the extra sign-off, even
    // though VP was the one who cleared gate 0.
    transition(artifact, 'sendFsdToClient', { userId: 'u-vp', tierId: 'vp' }, { finalApproverTier: 'ceo' });
    expect(artifact.currentStage).toBe('pending_approval');
    expect(artifact.approvalChain).toHaveLength(2);
    expect(artifact.approvalChain[1].approverTiers).toEqual(['ceo']);
    expect(artifact.currentApprovalIndex).toBe(1);

    // Only CEO can clear this new gate — not VP, not MD.
    expect(() => transition(artifact, 'approve', { userId: 'u-md', tierId: 'md' })).toThrow(/Unauthorized approver/);
    transition(artifact, 'approve', { userId: 'u-ceo', tierId: 'ceo' }, { comment: 'Signed off' });
    expect(artifact.currentStage).toBe('approved');
  });

  test('an invalid finalApproverTier is rejected', () => {
    const artifact = makeArtifact('tl');
    transition(artifact, 'submit', { userId: 'u-tl', tierId: 'tl' });
    transition(artifact, 'approve', { userId: 'u-vp', tierId: 'vp' });
    artifact.currentStage = 'fsd_review';

    expect(() =>
      transition(artifact, 'sendFsdToClient', { userId: 'u-vp', tierId: 'vp' }, { finalApproverTier: 'pm' })
    ).toThrow(/Invalid finalApproverTier/);
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
    transition(artifact, 'reject', { userId: 'u-vp', tierId: 'vp' }, { comment: 'not scoped correctly' });

    expect(artifact.currentStage).toBe('rejected');
    expect(() => transition(artifact, 'approve', { userId: 'u-vp', tierId: 'vp' })).toThrow(/Cannot perform "approve"/);
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
