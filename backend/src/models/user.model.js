const mongoose = require('mongoose');

const { Schema } = mongoose;

const userSchema = new Schema(
  {
    name: { type: String, required: true },
    email: { type: String, required: true, unique: true, lowercase: true, trim: true },
    passwordHash: { type: String, required: true },
    tierId: { type: String, default: null }, // 'md' | 'ceo' | 'vp' | 'pm' | 'tl'; null for clients
    isClient: { type: Boolean, default: false },
    // Only meaningful for tierId: 'tl' — which department's work queue this
    // TL sees, e.g. 'AI', 'Development'. One of TEAM_DEPARTMENTS.
    department: { type: String, default: null },
  },
  { timestamps: true }
);

module.exports = mongoose.model('User', userSchema);
