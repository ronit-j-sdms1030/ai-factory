const express = require('express');
const { randomUUID } = require('crypto');
const Artifact = require('../models/artifact.model');
const CodeGenJob = require('../models/codeGenJob.model');
const { requireAuth } = require('../middleware/auth.middleware');
const { runCodeGen, CODE_GEN_MODELS, DEFAULT_CODE_GEN_MODEL, runCodeReview, runSecurityAutoFix } = require('../services/llm.service');
const { runSecurityScan } = require('../services/securityScanner.service');

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
        tlApproved: job.tlApproved,
        tlApprovedAt: job.tlApprovedAt,
        tlApprovedBy: job.tlApprovedBy,
        codeReview: job.codeReview,
        codeReviewedAt: job.codeReviewedAt,
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
    tlApproved: !!doc.tlApproved,
    tlApprovedAt: doc.tlApprovedAt || null,
    tlApprovedBy: doc.tlApprovedBy || null,
    codeReview: doc.codeReview || null,
    codeReviewedAt: doc.codeReviewedAt || null,
  };
}

// Cache-first lookup for a single job, falling back to (and rehydrating
// from) Mongo — covers the case where this process restarted since the job
// last ran.
async function getJob(codeGenId) {
  const cached = codeGenJobs.get(codeGenId);
  if (cached) return cached;

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
    tlApproved: false,
    tlApprovedAt: null,
    tlApprovedBy: null,
    codeReview: null,
    codeReviewedAt: null,
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
        onFileProgress: ({ index, total, path: filePath, status }) => {
          job.totalFiles = total;
          if (status === 'generating') {
            // Add a pending slot so the file tree shows it immediately
            if (!job.files.find((f) => f.path === filePath)) {
              job.files.push({ path: filePath, description: '', content: '', done: false });
            }
            job.log.push({ ts: Date.now(), msg: `Generating ${filePath} (${index + 1}/${total})…` });
          } else if (status === 'done') {
            // Content will be overwritten below from the returned array
            const existing = job.files.find((f) => f.path === filePath);
            if (existing) existing.done = true;
            job.log.push({ ts: Date.now(), msg: `✓ ${filePath}` });
            // Write through per file, not just at the end — a restart
            // mid-generation should lose at most the one file in flight,
            // not everything generated so far.
            persistJob(job);
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
        // what the manual "Run CI/CD & tests" project-wide step is for).
        // If it finds something, attempt one fix pass and re-scan to
        // confirm, rather than just reporting and stopping there.
        try {
          job.log.push({ ts: Date.now(), msg: 'Running automatic security scan…' });
          await persistJob(job);

          let security = await runSecurityScan({ department: job.department, artifactId: job.artifactId, files: job.files });
          if (security.verdict === 'needs_fixes' && security.findings.length) {
            job.log.push({ ts: Date.now(), msg: `Security scan found ${security.findings.length} issue(s) — applying fixes…` });
            await persistJob(job);

            const fixedFiles = await runSecurityAutoFix({ department: job.department, files: job.files, findings: security.findings });
            for (const fixed of fixedFiles) {
              const existing = job.files.find((f) => f.path === fixed.path);
              if (existing) existing.content = fixed.content;
            }
            if (fixedFiles.length) {
              job.log.push({ ts: Date.now(), msg: `Fixed ${fixedFiles.length} file(s) — re-scanning…` });
              await persistJob(job);
              security = await runSecurityScan({ department: job.department, artifactId: job.artifactId, files: job.files });
            }
          }
          job.security = security;
          job.reviewedAt = Date.now();
          job.log.push({
            ts: Date.now(),
            msg: security.verdict === 'pass' ? '✓ Security scan passed.' : `⚠ Security scan still flags ${security.findings.length} issue(s) after fixing.`,
          });
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

  // Send file stubs (path + done flag) but NOT content — the UI fetches full
  // content of the selected file separately via /file to keep status payloads small.
  res.json({
    status: job.status,
    model: job.model,
    modelLabel: job.modelLabel,
    totalFiles: job.totalFiles,
    files: job.files.map((f) => ({ path: f.path, description: f.description, done: f.done })),
    log: job.log,
    security: job.security || null,
    reviewedAt: job.reviewedAt || null,
    ciAdded: !!job.ciAdded,
    tlApproved: !!job.tlApproved,
    tlApprovedAt: job.tlApprovedAt || null,
    tlApprovedBy: job.tlApprovedBy || null,
    codeReview: job.codeReview || null,
    codeReviewedAt: job.codeReviewedAt || null,
  });
});

// ─── GET /api/codegen/:codeGenId/file ─────────────────────────────────────────
// Returns the content of one file from the job (passed as ?path= query param).
router.get('/:codeGenId/file', requireAuth, async (req, res) => {
  const job = await getJob(req.params.codeGenId);
  if (!job) return res.status(404).json({ error: 'Code gen job not found' });

  const filePath = req.query.path;
  const file = job.files.find((f) => f.path === filePath);
  if (!file) return res.status(404).json({ error: 'File not found in this job' });

  res.json({ path: file.path, content: file.content, done: file.done });
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
  const isReviewer = artifact.approvalChain[0].approverTiers.includes(actor.tierId);
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
      const security = await runSecurityScan({ department, artifactId: job.artifactId, files: job.files });
      job.security = security;
      job.reviewedAt = Date.now();
      codeGenJobs.set(job.codeGenId, job);
      await persistJob(job);
      return { department, ciAdded, security };
    } catch (err) {
      return { department, ciAdded, error: err.message };
    }
  }));

  res.json({ results });
});

