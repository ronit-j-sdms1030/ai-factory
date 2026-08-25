const mongoose = require('mongoose');

const { Schema } = mongoose;

// Persists what used to live only in the codegen routes' in-memory Map —
// every backend restart wiped that Map, silently discarding real generated
// code (and any CI/CD file + security/code review already run on it), even
// though the whole point of code-gen is to produce something durable.
const codeGenFileSchema = new Schema(
  {
    path: String,
    description: String,
    content: String,
    done: Boolean,
  },
  { _id: false }
);

const codeGenLogEntrySchema = new Schema(
  {
    ts: Number,
    msg: String,
  },
  { _id: false }
);

const codeGenJobSchema = new Schema({
  codeGenId: { type: String, required: true, unique: true, index: true },
  artifactId: { type: String, required: true, index: true },
  department: { type: String, required: true },
  model: String,
  modelLabel: String,
  status: { type: String, enum: ['generating', 'done', 'error'], default: 'generating' },
  files: [codeGenFileSchema],
  log: [codeGenLogEntrySchema],
  totalFiles: { type: Number, default: 0 },
  createdAt: { type: Number, default: () => Date.now() },
  security: Schema.Types.Mixed,
  reviewedAt: Number,
  // CI/CD stage — file added + a final security pass over it too (a CI
  // workflow file can itself have findings, e.g. an unpinned Action ref).
  ciAdded: Boolean,
  // Gate: the owning TL signs off on the security + CI/CD result before
  // self-testing is allowed to run.
  tlApproved: Boolean,
  tlApprovedAt: Number,
  tlApprovedBy: String,
  // Self AI testing — the same code-quality review as before, just moved
  // behind the TL-approval gate as its own explicit stage.
  codeReview: Schema.Types.Mixed,
  codeReviewedAt: Number,
});

module.exports = mongoose.model('CodeGenJob', codeGenJobSchema);
