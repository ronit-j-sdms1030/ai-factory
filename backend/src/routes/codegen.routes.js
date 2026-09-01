const express = require('express');
const { randomUUID } = require('crypto');
const Artifact = require('../models/artifact.model');
const CodeGenJob = require('../models/codeGenJob.model');
const { requireAuth } = require('../middleware/auth.middleware');
const { runCodeGen, CODE_GEN_MODELS, DEFAULT_CODE_GEN_MODEL, runCodeReview, runSecurityAutoFix, FRONTEND_OWNING_DEPARTMENT, runProjectDemoSynthesis, runBuildFix } = require('../services/llm.service');
const { runSecurityScan } = require('../services/securityScanner.service');
const { runPipeline } = require('../services/pipelineRunner.service');

const router = express.Router();

// ─── Job store ──────────────────────────────────────────────────────────────
// codeGenJobs is a live, in-process cache (fast, synchronous mutation during
// the generation loop) — but CodeGenJob (Mongo) is the actual source of
// truth. Every meaningful state change gets written through to Mongo, and
// any read that misses the cache (most commonly: the backend restarted)
// falls back to Mongo and rehydrates the cache from there. Losing the cache
// only costs a DB round-trip; it no longer costs the generated code itself.
// Key: codeGenId (UUID)
// Value: { codeGenId, artifactId, department, model, modelLabel, status, files, log, totalFiles, createdAt, security, codeReview, reviewedAt }
// status: 'generating' | 'done' | 'error'

const codeGenJobs = new Map();
const activeSecurityFixes = new Set();

// A job stuck at 'generating' in Mongo when this module loads means the
// process that was actually running its generation loop is gone — nothing
// will ever pick it back up (the loop lived only in that dead process's
// memory), so it would otherwise sit "generating" forever with no way to
// even retry (retry is only offered once a job has errored). Mark it
// errored on our own startup so the existing retry path can recover it.
(async () => {
  try {
    const result = await CodeGenJob.updateMany(
      { status: 'generating' },
      {
        $set: { status: 'error' },
        $push: { log: { ts: Date.now(), msg: 'Interrupted by a server restart — click Retry to start again.' } },
      }
    );
    if (result.modifiedCount) console.log(`[codegen] marked ${result.modifiedCount} interrupted job(s) as errored on startup`);
  } catch (err) {
    console.error('[codegen] startup reconciliation failed:', err.message);
  }
})();

// Evicts the in-process cache entry after a while so memory doesn't grow
// unbounded across a long-running process — this is safe now that Mongo
// holds the real copy; a cache miss just means one extra DB read.
setInterval(() => {
  const cutoff = Date.now() - 30 * 60 * 1000;
  for (const [id, job] of codeGenJobs.entries()) {
    if (job.createdAt < cutoff) codeGenJobs.delete(id);
  }
}, 5 * 60 * 1000);

async function persistJob(job) {
  try {
    await CodeGenJob.findOneAndUpdate(
      { codeGenId: job.codeGenId },
      {
        codeGenId: job.codeGenId,
        artifactId: job.artifactId,
        department: job.department,
        model: job.model,
        modelLabel: job.modelLabel,
        status: job.status,
        files: job.files,
        log: job.log,
        totalFiles: job.totalFiles,
        createdAt: job.createdAt,
        security: job.security,
        reviewedAt: job.reviewedAt,
        ciAdded: job.ciAdded,
        ciExecution: job.ciExecution,
        tlApproved: job.tlApproved,
        tlApprovedAt: job.tlApprovedAt,
        tlApprovedBy: job.tlApprovedBy,
        codeReview: job.codeReview,
        codeReviewedAt: job.codeReviewedAt,
        testExecution: job.testExecution,
      },
      { upsert: true }
    );
  } catch (err) {
    // A missed persist is recoverable (the in-memory copy is still correct
    // for the rest of this process's lifetime) — never let it crash the
    // generation loop.
    console.error('[codegen] failed to persist job', job.codeGenId, err.message);
  }
}

function docToJob(doc) {
  return {
    codeGenId: doc.codeGenId,
    artifactId: doc.artifactId,
    department: doc.department,
    model: doc.model,
    modelLabel: doc.modelLabel,
    status: doc.status,
    files: (doc.files || []).map((f) => ({ path: f.path, description: f.description, content: f.content, done: f.done })),
    log: (doc.log || []).map((l) => ({ ts: l.ts, msg: l.msg })),
    totalFiles: doc.totalFiles,
    createdAt: doc.createdAt,
    security: doc.security || null,
    reviewedAt: doc.reviewedAt || null,
    ciAdded: !!doc.ciAdded,
    ciExecution: doc.ciExecution || null,
    tlApproved: !!doc.tlApproved,
    tlApprovedAt: doc.tlApprovedAt || null,
    tlApprovedBy: doc.tlApprovedBy || null,
    codeReview: doc.codeReview || null,
    codeReviewedAt: doc.codeReviewedAt || null,
    testExecution: doc.testExecution || null,
  };
}

// Cache-first lookup for a single job, falling back to (and rehydrating
// from) Mongo — covers the case where this process restarted since the job
// last ran. Fine for high-frequency, low-stakes reads (status polling,
// previews) where a one-poll-cycle lag costs nothing.
async function getJob(codeGenId) {
  const cached = codeGenJobs.get(codeGenId);
  if (cached) return cached;

  const doc = await CodeGenJob.findOne({ codeGenId });
  if (!doc) return null;

  const job = docToJob(doc);
  codeGenJobs.set(codeGenId, job);
  return job;
}

// Always reads fresh from Mongo (and rehydrates the cache with what it
// finds) — for anything that GATES a state transition on the job's current
// fields (approve, self-test, re-running CI/CD or security) rather than
// just displaying them. getJob()'s cache-first read can legitimately be
// stale here: this same process may have cached this exact job earlier
// (e.g. an open IDE panel polling /status) before some OTHER action (CI/CD
// finishing, a security fix landing) persisted the real update straight to
// Mongo — a gate check against that stale cache entry then rejects an
// action that's actually valid, which is confusing in a way a display lag
// never is. The extra Mongo round-trip is negligible for a one-off action;
// it's not worth paying for on every /status poll, which is why that one
// stays on getJob() above.
async function getJobFresh(codeGenId) {
  const doc = await CodeGenJob.findOne({ codeGenId });
  if (!doc) return null;

  const job = docToJob(doc);
  codeGenJobs.set(codeGenId, job);
  return job;
}

// Always reads from Mongo — used by the cross-department listing endpoints,
// which need to be correct even when nothing for this artifact is in the
// in-process cache (e.g. right after a restart).
async function getJobsForArtifact(artifactId) {
  const docs = await CodeGenJob.find({ artifactId }).sort({ createdAt: -1 });
  const latestByDept = new Map();
  for (const doc of docs) {
    if (!latestByDept.has(doc.department)) latestByDept.set(doc.department, docToJob(doc));
  }
  return latestByDept;
}