// ─── POST /api/codegen/:codeGenId/approve ─────────────────────────────────────
// TL-only gate: the owning department's TL signs off on the CI/CD result
// before self-testing is allowed to run. Requires the CI/CD stage to have
// actually completed first (a file added, a security scan on record).
router.post('/:codeGenId/approve', requireAuth, async (req, res) => {
  const actor = req.session.user;
  const job = await getJob(req.params.codeGenId);
  if (!job) return res.status(404).json({ error: 'Code gen job not found' });

  if (actor.isClient || actor.tierId !== 'tl' || actor.department !== job.department) {
    return res.status(403).json({ error: 'Only this department\'s own Team Lead can approve this module' });
  }
  if (!job.ciAdded || !job.security) {
    return res.status(400).json({ error: 'Run CI/CD for this module before approving it' });
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

  const job = await getJob(req.params.codeGenId);
  if (!job) return res.status(404).json({ error: 'Code gen job not found' });
  if (!job.tlApproved) {
    return res.status(400).json({ error: 'This module needs its Team Lead\'s approval before self-testing can begin' });
  }

  try {
    const codeReview = await runCodeReview({ department: job.department, files: job.files });
    job.codeReview = codeReview;
    job.codeReviewedAt = Date.now();
    job.log.push({
      ts: Date.now(),
      msg: codeReview.verdict === 'pass' ? '✓ Self AI testing passed.' : `⚠ Self AI testing flagged ${codeReview.findings.length} issue(s).`,
    });
    codeGenJobs.set(job.codeGenId, job);
    await persistJob(job);
    res.json({ codeReview });
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

  html = html.replace(/<link\b[^>]*rel=["']stylesheet["'][^>]*href=["']([^"']+)["'][^>]*>/gi, (match, href) => {
    const resolved = resolveRelative(entryFile.path, href);
    const cssFile = resolved && byPath.get(resolved);
    return cssFile ? '<style>\n' + cssFile.content + '\n</style>' : match;
  });

  html = html.replace(/<script\b([^>]*)\ssrc=["']([^"']+)["']([^>]*)><\/script>/gi, (match, before, src) => {
    const resolved = resolveRelative(entryFile.path, src);
    const jsFile = resolved && byPath.get(resolved);
    return jsFile ? '<script>\n' + jsFile.content + '\n</script>' : match;
  });

  return html;
}

// ─── GET /api/codegen/:codeGenId/preview ──────────────────────────────────────
router.get('/:codeGenId/preview', requireAuth, async (req, res) => {
  const job = await getJob(req.params.codeGenId);
  if (!job) return res.status(404).json({ error: 'Code gen job not found' });
  if (!job.codeReview) {
    return res.status(400).json({ error: 'Run self AI testing for this module before opening the UAT sandbox' });
  }

  const entry = findFrontendEntry(job.files);
  if (!entry) return res.status(400).json({ error: 'No frontend (.html) file found in this module' });

  res.json({ html: buildStaticPreview(entry, job.files), entryPath: entry.path });
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

  const artifact = await Artifact.findById(req.params.artifactId).select('teamReports');
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
      tlApproved: !!job.tlApproved,
      tlApprovedAt: job.tlApprovedAt || null,
      tlApprovedBy: job.tlApprovedBy || null,
      codeReview: job.codeReview || null,
      codeReviewedAt: job.codeReviewedAt || null,
      hasFrontend: job.status === 'done' && !!findFrontendEntry(job.files),
    };
  });

  res.json({ modules, allDone: modules.length > 0 && modules.every((m) => m.status === 'done') });
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
