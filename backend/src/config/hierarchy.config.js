// Static demo hierarchy. MD and CEO are co-equal (same rank) — everything else
// ranks strictly below them. Approval routing is defined explicitly per
// originator rather than derived from rank math, because the rules aren't a
// uniform "one tier up": peers cross-approve each other, and client
// submissions pass through two separate gates.

const TIERS = {
  md: { id: 'md', name: 'Managing Director', rank: 1 },
  ceo: { id: 'ceo', name: 'Chief Executive Officer', rank: 1 },
  vp: { id: 'vp', name: 'Vice President', rank: 2 },
  pm: { id: 'pm', name: 'Project Manager', rank: 3 },
  tl: { id: 'tl', name: 'Team Lead', rank: 4 },
};

// Each entry is the ordered chain of approval steps a submission from that
// originator must clear. A step's `mode` is 'any' (one approver from
// approverTiers is enough) or 'all' (every listed tier must approve before
// the step is satisfied). "client" covers external submissions, which have
// no tierId of their own.
const APPROVAL_RULES = {
  md: [{ approverTiers: ['ceo'], mode: 'any' }],
  ceo: [{ approverTiers: ['md'], mode: 'any' }],
  vp: [{ approverTiers: ['md', 'ceo'], mode: 'any' }],
  pm: [{ approverTiers: ['vp'], mode: 'any' }],
  tl: [{ approverTiers: ['pm'], mode: 'any' }],
  client: [
    { approverTiers: ['md', 'ceo'], mode: 'any' },
    { approverTiers: ['vp'], mode: 'any' },
  ],
};

function resolveApprovalChain(originatorTierId) {
  const key = originatorTierId == null ? 'client' : originatorTierId;
  const chain = APPROVAL_RULES[key];
  if (!chain) {
    throw new Error(`No approval chain defined for originator tier "${originatorTierId}"`);
  }
  return chain.map((step) => ({ approverTiers: [...step.approverTiers], mode: step.mode, approvedBy: [] }));
}

module.exports = { TIERS, APPROVAL_RULES, resolveApprovalChain };
