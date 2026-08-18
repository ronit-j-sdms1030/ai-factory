function requireAuth(req, res, next) {
  if (!req.session.user) return res.status(401).json({ error: 'Not authenticated' });
  next();
}

function requireInternal(req, res, next) {
  if (!req.session.user || req.session.user.isClient) {
    return res.status(403).json({ error: 'Internal access only' });
  }
  next();
}

function requireTier(...tiers) {
  return (req, res, next) => {
    if (!req.session.user || !tiers.includes(req.session.user.tierId)) {
      return res.status(403).json({ error: 'Insufficient tier for this action' });
    }
    next();
  };
}

module.exports = { requireAuth, requireInternal, requireTier };