// Shared by the automatic post-generation scan (/start) and the manual
// CI/CD-stage re-scan (/run-cicd) — a security scan that finds something
// always gets an auto-fix pass and a re-scan to confirm, in both places.
// Previously only the first scan had this; a finding introduced by the
// CI/CD file itself (or anything else the CI/CD stage touches) sat
// unfixed forever because /run-cicd only ever reported, never fixed.
// A single fix-and-rescan pass isn't reliable — the same findings, sent to
// the same fix call twice, can come back with 3 files fixed one time and 1
// file fixed the next (real model non-determinism, not a bug in the
// prompt) — and a round can just as easily make things WORSE (fix one
// finding, introduce or re-trigger another) as better. So this tracks the
// best (fewest-findings) state seen across rounds and always retries from
// that baseline rather than compounding on top of a regression, and keeps
// trying for a couple of rounds past a stall before giving up — genuinely
// more persistent than "stop at the first round that doesn't improve."
const MAX_AUTO_FIX_ROUNDS = 6;
const MAX_STALLED_ROUNDS = 2; // consecutive non-improving attempts (from the best baseline) before calling it exhausted

function snapshotFileContents(files) {
  return new Map(files.map((f) => [f.path, f.content]));
}
function restoreFileContents(files, snapshot) {
  for (const f of files) {
    if (snapshot.has(f.path)) f.content = snapshot.get(f.path);
  }
}

