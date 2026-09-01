require('dotenv').config();
const express = require('express');
const session = require('express-session');
const cors = require('cors');

const connectDB = require('./db/connect');
const { seedDemoUsers } = require('./seed');
const authRoutes = require('./routes/auth.routes');
const codegenRoutes = require('./routes/codegen.routes');


const app = express();

const FRONTEND_ORIGIN = process.env.FRONTEND_ORIGIN || 'http://localhost:5173';
const allowedOrigins = FRONTEND_ORIGIN.split(',').map(s => s.trim());

app.use(cors({
  origin: (origin, callback) => {
    if (!origin || allowedOrigins.includes(origin) || /^http:\/\/(localhost|127\.0\.0\.1):\d+$/.test(origin)) {
      callback(null, true);
    } else {
      callback(new Error('Not allowed by CORS'));
    }
  },
  credentials: true
}));
app.use(express.json());
app.use(
  session({
    secret: process.env.SESSION_SECRET || 'dev-secret-change-me',
    resave: false,
    saveUninitialized: false,
    cookie: { maxAge: 1000 * 60 * 60 * 8 },
  })
);

app.use('/api/auth', authRoutes);
// /api/artifacts is served by the Python agent service — see
// ../../agent-service. Express retains auth (code generation depends on
// the session) and /api/codegen, which has not been migrated.
app.use('/api/codegen', codegenRoutes);

app.get('/', (req, res) => res.json({ ok: true, service: 'ai-software-factory backend', frontend: FRONTEND_ORIGIN }));

const PORT = process.env.PORT || 4000;

connectDB()
  .then(seedDemoUsers)
  .then(() => {
    app.listen(PORT, () => console.log(`[server] backend API listening on http://localhost:${PORT} (allowing ${FRONTEND_ORIGIN})`));
  })
  .catch((err) => {
    console.error('[server] failed to start', err);
    process.exit(1);
  });
