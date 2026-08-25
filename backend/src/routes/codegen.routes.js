const express = require('express');
const { randomUUID } = require('crypto');
const Artifact = require('../models/artifact.model');
const { requireAuth } = require('../middleware/auth.middleware');
const { runCodeGen, CODE_GEN_MODELS, DEFAULT_CODE_GEN_MODEL } = require('../services/llm.service');

const router = express.Router();

// ─── In-memory job store ───────────────────────────────────────────────────────
// Key: codeGenId (UUID)
// Value: { artifactId, department, model, modelLabel, status, files, log, totalFiles, createdAt }
// status: 'generating' | 'done' | 'error'

const codeGenJobs = new Map();

// Clean up jobs older than 30 minutes
setInterval(() => {
  const cutoff = Date.now() - 30 * 60 * 1000;
  for (const [id, job] of codeGenJobs.entries()) {
    if (job.createdAt < cutoff) codeGenJobs.delete(id);
  }
}, 5 * 60 * 1000);

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

  const requestedModel = req.body && req.body.model;
  const model = ALLOWED_MODELS.has(requestedModel) ? requestedModel : DEFAULT_CODE_GEN_MODEL;

  const codeGenId = randomUUID();
  const job = {
    artifactId: artifact._id.toString(),
    department: actor.department,
    model,
    modelLabel: CODE_GEN_MODELS[model],
    status: 'generating',
    files: [],          // { path, description, content, done }
    filePlan: [],       // { path, description } — populated as soon as planning finishes
    log: [],
    totalFiles: 0,
    createdAt: Date.now(),
  };
  codeGenJobs.set(codeGenId, job);

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
          }
        },
      }).then((result) => {
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
      });
    } catch (err) {
      job.status = 'error';
      job.log.push({ ts: Date.now(), msg: `Error: ${err.message}` });
    }
  })();

  res.status(202).json({ codeGenId, model, modelLabel: CODE_GEN_MODELS[model] });
});

// ─── GET /api/codegen/:codeGenId/status ───────────────────────────────────────
// Returns the current job state for polling.
router.get('/:codeGenId/status', requireAuth, (req, res) => {
  const job = codeGenJobs.get(req.params.codeGenId);
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
  });
});

// ─── GET /api/codegen/:codeGenId/file ─────────────────────────────────────────
// Returns the content of one file from the job (passed as ?path= query param).
router.get('/:codeGenId/file', requireAuth, (req, res) => {
  const job = codeGenJobs.get(req.params.codeGenId);
  if (!job) return res.status(404).json({ error: 'Code gen job not found' });

  const filePath = req.query.path;
  const file = job.files.find((f) => f.path === filePath);
  if (!file) return res.status(404).json({ error: 'File not found in this job' });

  res.json({ path: file.path, content: file.content, done: file.done });
});

// ─── GET /api/codegen/:codeGenId/download ─────────────────────────────────────
// Streams a zip archive of all completed files.
router.get('/:codeGenId/download', requireAuth, async (req, res) => {
  const job = codeGenJobs.get(req.params.codeGenId);
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