// Deterministic remediation for recurring, mechanically provable findings.
// These do not benefit from an LLM retry: imports have one canonical form,
// and a generated Dockerfile can use an explicit allow-list instead of a
// recursive build-context copy.
function applyDeterministicSecurityFixes(job) {
  let changed = 0;
  for (const file of job.files) {
    if (!file.content) continue;
    if (/\.m?js$/i.test(file.path)) {
      const fixed = file.content
        .replace(/require\((['"])fs\1\)/g, "require('node:fs')")
        .replace(/require\((['"])path\1\)/g, "require('node:path')");
      if (fixed !== file.content) { file.content = fixed; changed++; }
    }
  }

  const dockerfile = job.files.find((file) => file.path === 'Dockerfile');
  const packageFile = job.files.find((file) => file.path === 'package.json');
  if (dockerfile && packageFile) {
    const copyable = job.files
      .filter((file) => file.path !== 'Dockerfile' && file.path !== '.env.example' && file.path !== '.dockerignore' && file.content)
      .map((file) => file.path.replace(/\\/g, '/'));
    const lines = [
      'FROM node:20-alpine',
      'RUN mkdir -p /app && chown node:node /app',
      'USER node',
      'WORKDIR /app',
      ...copyable.map((filePath) => `COPY --chown=node:node ["${filePath}", "./${filePath}"]`),
      'RUN npm install --ignore-scripts --no-audit --no-fund',
      'CMD ["npm", "test"]',
      '',
    ];
    const fixed = lines.join('\n');
    if (fixed !== dockerfile.content) { dockerfile.content = fixed; changed++; }
  }
  return changed;
}

async function scanAndAutoFix(job) {
  const onProgress = (msg) => {
    job.log.push({ ts: Date.now(), msg });
    persistJob(job);
  };
  const deterministicChanges = applyDeterministicSecurityFixes(job);
  if (deterministicChanges) {
    job.log.push({ ts: Date.now(), msg: `Applied deterministic security hardening to ${deterministicChanges} file(s).` });
    await persistJob(job);
  }
  job.log.push({ ts: Date.now(), msg: 'Running 3-stage security scan (Security Tests -> Semgrep -> SonarQube)…' });
  await persistJob(job);

  let security = await runSecurityScan({ department: job.department, artifactId: job.artifactId, files: job.files, onProgress });
  let best = { security, files: snapshotFileContents(job.files) };
  let stalledRounds = 0;

  for (let round = 1; round <= MAX_AUTO_FIX_ROUNDS && security.verdict === 'needs_fixes' && security.findings.length; round++) {
    job.log.push({ ts: Date.now(), msg: `Security scan found ${security.findings.length} issue(s) — applying AI fixes (round ${round})…` });
    await persistJob(job);

    const fixedFiles = await runSecurityAutoFix({ department: job.department, files: job.files, findings: security.findings, model: job.model });
    if (!fixedFiles.length) break; // nothing came back — no point re-scanning the same state again

    for (const fixed of fixedFiles) {
      const existing = job.files.find((f) => f.path === fixed.path);
      if (existing) existing.content = fixed.content;
    }
    // AI rewrites can reintroduce mechanically identifiable issues. Reapply
    // the deterministic rules before every rescan so the verified baseline
    // cannot regress between repair rounds.
    applyDeterministicSecurityFixes(job);
    job.log.push({ ts: Date.now(), msg: `Fixed ${fixedFiles.length} file(s) — re-running 3-stage security scan…` });
    await persistJob(job);
    security = await runSecurityScan({ department: job.department, artifactId: job.artifactId, files: job.files, onProgress });

    if (security.findings.length < best.security.findings.length) {
      best = { security, files: snapshotFileContents(job.files) };
      stalledRounds = 0;
    } else {
      stalledRounds++;
      // This attempt didn't beat the best seen so far — retry the next
      // round from that best baseline instead of building further on a
      // regression, so a bad round can never leave the code worse off
      // than an earlier good one.
      restoreFileContents(job.files, best.files);
      security = best.security;
      if (stalledRounds >= MAX_STALLED_ROUNDS) break;
    }
  }

  security = best.security;
  restoreFileContents(job.files, best.files);

  // Auto-fix genuinely tried and plateaued — some findings (a SonarQube
  // security hotspot flagging any `COPY . .` in a Dockerfile, for
  // instance) are inherently a "have a human look at this" flag, not a
  // defect a rewrite reliably eliminates. Marking it exhausted (rather
  // than silently leaving it as a plain "needs_fixes" that implies
  // clicking re-run again will help) is what the UI uses to show these as
  // flagged for manual review instead of a bare failing gate — the
  // findings themselves stay fully visible either way; this never claims
  // a pass that isn't real.
  if (security.verdict === 'needs_fixes' && security.findings.length) {
    security.exhausted = true;
  }

  job.security = security;
  job.reviewedAt = Date.now();
  job.log.push({
    ts: Date.now(),
    msg: security.verdict === 'pass'
      ? '✓ All security tests passed after AI repair.'
      : security.exhausted
        ? `⚠ Auto-fix plateaued after repeated attempts — ${security.findings.length} issue(s) flagged for manual review in IDE.`
        : `⚠ Security scan still flags ${security.findings.length} issue(s) after fixing.`,
  });
  await persistJob(job);
  return security;
}

async function scanOnly(job) {
  const onProgress = (msg) => {
    job.log.push({ ts: Date.now(), msg });
    persistJob(job);
  };
  job.log.push({ ts: Date.now(), msg: 'Initiating 3-stage security scan: 1. Security Tests -> 2. Semgrep OSS -> 3. SonarQube…' });
  await persistJob(job);
  const security = await runSecurityScan({
    department: job.department,
    artifactId: job.artifactId,
    files: job.files,
    onProgress,
  });
  job.security = security;
  job.reviewedAt = Date.now();
  if (security.verdict === 'pass') {
    job.log.push({ ts: Date.now(), msg: '✓ 3-stage security scan passed (0 issues found).' });
  } else {
    job.log.push({
      ts: Date.now(),
      msg: `⚠ 3-stage security scan completed with ${security.findings.length} issue(s). TL action options: "Fix with AI" or "Fix manually in IDE".`,
    });
  }
  await persistJob(job);
  return security;
}

// ─── Allowed model whitelist ───────────────────────────────────────────────────
const ALLOWED_MODELS = new Set(Object.keys(CODE_GEN_MODELS));

// ─── POST /api/codegen/:artifactId/start ──────────────────────────────────────
// Kicks off a code-gen job. Returns { codeGenId, totalFiles (once planning done) }.
// The actual generation runs async; poll /status for updates.
router.post('/:artifactId/start', requireAuth, async (req, res) => {
  const actor = req.session.user;

  // Only internal TLs can generate code
  if (actor.isClient || actor.tierId !== 'tl') {
    return res.status(403).json({ error: 'Only Team Leads can generate code' });
  }
  if (!actor.department) {
    return res.status(403).json({ error: 'Your account has no department assigned' });
  }

  const artifact = await Artifact.findById(req.params.artifactId);
  if (!artifact) return res.status(404).json({ error: 'Artifact not found' });

  if (artifact.currentStage !== 'approved') {
    return res.status(400).json({ error: 'Code can only be generated for fully approved requirements' });
  }

  const teamReport = (artifact.teamReports || []).find((t) => t.team === actor.department);
  if (!teamReport) {
    return res.status(403).json({ error: 'No team work package found for your department' });
  }

  // Given to every department's own generation, not just the frontend
  // owner's — lets each one build with awareness of what else is being
  // built alongside it (consistent naming/API conventions), and lets the
  // frontend-owning department's sandbox demo represent the whole project's
  // planned features (mocked), not just this department's own slice.
  // dataModel travels with each package so a department can see which
  // entities another department OWNS, and reference them by their exact
  // agreed names instead of inventing a parallel incompatible definition.
  const otherDepartments = (artifact.teamReports || [])
    .filter((t) => t.team !== actor.department)
    .map((t) => ({ team: t.team, objective: t.objective, architecture: t.architecture, dataModel: t.dataModel }));

  // Locked once a job exists and hasn't errored — starting a second job
  // over a generating/done one would silently orphan it (nothing else in
  // the app references the abandoned codeGenId, so its progress would
  // look "lost" even though it's still running/complete server-side). The
  // frontend already hides this path once a job exists; this is the
  // server-side backstop for a stale tab or race.
  const existingByDept = await getJobsForArtifact(req.params.artifactId);
  const existingJob = existingByDept.get(actor.department);
  if (existingJob && existingJob.status !== 'error') {
    return res.status(409).json({ error: 'Code generation already exists for this department — reopen it instead of starting a new one.', status: existingJob.status });
  }

  const requestedModel = req.body && req.body.model;
  const model = ALLOWED_MODELS.has(requestedModel) ? requestedModel : DEFAULT_CODE_GEN_MODEL;

  const codeGenId = randomUUID();
  const job = {
    codeGenId,
    artifactId: artifact._id.toString(),
    department: actor.department,
    model,
    modelLabel: CODE_GEN_MODELS[model],
    status: 'generating',
    files: [],          // { path, description, content, done }
    log: [],
    totalFiles: 0,
    createdAt: Date.now(),
    security: null,
    reviewedAt: null,
    ciAdded: false,
    ciExecution: null,
    tlApproved: false,
    tlApprovedAt: null,
    tlApprovedBy: null,
    codeReview: null,
    codeReviewedAt: null,
    testExecution: null,
  };
  codeGenJobs.set(codeGenId, job);
  await persistJob(job);

  // Fire-and-forget async generation
  (async () => {
    try {
      job.log.push({ ts: Date.now(), msg: `Planning file structure with ${CODE_GEN_MODELS[model]}…` });

      await runCodeGen({
        teamReport: teamReport.toObject ? teamReport.toObject() : teamReport,
        artifact: { title: artifact.title },
        model,
        otherDepartments,
        onFileProgress: ({ index, total, path: filePath, status, missing, message, partialContent }) => {
          job.totalFiles = total;
          if (status === 'generating') {
            // Add a pending slot so the file tree shows it immediately
            if (!job.files.find((f) => f.path === filePath)) {
              job.files.push({ path: filePath, description: '', content: '', done: false });
            }
            job.log.push({ ts: Date.now(), msg: `Generating ${filePath} (${index + 1}/${total})…` });
          } else if (status === 'streaming') {
            // Mutating the in-memory copy is enough for the IDE panel's own
            // polling (same process, same object) to show the file being
            // written live — persisting every chunk to Mongo would be way
            // more writes than this is worth; a throttled persist (a couple
            // times a second) still bounds how much a mid-file restart loses.
            const existing = job.files.find((f) => f.path === filePath);
            if (existing) existing.content = partialContent;
            const now = Date.now();
            if (!job._lastStreamPersist || now - job._lastStreamPersist > 500) {
              job._lastStreamPersist = now;
              persistJob(job);
            }
          } else if (status === 'done') {
            // Content will be overwritten below from the returned array
            const existing = job.files.find((f) => f.path === filePath);
            if (existing) existing.done = true;
            job.log.push({ ts: Date.now(), msg: `✓ ${filePath}` });
            // Write through per file, not just at the end — a restart
            // mid-generation should lose at most the one file in flight,
            // not everything generated so far.
            persistJob(job);
          } else if (status === 'coverage-fix') {
            job.log.push({ ts: Date.now(), msg: `Sandbox demo is missing ${missing.join(', ')} — adding mock section(s)…` });
          } else if (status === 'coverage-error') {
            job.log.push({ ts: Date.now(), msg: `Could not verify cross-department coverage: ${message}` });
          } else if (status === 'validation-retry') {
            job.log.push({ ts: Date.now(), msg: `${filePath} didn't parse (${message}) — regenerating…` });
          } else if (status === 'validation-failed') {
            job.log.push({ ts: Date.now(), msg: `⚠ ${filePath} still doesn't parse after retrying (${message}) — shipped anyway, will need a manual look.` });
          }
        },
      }).then(async (result) => {
        // Merge full content into job.files
        for (const generated of result.files) {
          const existing = job.files.find((f) => f.path === generated.path);
          if (existing) {
            existing.content = generated.content;
            existing.description = generated.description;
            existing.done = true;
          } else {
            job.files.push({ ...generated, done: true });
          }
        }
        job.status = 'done';
        job.log.push({ ts: Date.now(), msg: `✓ Done — ${result.files.length} file${result.files.length === 1 ? '' : 's'} generated.` });
        await persistJob(job);

        // Auto security scan — runs the moment this module's own code is
        // ready, not gated behind every other department finishing (that's
        // what the manual "Run CI/CD" project-wide step is for). Finds +
        // fixes + re-scans via the shared helper (also used by /run-cicd).
        try {
          await scanOnly(job);
        } catch (err) {
          job.log.push({ ts: Date.now(), msg: `Security scan failed: ${err.message}` });
        }
        await persistJob(job);
      });
    } catch (err) {
      job.status = 'error';
      job.log.push({ ts: Date.now(), msg: `Error: ${err.message}` });
      await persistJob(job);
    }
  })();

  res.status(202).json({ codeGenId, model, modelLabel: CODE_GEN_MODELS[model] });
});

// ─── GET /api/codegen/:codeGenId/status ───────────────────────────────────────
// Returns the current job state for polling.
router.get('/:codeGenId/status', requireAuth, async (req, res) => {
  const job = await getJob(req.params.codeGenId);
  if (!job) return res.status(404).json({ error: 'Code gen job not found (may have expired)' });

  // Map finding counts per file
  const findingsByFile = {};
  for (const f of job.security?.findings || []) {
    const norm = (f.file || '').replace(/\\/g, '/');
    findingsByFile[norm] = (findingsByFile[norm] || 0) + 1;
  }

  // Send file stubs (path + done flag + finding counts) but NOT full content —
  // the UI fetches a finished file's content separately via /file to keep
  // status payloads small. The one exception is whichever file is actively
  // streaming right now (still !done but already has content) — its
  // growing partial content rides along here so the IDE panel can show it
  // being written in real time; only ever one file at a time, so this stays
  // small too.
  res.json({
    status: job.status,
    model: job.model,
    modelLabel: job.modelLabel,
    totalFiles: job.totalFiles,
    files: job.files.map((f) => ({
      path: f.path,
      description: f.description,
      done: f.done,
      findingsCount: findingsByFile[f.path.replace(/\\/g, '/')] || 0,
      ...(!f.done && f.content ? { partialContent: f.content } : {}),
    })),
    log: job.log,
    security: job.security || null,
    reviewedAt: job.reviewedAt || null,
    ciAdded: !!job.ciAdded,
    ciExecution: job.ciExecution || null,
    tlApproved: !!job.tlApproved,
    tlApprovedAt: job.tlApprovedAt || null,
    tlApprovedBy: job.tlApprovedBy || null,
    codeReview: job.codeReview || null,
    codeReviewedAt: job.codeReviewedAt || null,
    testExecution: job.testExecution || null,
    securityFixRunning: activeSecurityFixes.has(job.codeGenId),
  });
});

// ─── GET /api/codegen/:codeGenId/file ─────────────────────────────────────────
// Returns the content of one file from the job along with its specific security findings.
router.get('/:codeGenId/file', requireAuth, async (req, res) => {
  const job = await getJob(req.params.codeGenId);
  if (!job) return res.status(404).json({ error: 'Code gen job not found' });

  const filePath = req.query.path;
  const file = job.files.find((f) => f.path === filePath);
  if (!file) return res.status(404).json({ error: 'File not found in this job' });

  const normPath = (filePath || '').replace(/\\/g, '/');
  const fileFindings = (job.security?.findings || []).filter((f) => (f.file || '').replace(/\\/g, '/') === normPath);

  res.json({
    path: file.path,
    content: file.content,
    done: file.done,
    findings: fileFindings,
  });
});

// Manual IDE editing is restricted to the module's owning TL.
router.put('/:codeGenId/file', requireAuth, async (req, res) => {
  const actor = req.session.user;
  const job = await getJob(req.params.codeGenId);
  if (!job) return res.status(404).json({ error: 'Code gen job not found' });
  if (actor.isClient || actor.tierId !== 'tl' || actor.department !== job.department) {
    return res.status(403).json({ error: 'Only this department’s Team Lead can edit these files' });
  }
  const file = job.files.find((item) => item.path === req.body?.path);
  if (!file) return res.status(404).json({ error: 'File not found in this job' });
  if (typeof req.body?.content !== 'string') return res.status(400).json({ error: 'content must be a string' });
  file.content = req.body.content;
  job.tlApproved = false;

  let securityResult = null;
  if (req.body.rescan) {
    job.log.push({ ts: Date.now(), msg: `Saved ${file.path} — running immediate 3-stage security re-scan…` });
    securityResult = await scanOnly(job);
  } else {
    job.log.push({ ts: Date.now(), msg: `Manual IDE edit saved: ${file.path}. Re-scan to verify.` });
    await persistJob(job);
  }

  res.json({ path: file.path, saved: true, security: securityResult || job.security });
});

// ─── POST /api/codegen/:codeGenId/rescan-security ─────────────────────────────
// Re-runs the 3-stage security scan on demand (e.g. after manual edits in IDE).
router.post('/:codeGenId/rescan-security', requireAuth, async (req, res) => {
  const actor = req.session.user;
  const job = await getJobFresh(req.params.codeGenId);
  if (!job) return res.status(404).json({ error: 'Code gen job not found' });
  if (actor.isClient || actor.tierId !== 'tl' || actor.department !== job.department) {
    return res.status(403).json({ error: 'Only this department’s Team Lead can run security re-scans' });
  }

  try {
    job.log.push({ ts: Date.now(), msg: 'TL requested manual 3-stage security re-scan…' });
    const security = await scanOnly(job);
    res.json({ security });
  } catch (err) {
    res.status(502).json({ error: err.message });
  }
});

// ─── GET /api/codegen/:codeGenId/download ─────────────────────────────────────
// Streams a zip archive of all completed files.
router.get('/:codeGenId/download', requireAuth, async (req, res) => {
  const job = await getJob(req.params.codeGenId);
  if (!job) return res.status(404).json({ error: 'Code gen job not found (may have expired)' });

  const doneFiles = job.files.filter((f) => f.done && f.content);
  if (doneFiles.length === 0) {
    return res.status(400).json({ error: 'No files have been generated yet' });
  }

  // Build a simple zip in pure JS (no native deps) — each file is stored with
  // deflate-0 (store) so we avoid pulling in a native addon. The format is
  // standard PKZIP so any unzip utility can open it.
  try {
    const archiver = require('archiver');
    const filename = (job.department || 'codegen').toLowerCase().replace(/\s+/g, '-') + '-generated.zip';
    res.setHeader('Content-Type', 'application/zip');
    res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);

    const archive = archiver('zip', { zlib: { level: 6 } });
    archive.pipe(res);
    for (const file of doneFiles) {
      archive.append(Buffer.from(file.content, 'utf8'), { name: file.path });
    }
    await archive.finalize();
  } catch (err) {
    // archiver not installed — fall back to a plain text bundle
    const lines = [];
    for (const file of doneFiles) {
      lines.push('// ===== ' + file.path + ' =====');
      lines.push(file.content);
      lines.push('');
    }
    const filename = (job.department || 'codegen').toLowerCase().replace(/\s+/g, '-') + '-generated.txt';
    res.setHeader('Content-Type', 'text/plain; charset=utf-8');
    res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);
    res.send(lines.join('\n'));
  }
});

// ─── CI/CD pipeline file — template-based, not AI-generated ───────────────────
// Deterministic on purpose: this is boilerplate the department's own stack
// dictates, not something that benefits from an LLM's creativity, and a
// template can't drift into invalid YAML the way a generated one occasionally
// might.
function detectStackKind(files) {
  const paths = files.map((f) => f.path);
  if (paths.includes('package.json')) return 'node';
  if (paths.includes('requirements.txt') || paths.some((p) => p.endsWith('.py'))) return 'python';
  return 'generic';
}

function buildCiWorkflow(department, stackKind) {
  const header = ['name: CI - ' + department, 'on:', '  push:', '    branches: [main]', '  pull_request:', '    branches: [main]', 'jobs:'];
  if (stackKind === 'node') {
    return header.concat([
      '  build-and-test:',
      '    runs-on: ubuntu-latest',
      '    steps:',
      '      - uses: actions/checkout@v4',
      '      - uses: actions/setup-node@v4',
      '        with:',
      "          node-version: '20'",
      '      - run: npm ci',
      '      - run: npm run build --if-present',
      '      - run: npm test --if-present',
    ]).join('\n') + '\n';
  }
  if (stackKind === 'python') {
    return header.concat([
      '  build-and-test:',
      '    runs-on: ubuntu-latest',
      '    steps:',
      '      - uses: actions/checkout@v4',
      '      - uses: actions/setup-python@v5',
      '        with:',
      "          python-version: '3.12'",
      '      - run: pip install -r requirements.txt',
      '      - run: pytest',
    ]).join('\n') + '\n';
  }
  return header.concat([
    '  build:',
    '    runs-on: ubuntu-latest',
    '    steps:',
    '      - uses: actions/checkout@v4',
    '      - run: echo "Add build/test steps for this stack"',
  ]).join('\n') + '\n';
}

// ─── POST /api/codegen/:artifactId/run-cicd ───────────────────────────────────
// Manual, project-wide step — only runnable once every department's module
// has finished generating. Per department: adds a CI/CD workflow file, then
// re-runs the security scan over the module INCLUDING that new file (a CI
// workflow can itself have real findings — an unpinned Action reference,
// for instance — so it needs to go through the same scan as everything
// else). This is the "ci/cd" stage; self-testing is a separate, later
// stage gated behind the owning TL's approval (see /:codeGenId/approve and
// /:codeGenId/self-test below).
router.post('/:artifactId/run-cicd', requireAuth, async (req, res) => {
  const actor = req.session.user;
  if (actor.isClient) return res.status(403).json({ error: 'Clients cannot run this step' });

  const artifact = await Artifact.findById(req.params.artifactId).select('teamReports approvalChain originator');
  if (!artifact) return res.status(404).json({ error: 'Artifact not found' });

  const departments = (artifact.teamReports || []).map((t) => t.team);
  if (!departments.length) return res.status(400).json({ error: 'This requirement has no department packages yet' });

  const isOriginator = artifact.originator.userId === actor.id;
  // Checking only approvalChain[0] excluded VP from nearly every real
  // artifact: VP is gate 1 (after MD/CEO/TL/client's own gate 0), not gate
  // 0, for every originator except PM. Check the whole chain, not just its
  // first step, so anyone who's a real approver anywhere in it — including
  // VP on the far more common gate-1 case — can act here.
  const isReviewer = artifact.approvalChain.some((step) => step.approverTiers.includes(actor.tierId));
  const isInvolvedTl = actor.tierId === 'tl' && actor.department && departments.includes(actor.department);
  if (!isOriginator && !isReviewer && !isInvolvedTl) {
    return res.status(403).json({ error: 'You are not authorized to run this step' });
  }

  const latestByDept = await getJobsForArtifact(req.params.artifactId);

  const missing = departments.filter((d) => {
    const job = latestByDept.get(d);
    return !job || job.status !== 'done';
  });
  if (missing.length) {
    return res.status(400).json({ error: 'All department modules must be fully generated first — still waiting on: ' + missing.join(', ') });
  }

  const results = await Promise.all(departments.map(async (department) => {
    const job = latestByDept.get(department);
    const ciPath = '.github/workflows/ci.yml';
    let ciAdded = false;
    if (!job.files.find((f) => f.path === ciPath)) {
      job.files.push({ path: ciPath, description: 'CI/CD pipeline', content: buildCiWorkflow(department, detectStackKind(job.files)), done: true });
      ciAdded = true;
    }
    job.ciAdded = true;

    try {
      const security = await scanOnly(job);
      job.log.push({ ts: Date.now(), msg: 'Executing CI build and validation checks…' });
      job.ciExecution = await runPipeline({ files: job.files, phase: 'ci' });
      job.log.push({ ts: Date.now(), msg: job.ciExecution.verdict === 'pass' ? '✓ CI execution passed.' : '⚠ CI execution failed — inspect the recorded command output.' });
      codeGenJobs.set(job.codeGenId, job);
      await persistJob(job);
      return { department, ciAdded, security, ciExecution: job.ciExecution };
    } catch (err) {
      return { department, ciAdded, error: err.message };
    }
  }));

  res.json({ results });
});

// Module-level retry used by the department row's "Re-run CI/CD" action.
// This avoids making one TL wait for every unrelated department scan.
router.post('/:codeGenId/rerun-cicd', requireAuth, async (req, res) => {
  const actor = req.session.user;
  const job = await getJobFresh(req.params.codeGenId);
  if (!job) return res.status(404).json({ error: 'Code gen job not found' });
  if (actor.isClient || (actor.tierId === 'tl' && actor.department !== job.department)) {
    return res.status(403).json({ error: 'Only this department’s Team Lead or an authorized reviewer can rerun this module' });
  }
  if (job.status !== 'done') return res.status(400).json({ error: 'Code generation must finish before CI/CD can run' });

  const ciPath = '.github/workflows/ci.yml';
  if (!job.files.find((file) => file.path === ciPath)) {
    job.files.push({ path: ciPath, description: 'CI/CD pipeline', content: buildCiWorkflow(job.department, detectStackKind(job.files)), done: true });
  }
  job.ciAdded = true;
  job.log.push({ ts: Date.now(), msg: 'Re-running CI/CD for this module…' });
  await persistJob(job);

  try {
    const security = await scanOnly(job);
    job.log.push({ ts: Date.now(), msg: 'Executing CI build and validation checks…' });
    job.ciExecution = await runPipeline({ files: job.files, phase: 'ci' });
    job.log.push({ ts: Date.now(), msg: job.ciExecution.verdict === 'pass' ? '✓ CI execution passed.' : '⚠ CI execution failed — inspect the recorded command output.' });
    codeGenJobs.set(job.codeGenId, job);
    await persistJob(job);
    res.json({ department: job.department, security, ciExecution: job.ciExecution });
  } catch (err) {
    res.status(502).json({ error: err.message });
  }
});

// Explicit TL-controlled AI repair. The initial scan never modifies code;
// this endpoint is only called after the TL chooses "Fix with AI".
router.post('/:codeGenId/fix-security-ai', requireAuth, async (req, res) => {
  const actor = req.session.user;
  const job = await getJobFresh(req.params.codeGenId);
  if (!job) return res.status(404).json({ error: 'Code gen job not found' });
  if (actor.isClient || actor.tierId !== 'tl' || actor.department !== job.department) {
    return res.status(403).json({ error: 'Only this department’s Team Lead can apply AI fixes' });
  }
  // This used to only ever touch security findings — if security was
  // already clean but the real CI build/test execution itself failed, the
  // button was offered (the UI shows it whenever either one isn't passing)
  // but the request was rejected outright, with no automated way to fix a
  // build failure at all. Now it fixes whichever of the two actually needs it.
  const hasSecurityFindings = !!(job.security && job.security.verdict !== 'pass');
  const hasBuildFailure = !!(job.ciExecution && job.ciExecution.verdict !== 'pass');
  if (!hasSecurityFindings && !hasBuildFailure) {
    return res.status(400).json({ error: 'Nothing to fix — security and the CI build both already pass.' });
  }
  if (activeSecurityFixes.has(job.codeGenId)) {
    return res.status(409).json({ error: 'An AI fix is already running for this module' });
  }
  activeSecurityFixes.add(job.codeGenId);
  try {
    let security = job.security;
    if (hasSecurityFindings) {
      job.log.push({ ts: Date.now(), msg: 'TL requested AI-assisted security fixes…' });
      security = await scanAndAutoFix(job);
    }
    if (hasBuildFailure) {
      job.log.push({ ts: Date.now(), msg: 'TL requested AI-assisted CI build fixes…' });
      const fixedFiles = await runBuildFix({ department: job.department, files: job.files, ciExecution: job.ciExecution, model: job.model });
      if (fixedFiles.length) {
        for (const fixed of fixedFiles) {
          const existing = job.files.find((f) => f.path === fixed.path);
          if (existing) existing.content = fixed.content;
          else job.files.push({ path: fixed.path, description: '', content: fixed.content, done: true });
        }
        job.log.push({ ts: Date.now(), msg: `AI build fix touched ${fixedFiles.length} file(s) — re-running CI…` });
      }
    }
    job.ciExecution = await runPipeline({ files: job.files, phase: 'ci' });
    job.log.push({ ts: Date.now(), msg: job.ciExecution.verdict === 'pass' ? '✓ CI execution passed.' : '⚠ CI execution still failing after the AI fix attempt.' });
    await persistJob(job);
    res.json({ security, ciExecution: job.ciExecution });
  } catch (err) {
    res.status(502).json({ error: err.message });
  } finally {
    activeSecurityFixes.delete(job.codeGenId);
  }
});

// ─── POST /api/codegen/:codeGenId/approve ─────────────────────────────────────
// TL-only gate: the owning department's TL signs off on the CI/CD result
// before self-testing is allowed to run. Requires the CI/CD stage to have
// actually completed first (a file added, a security scan on record).
router.post('/:codeGenId/approve', requireAuth, async (req, res) => {
  const actor = req.session.user;
  const job = await getJobFresh(req.params.codeGenId);
  if (!job) return res.status(404).json({ error: 'Code gen job not found' });

  if (actor.isClient || actor.tierId !== 'tl' || actor.department !== job.department) {
    return res.status(403).json({ error: 'Only this department\'s own Team Lead can approve this module' });
  }
  if (!job.ciAdded) {
    return res.status(400).json({ error: 'Run CI/CD for this module before approving it' });
  }
  if (!job.security) {
    return res.status(400).json({ error: 'The CI/CD security scan is still running for this module — wait for it to finish before approving' });
  }
  if (job.security.verdict !== 'pass') {
    return res.status(400).json({ error: 'Security scanning must pass before this module can be approved' });
  }
  if (!job.ciExecution || job.ciExecution.verdict !== 'pass') {
    return res.status(400).json({ error: 'The executable CI checks must pass before this module can be approved' });
  }

  job.tlApproved = true;
  job.tlApprovedAt = Date.now();
  job.tlApprovedBy = actor.name;
  job.log.push({ ts: Date.now(), msg: `✓ Approved by ${actor.name} (TL, ${job.department}).` });
  codeGenJobs.set(job.codeGenId, job);
  await persistJob(job);

  res.json({ tlApproved: true, tlApprovedAt: job.tlApprovedAt, tlApprovedBy: job.tlApprovedBy });
});

// ─── POST /api/codegen/:codeGenId/self-test ───────────────────────────────────
// "Self AI testing" — the same AI code-quality review as before, now its
// own explicit stage that only unlocks once the owning TL has approved the
// CI/CD result. Still a read-only review of already-generated text.
router.post('/:codeGenId/self-test', requireAuth, async (req, res) => {
  const actor = req.session.user;
  if (actor.isClient) return res.status(403).json({ error: 'Clients cannot run this step' });

  const job = await getJobFresh(req.params.codeGenId);
  if (!job) return res.status(404).json({ error: 'Code gen job not found' });
  if (!job.tlApproved) {
    return res.status(400).json({ error: 'This module needs its Team Lead\'s approval before self-testing can begin' });
  }

  try {
    // Run independently — a failing (or simply nonexistent) real test suite
    // shouldn't hide the AI review entirely. A TL needs both pieces of
    // information regardless of which one failed; a test failure that
    // dead-ends before the review ever runs means the one time you'd most
    // want AI feedback (something's actually broken) is exactly when you
    // never get it.
    const [testExecution, codeReview] = await Promise.all([
      runPipeline({ files: job.files, phase: 'test' }),
      runCodeReview({ department: job.department, files: job.files }),
    ]);
    job.testExecution = testExecution;
    job.codeReview = codeReview;
    job.codeReviewedAt = Date.now();
    job.log.push({ ts: Date.now(), msg: testExecution.verdict === 'pass' ? '✓ Real test execution passed.' : '⚠ Real test execution failed.' });
    job.log.push({
      ts: Date.now(),
      msg: codeReview.verdict === 'pass' ? '✓ Self AI testing passed.' : `⚠ Self AI testing flagged ${codeReview.findings.length} issue(s).`,
    });
    codeGenJobs.set(job.codeGenId, job);
    await persistJob(job);
    res.json({ testExecution, codeReview });
  } catch (err) {
    res.status(502).json({ error: err.message });
  }
});

// ─── UAT sandbox — static preview of a generated frontend ─────────────────────
// Deliberately NOT a build-and-run sandbox: no npm install, no dev server,
// no server-side execution of generated code. This finds an HTML entry
// point and inlines any local CSS/JS it references so it can be shown in a
// sandboxed iframe — the same trust boundary as viewing any static site.
function findFrontendEntry(files) {
  const htmlFiles = files.filter((f) => f.path.toLowerCase().endsWith('.html') && f.content);
  if (!htmlFiles.length) return null;
  return (
    htmlFiles.find((f) => /(^|\/)index\.html$/i.test(f.path)) ||
    htmlFiles.find((f) => /^public\//i.test(f.path)) ||
    htmlFiles[0]
  );
}

function resolveRelative(basePath, ref) {
  if (/^([a-z]+:)?\/\//i.test(ref) || ref.startsWith('data:')) return null; // external/remote — leave alone
  const baseDir = basePath.includes('/') ? basePath.slice(0, basePath.lastIndexOf('/')) : '';
  const parts = (baseDir ? baseDir + '/' + ref : ref).split('/');
  const resolved = [];
  for (const part of parts) {
    if (part === '.' || part === '') continue;
    if (part === '..') resolved.pop();
    else resolved.push(part);
  }
  return resolved.join('/');
}

function buildStaticPreview(entryFile, files) {
  const byPath = new Map(files.map((f) => [f.path, f]));
  let html = entryFile.content;
  // Inlining raw TypeScript/JSX as if it were plain JS doesn't error loudly
  // — the browser just throws a silent syntax error in the console and the
  // page renders blank (e.g. an empty <div id="root">). Detect this instead
  // of producing that mystery-blank-page experience.
  let unsupportedScript = null;

  html = html.replace(/<link\b[^>]*rel=["']stylesheet["'][^>]*href=["']([^"']+)["'][^>]*>/gi, (match, href) => {
    const resolved = resolveRelative(entryFile.path, href);
    const cssFile = resolved && byPath.get(resolved);
    return cssFile ? '<style>\n' + cssFile.content + '\n</style>' : match;
  });

  html = html.replace(/<script\b([^>]*)\ssrc=["']([^"']+)["']([^>]*)><\/script>/gi, (match, before, src) => {
    const resolved = resolveRelative(entryFile.path, src);
    if (!resolved) return match; // external/remote — leave alone
    if (/\.(ts|tsx|jsx|vue|svelte)$/i.test(resolved)) {
      unsupportedScript = resolved;
      return match;
    }
    const jsFile = byPath.get(resolved);
    return jsFile ? '<script>\n' + jsFile.content + '\n</script>' : match;
  });

  return { html, unsupportedScript };
}

// ─── GET /api/codegen/:codeGenId/preview ──────────────────────────────────────
router.get('/:codeGenId/preview', requireAuth, async (req, res) => {
  const job = await getJob(req.params.codeGenId);
  if (!job) return res.status(404).json({ error: 'Code gen job not found' });
  if (job.status !== 'done') return res.status(400).json({ error: 'Code generation must finish before opening the demo sandbox' });

  const entry = findFrontendEntry(job.files);
  if (!entry) return res.status(400).json({ error: 'No frontend (.html) file found in this module' });

  const { html, unsupportedScript } = buildStaticPreview(entry, job.files);
  if (unsupportedScript) {
    return res.status(400).json({
      error: `This frontend's entry point loads ${unsupportedScript}, which needs a real TypeScript/JSX build step — a static preview can only run plain HTML/CSS/JS, so it can't render this without actually building the app.`,
    });
  }

  res.json({ html, entryPath: entry.path });
});

// A directly runnable, authenticated UAT deployment of the generated HTML
// artifact. It remains strongly sandboxed and cannot call the factory API,
// read cookies, submit forms, or navigate its opener.
router.get('/:codeGenId/uat', requireAuth, async (req, res) => {
  const job = await getJob(req.params.codeGenId);
  if (!job) return res.status(404).send('Code gen job not found');
  if (job.status !== 'done') return res.status(400).send('Code generation must finish before opening the demo sandbox');
  const entry = findFrontendEntry(job.files);
  if (!entry) return res.status(400).send('No generated HTML entry point is available');
  const { html, unsupportedScript } = buildStaticPreview(entry, job.files);
  if (unsupportedScript) return res.status(400).send(`A build is required for ${unsupportedScript}; no deployable browser artifact was generated.`);
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  res.setHeader('Content-Security-Policy', "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' https://unpkg.com; style-src 'unsafe-inline' https:; img-src data: https:; font-src https:; connect-src 'none'");
  res.setHeader('Cache-Control', 'no-store');
  res.send(html);
});

// ─── POST /api/codegen/:artifactId/project-demo ───────────────────────────────
// Synthesizes ONE combined demo from every department's actual generated
// code, once all of them have finished. Distinct from any single
// department's own /uat — that only ever shows one module's own file, even
// after hasFrontend was restricted to just the frontend owner. This reads
// every department's real file list (not just its original one-line
// objective) and, if the frontend owner produced a working sandbox file,
// uses it as a concrete starting point. Result is cached on the artifact
// (an LLM call, not something to redo on every click) until explicitly
// regenerated.
router.post('/:artifactId/project-demo', requireAuth, async (req, res) => {
  const actor = req.session.user;
  if (actor.isClient) return res.status(403).json({ error: 'Clients cannot run this step' });

  const artifact = await Artifact.findById(req.params.artifactId).select('teamReports approvalChain originator title projectDemo');
  if (!artifact) return res.status(404).json({ error: 'Artifact not found' });

  const departments = (artifact.teamReports || []).map((t) => t.team);
  if (!departments.length) return res.status(400).json({ error: 'This requirement has no department packages yet' });

  const isOriginator = artifact.originator.userId === actor.id;
  // Checking only approvalChain[0] excluded VP from nearly every real
  // artifact: VP is gate 1 (after MD/CEO/TL/client's own gate 0), not gate
  // 0, for every originator except PM. Check the whole chain, not just its
  // first step, so anyone who's a real approver anywhere in it — including
  // VP on the far more common gate-1 case — can act here.
  const isReviewer = artifact.approvalChain.some((step) => step.approverTiers.includes(actor.tierId));
  const isInvolvedTl = actor.tierId === 'tl' && actor.department && departments.includes(actor.department);
  if (!isOriginator && !isReviewer && !isInvolvedTl) {
    return res.status(403).json({ error: 'You are not authorized to run this step' });
  }

  const latestByDept = await getJobsForArtifact(req.params.artifactId);
  const missing = departments.filter((d) => {
    const job = latestByDept.get(d);
    return !job || job.status !== 'done';
  });
  if (missing.length) {
    return res.status(400).json({ error: 'All department modules must be fully generated first — still waiting on: ' + missing.join(', ') });
  }
  // Generated isn't the same as vetted — this demo represents a finished,
  // signed-off product, so every department's own TL has to have actually
  // approved and self-tested it first, not just have code that exists.
  const unapproved = departments.filter((d) => {
    const job = latestByDept.get(d);
    return !job.tlApproved || !job.codeReview;
  });
  if (unapproved.length) {
    return res.status(400).json({ error: 'Every department must be approved and self-tested first — still waiting on: ' + unapproved.join(', ') });
  }

  const demoInput = departments.map((department) => {
    const job = latestByDept.get(department);
    const teamReport = (artifact.teamReports || []).find((t) => t.team === department) || {};
    const entry = department === FRONTEND_OWNING_DEPARTMENT ? findFrontendEntry(job.files) : null;
    return {
      team: department,
      objective: teamReport.objective,
      files: job.files.map((f) => ({ path: f.path, description: f.description })),
      referenceHtml: entry ? entry.content : undefined,
    };
  });

  // Defaults to whichever model the frontend-owning department was actually
  // generated with, but the caller can override — the department code
  // itself stays exactly as generated regardless of which model drives
  // *this* synthesis step, so a TL can pick a more reliable model here
  // without having to regenerate any department's actual code.
  const requestedModel = req.body && req.body.model;
  const demoModel = ALLOWED_MODELS.has(requestedModel) ? requestedModel : (latestByDept.get(FRONTEND_OWNING_DEPARTMENT) || {}).model;

  try {
    const result = await runProjectDemoSynthesis({ title: artifact.title, departments: demoInput, model: demoModel });
    if (!result || !result.content) throw new Error('The model returned no content');
    artifact.projectDemo = { html: result.content, generatedAt: Date.now() };
    await artifact.save();
    res.json({ generatedAt: artifact.projectDemo.generatedAt, model: demoModel, modelLabel: CODE_GEN_MODELS[demoModel] });
  } catch (err) {
    res.status(502).json({ error: err.message });
  }
});

// A directly runnable, authenticated deployment of the synthesized project
// demo — same sandboxing posture as /:codeGenId/uat above.
router.get('/:artifactId/project-demo', requireAuth, async (req, res) => {
  const artifact = await Artifact.findById(req.params.artifactId).select('projectDemo');
  if (!artifact) return res.status(404).send('Artifact not found');
  if (!artifact.projectDemo || !artifact.projectDemo.html) {
    return res.status(400).send('No project demo has been generated yet for this requirement');
  }
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  res.setHeader('Content-Security-Policy', "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' https://unpkg.com; style-src 'unsafe-inline' https:; img-src data: https:; font-src https:; connect-src 'none'");
  res.setHeader('Cache-Control', 'no-store');
  res.send(artifact.projectDemo.html);
});

// ─── GET /api/codegen/by-artifact/:artifactId ─────────────────────────────────
// Cross-department code-gen status for VPs and Team Leads — one entry per
// department that has a team package, with progress % and a codeGenId to
// open in the IDE (view-only for a department that isn't theirs). This is
// operational status only (department, model, % done) — never the team
// package's actual content, which stays restricted the way it already was.
router.get('/by-artifact/:artifactId', requireAuth, async (req, res) => {
  const actor = req.session.user;
  if (actor.isClient || (actor.tierId !== 'vp' && actor.tierId !== 'tl')) {
    return res.status(403).json({ error: 'Only VPs and Team Leads can view code generation status' });
  }

  const artifact = await Artifact.findById(req.params.artifactId).select('teamReports projectDemo');
  if (!artifact) return res.status(404).json({ error: 'Artifact not found' });

  const departments = (artifact.teamReports || []).map((t) => t.team);
  const latestByDept = await getJobsForArtifact(req.params.artifactId);

  const modules = departments.map((department) => {
    const job = latestByDept.get(department);
    if (!job) return { department, status: 'not_started', progressPct: 0 };

    const filesDone = job.files.filter((f) => f.done).length;
    const progressPct = job.status === 'done'
      ? 100
      : (job.totalFiles > 0 ? Math.round((filesDone / job.totalFiles) * 100) : 0);
    return {
      department,
      codeGenId: job.codeGenId,
      status: job.status,
      model: job.model,
      modelLabel: job.modelLabel,
      totalFiles: job.totalFiles,
      filesDone,
      progressPct,
      createdAt: job.createdAt,
      security: job.security || null,
      reviewedAt: job.reviewedAt || null,
      ciAdded: !!job.ciAdded,
      ciExecution: job.ciExecution || null,
      tlApproved: !!job.tlApproved,
      tlApprovedAt: job.tlApprovedAt || null,
      tlApprovedBy: job.tlApprovedBy || null,
      codeReview: job.codeReview || null,
      codeReviewedAt: job.codeReviewedAt || null,
      testExecution: job.testExecution || null,
      securityFixRunning: activeSecurityFixes.has(job.codeGenId),
      // Restricted to the one department actually instructed to build the
      // whole-project sandbox demo (see FRONTEND_OWNING_DEPARTMENT in
      // llm.service.js) — any other department's own incidental HTML file
      // (an AI department generating its own throwaway dashboard, say)
      // isn't a project demo and showing a UI Demo button for it just reads
      // as "there are two conflicting demos," not "here's the app."
      hasFrontend: job.department === FRONTEND_OWNING_DEPARTMENT && job.status === 'done' && !!findFrontendEntry(job.files),
    };
  });

  res.json({
    modules,
    allDone: modules.length > 0 && modules.every((m) => m.status === 'done'),
    projectDemoReady: !!(artifact.projectDemo && artifact.projectDemo.html),
    projectDemoGeneratedAt: (artifact.projectDemo && artifact.projectDemo.generatedAt) || null,
  });
});

// ─── GET /api/codegen/models ──────────────────────────────────────────────────
// Returns the list of available code-gen models.
router.get('/models', requireAuth, (req, res) => {
  res.json({
    models: Object.entries(CODE_GEN_MODELS).map(([id, label]) => ({
      id,
      label,
      isDefault: id === DEFAULT_CODE_GEN_MODEL,
    })),
  });
});

module.exports = router;
