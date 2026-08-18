const mongoose = require('mongoose');

const { Schema } = mongoose;

const historyEntrySchema = new Schema(
  {
    stage: String,
    actorId: String,
    actorTier: String,
    action: String,
    comment: String,
    timestamp: { type: Date, default: Date.now },
  },
  { _id: false }
);

const approvalStepSchema = new Schema(
  {
    approverTiers: [String],
    mode: { type: String, enum: ['any', 'all'], default: 'any' },
    approvedBy: [
      {
        userId: String,
        tierId: String,
        timestamp: Date,
      },
    ],
  },
  { _id: false }
);

const chatMessageSchema = new Schema(
  {
    role: { type: String, enum: ['user', 'assistant'], required: true },
    content: String,
    timestamp: { type: Date, default: Date.now },
  },
  { _id: false }
);

const artifactSchema = new Schema(
  {
    type: { type: String, enum: ['idea', 'fsd'], required: true },
    title: { type: String, required: true },
    content: Schema.Types.Mixed,
    originator: {
      userId: { type: String, required: true },
      tierId: { type: String, default: null }, // null = external client
    },
    currentStage: {
      type: String,
      enum: ['draft', 'clarifying', 'pending_approval', 'approved', 'rejected', 'revision_requested'],
      default: 'draft',
    },
    approvalChain: [approvalStepSchema],
    currentApprovalIndex: { type: Number, default: 0 },
    history: [historyEntrySchema],
    chatHistory: [chatMessageSchema],
  },
  { timestamps: true }
);

module.exports = mongoose.model('Artifact', artifactSchema);
