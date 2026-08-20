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

const teamReportSchema = new Schema(
  {
    team: String,
    objective: String,
    tasks: [String],
    relevantDataEntities: [String],
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
      enum: [
        'draft',
        'clarifying',
        'pending_approval',
        'approved',
        'rejected',
        'revision_requested',
        // Client flow only: the gate-0 approver (MD/CEO) proposed their own
        // edit to the summary instead of asking the client to redo it —
        // waiting on the client to accept it.
        'pending_client_review',
        // Client flow only: detailed report/FSD generated after the MD/CEO
        // gate — sitting with MD/CEO to review/edit before it goes to the
        // client. VP's gate does not open until this loop completes.
        'fsd_review',
        'fsd_pending_client',
        // Client approved the FSD — MD/CEO gives a final signoff before the
        // chain proceeds to VP.
        'fsd_final_approval',
      ],
      default: 'draft',
    },
    approvalChain: [approvalStepSchema],
    currentApprovalIndex: { type: Number, default: 0 },
    history: [historyEntrySchema],
    chatHistory: [chatMessageSchema],
    // Generated once an MD/CEO-level gate clears — a fuller BRD-style
    // expansion of `content`, produced from the finished intake conversation.
    detailedReport: Schema.Types.Mixed,
    detailedReportGeneratedAt: Date,
    // Chat-style edit history for the detailed report — MD/CEO (during
    // fsd_review) or the client (during fsd_pending_client) describe a
    // change in plain language and the AI applies it.
    fsdChatHistory: [chatMessageSchema],
    // Generated once the approval chain is fully cleared — the detailed
    // report split into per-discipline work packages for team leads.
    teamReports: [teamReportSchema],
    teamReportsGeneratedAt: Date,
    // Any internal role with access can share this report with another
    // internal colleague for discussion, even if the recipient has no
    // package here — this grants that visibility explicitly.
    discussionRecipients: [String], // userIds
    discussionShares: [
      {
        fromUserId: String,
        fromName: String,
        toUserId: String,
        toName: String,
        note: String,
        timestamp: { type: Date, default: Date.now },
      },
    ],
  },
  { timestamps: true }
);

module.exports = mongoose.model('Artifact', artifactSchema);
