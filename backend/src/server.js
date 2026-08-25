require('dotenv').config();
const express = require('express');
const session = require('express-session');
const cors = require('cors');

const connectDB = require('./db/connect');
const { seedDemoUsers } = require('./seed');
const authRoutes = require('./routes/auth.routes');
const artifactRoutes = require('./routes/artifact.routes');
const codegenRoutes = require('./routes/codegen.routes');


const app = express();

const FRONTEND_ORIGIN = process.env.FRONTEND_ORIGIN || 'http://localhost:5173';

app.use(cors({ origin: FRONTEND_ORIGIN, credentials: true }));
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
app.use('/api/artifacts', artifactRoutes);
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
