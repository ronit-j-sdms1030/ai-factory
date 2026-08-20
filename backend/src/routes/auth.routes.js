const express = require('express');
const bcrypt = require('bcryptjs');
const User = require('../models/user.model');
const { requireAuth } = require('../middleware/auth.middleware');

const router = express.Router();

function publicUser(user) {
  return {
    id: user._id.toString(),
    name: user.name,
    email: user.email,
    tierId: user.tierId,
    isClient: user.isClient,
    department: user.department || null,
  };
}

// Internal sign-in — MD / CEO / VP / PM / TL. Accounts are provisioned via
// seed, not self-registered.
router.post('/login', async (req, res) => {
  const { email, password } = req.body || {};
  const user = await User.findOne({ email: (email || '').toLowerCase().trim(), isClient: false });
  if (!user || !(await bcrypt.compare(password || '', user.passwordHash))) {
    return res.status(401).json({ error: 'Invalid email or password' });
  }
  req.session.user = publicUser(user);
  res.json({ user: req.session.user });
});

// Client self-service — clients register their own account.
router.post('/client/register', async (req, res) => {
  const { name, email, password } = req.body || {};
  if (!name || !email || !password) {
    return res.status(400).json({ error: 'name, email, and password are required' });
  }
  const existing = await User.findOne({ email: email.toLowerCase().trim() });
  if (existing) {
    return res.status(409).json({ error: 'An account with this email already exists' });
  }
  const passwordHash = await bcrypt.hash(password, 10);
  const user = await User.create({ name, email: email.toLowerCase().trim(), passwordHash, isClient: true, tierId: null });
  req.session.user = publicUser(user);
  res.status(201).json({ user: req.session.user });
});

router.post('/client/login', async (req, res) => {
  const { email, password } = req.body || {};
  const user = await User.findOne({ email: (email || '').toLowerCase().trim(), isClient: true });
  if (!user || !(await bcrypt.compare(password || '', user.passwordHash))) {
    return res.status(401).json({ error: 'Invalid email or password' });
  }
  req.session.user = publicUser(user);
  res.json({ user: req.session.user });
});

router.post('/logout', (req, res) => {
  req.session.destroy(() => res.json({ ok: true }));
});

router.get('/me', (req, res) => {
  if (!req.session.user) return res.status(401).json({ error: 'Not authenticated' });
  res.json({ user: req.session.user });
});

// Lists every internal colleague (any tier), for the "send for discussion"
// recipient picker — any internal role can share a report with any other
// internal role, not just TL-to-TL. Clients are excluded both as senders
// (blocked in shareForDiscussion) and as recipients here.
router.get('/colleagues', requireAuth, async (req, res) => {
  const actor = req.session.user;
  if (actor.isClient) {
    return res.status(403).json({ error: 'Only internal roles can view this list' });
  }
  const colleagues = await User.find({ isClient: false }, 'name email tierId department').sort({ tierId: 1, department: 1, name: 1 });
  res.json({
    colleagues: colleagues.map((u) => ({ id: u._id.toString(), name: u.name, email: u.email, tierId: u.tierId, department: u.department })),
  });
});

module.exports = router;
