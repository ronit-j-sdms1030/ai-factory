const express = require('express');
const { requireAuth } = require('../middleware/auth.middleware');

const router = express.Router();

// /api/artifacts now lives in the Python agent service, but the frontend must
// keep talking to a single origin with a single login. Express therefore
// authenticates the request as it always did, then forwards it onward.
//
// This is the strangler pattern: the route is gone from Express but its URL
// still works, so the frontend needs no change and can be migrated on its own
// schedule. The proxy disappears once the frontend calls the agent service
// directly and the two share a session.
const AGENT_SERVICE_URL = process.env.AGENT_SERVICE_URL || 'http://127.0.0.1:8000';

// The agent service trusts the forwarded identity instead of re-authenticating,
// so that trust has to be earned: the shared secret proves the request came
// from Express rather than from anyone who can reach the port. The agent
// service refuses forwarded identities outright when this is unset, so a
// missing secret fails closed rather than open.
const AGENT_SERVICE_TOKEN = process.env.AGENT_SERVICE_TOKEN || '';

router.use('/', requireAuth, async (req, res) => {
  const target = new URL(`/api/artifacts${req.url === '/' ? '' : req.url}`, AGENT_SERVICE_URL);

  try {
    const upstream = await fetch(target, {
      method: req.method,
      headers: {
        'Content-Type': 'application/json',
        // The session user, already authenticated here.
        'X-Actor': JSON.stringify(req.session.user),
        'X-Agent-Service-Token': AGENT_SERVICE_TOKEN,
      },
      body: ['GET', 'HEAD'].includes(req.method) ? undefined : JSON.stringify(req.body ?? {}),
      // Report generation and the team split are real model calls; the
      // default fetch timeout would abort them mid-flight.
      signal: AbortSignal.timeout(300000),
    });

    const text = await upstream.text();
    res.status(upstream.status);
    res.set('Content-Type', upstream.headers.get('content-type') || 'application/json');
    res.send(text);
  } catch (err) {
    // A stopped agent service is an operational problem, not a client error —
    // say so plainly rather than returning a confusing 404 or empty 500.
    const reason = err.name === 'TimeoutError' ? 'timed out' : `is unreachable (${err.message})`;
    res.status(502).json({ error: `The agent service ${reason}. Is it running on ${AGENT_SERVICE_URL}?` });
  }
});

module.exports = router;
