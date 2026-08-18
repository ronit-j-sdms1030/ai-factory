const { transition } = require('../src/services/stateMachine.service');
const { resolveApprovalChain } = require('../src/config/hierarchy.config');

function makeArtifact(originatorTierId) {
  return {
    title: 'Untitled',
    currentStage: 'draft',
    approvalChain: resolveApprovalChain(originatorTierId),
    currentApprovalIndex: 0,
    history: [],
  };
}

describe('submit -> pending_approval -> approved', () => {
  test('a single-tier chain (PM -> VP) approves in one step', () => {
    const artifact = makeArtifact('pm');
    const originator = { userId: 'u-pm', tierId: 'pm' };
    const approver = { userId: 'u-vp', tierId: 'vp' };

    transition(artifact, 'submit', originator);
    expect(artifact.currentStage).toBe('pending_approval');

    transition(artifact, 'approve', approver, { comment: 'looks good' });
    expect(artifact.currentStage).toBe('approved');
    expect(artifact.history).toHaveLength(2);
    expect(artifact.history[1].action).toBe('approve');
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

  test('MD and CEO peer-approve each other', () => {
    const mdSubmission = makeArtifact('md');
    transition(mdSubmission, 'submit', { userId: 'u-md', tierId: 'md' });
    transition(mdSubmission, 'approve', { userId: 'u-ceo', tierId: 'ceo' });
    expect(mdSubmission.currentStage).toBe('approved');

    const ceoSubmission = makeArtifact('ceo');
    transition(ceoSubmission, 'submit', { userId: 'u-ceo', tierId: 'ceo' });
    transition(ceoSubmission, 'approve', { userId: 'u-md', tierId: 'md' });
    expect(ceoSubmission.currentStage).toBe('approved');
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
    transition(artifact, 'reject', { userId: 'u-pm', tierId: 'pm' }, { comment: 'not scoped correctly' });

    expect(artifact.currentStage).toBe('rejected');
    expect(() => transition(artifact, 'approve', { userId: 'u-pm', tierId: 'pm' })).toThrow(/Cannot perform "approve"/);
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
