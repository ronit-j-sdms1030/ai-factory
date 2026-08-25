// Real static-analysis tooling for generated code — Semgrep OSS (local venv,
// no network dependency at scan time) and SonarQube Community Edition (a
// dedicated local instance, see backend/security-tools/docker-compose.yml).
// Both operate on files written to a real temp directory on disk; neither
// installs the generated project's own dependencies or executes its code.
const { execFile } = require('child_process');
const { promisify } = require('util');
const fs = require('fs/promises');
const os = require('os');
const path = require('path');
const { randomUUID } = require('crypto');

const execFileAsync = promisify(execFile);

const SEMGREP_BIN = path.join(__dirname, '..', '..', '.semgrep-venv', 'bin', 'semgrep');
const SONARQUBE_URL = process.env.SONARQUBE_URL || 'http://localhost:9000';
const SONARQUBE_TOKEN = process.env.SONARQUBE_TOKEN;

async function writeFilesToTempDir(files) {
  const dir = path.join(os.tmpdir(), 'ai-factory-scan-' + randomUUID());
  for (const file of files) {
    if (!file.content) continue;
    const filePath = path.join(dir, file.path);
    await fs.mkdir(path.dirname(filePath), { recursive: true });
    await fs.writeFile(filePath, file.content, 'utf8');
  }
  return dir;
}

async function cleanupTempDir(dir) {
  try {
    await fs.rm(dir, { recursive: true, force: true });
  } catch (err) {
    // best-effort — a leftover temp dir is not worth failing the scan over
  }
}

// ─── Semgrep OSS ────────────────────────────────────────────────────────────
// Official, free Semgrep Registry rulesets — no login/API key required.
const SEMGREP_CONFIGS = ['p/security-audit', 'p/secrets', 'p/owasp-top-ten'];

function mapSemgrepSeverity(sev) {
  if (sev === 'ERROR') return 'high';
  if (sev === 'WARNING') return 'medium';
  return 'low';
}

async function runSemgrepScan(dir) {
  const args = ['--json', '--quiet', '--timeout', '60'];
  SEMGREP_CONFIGS.forEach((c) => args.push('--config', c));
  args.push(dir);

  let stdout;
  try {
    const result = await execFileAsync(SEMGREP_BIN, args, { maxBuffer: 20 * 1024 * 1024, timeout: 90000 });
    stdout = result.stdout;
  } catch (err) {
    // Semgrep exits non-zero when a finding is marked "blocking" — the real
    // JSON output is still on stdout in that case, so only re-throw if it's
    // genuinely missing (a crash, a bad config, a timeout).
    if (err.stdout) stdout = err.stdout;
    else throw err;
  }

  const parsed = JSON.parse(stdout);
  return (parsed.results || []).map((r) => ({
    tool: 'semgrep',
    category: /secret/i.test(r.check_id) ? 'secrets' : 'sast',
    file: path.relative(dir, r.path),
    issue: (r.extra && r.extra.message) || r.check_id,
    severity: mapSemgrepSeverity(r.extra && r.extra.severity),
  }));
}

// ─── SonarQube Community Edition ────────────────────────────────────────────
function mapSonarSeverity(sev) {
  if (sev === 'BLOCKER' || sev === 'CRITICAL') return 'critical';
  if (sev === 'MAJOR') return 'high';
  if (sev === 'MINOR') return 'medium';
  return 'low';
}

function sonarComponentPath(component) {
  // Component keys look like "<projectKey>:<relative/path>" — strip the
  // project key prefix so the UI shows a plain, familiar file path.
  const idx = component.indexOf(':');
  return idx === -1 ? component : component.slice(idx + 1);
}

async function runSonarScan({ dir, projectKey }) {
  if (!SONARQUBE_TOKEN) return []; // no local instance configured — skip, don't fail the whole scan

  await execFileAsync('docker', [
    'run', '--rm', '--network', 'host',
    '-v', `${dir}:/usr/src`,
    'sonarsource/sonar-scanner-cli',
    '-Dsonar.host.url=' + SONARQUBE_URL,
    '-Dsonar.token=' + SONARQUBE_TOKEN,
    '-Dsonar.projectKey=' + projectKey,
    '-Dsonar.sources=.',
    '-Dsonar.sourceEncoding=UTF-8',
    // Without this, the scanner writes .scannerwork (and report-task.txt,
    // which is how we find the analysis task to poll) to /tmp INSIDE the
    // container — invisible on the host once --rm removes it. Forcing it
    // under the bind-mounted /usr/src makes it land back on the host dir.
    '-Dsonar.working.directory=/usr/src/.scannerwork',
  ], { timeout: 180000, maxBuffer: 20 * 1024 * 1024 });

  // The scanner writes the background-task id it just submitted here —
  // more reliable than scraping its log output for the same information.
  const reportTaskPath = path.join(dir, '.scannerwork', 'report-task.txt');
  const reportTaskRaw = await fs.readFile(reportTaskPath, 'utf8');
  const ceTaskIdMatch = reportTaskRaw.match(/^ceTaskId=(.+)$/m);
  if (!ceTaskIdMatch) return [];
  const taskId = ceTaskIdMatch[1].trim();

  const authHeader = 'Basic ' + Buffer.from(SONARQUBE_TOKEN + ':').toString('base64');

  // SonarQube processes the uploaded report asynchronously — poll until
  // that background task finishes before the issues it produced exist to query.
  for (let i = 0; i < 30; i++) {
    const res = await fetch(SONARQUBE_URL + '/api/ce/task?id=' + taskId, { headers: { Authorization: authHeader } });
    const data = await res.json();
    if (['SUCCESS', 'FAILED', 'CANCELED'].includes(data.task.status)) break;
    await new Promise((resolve) => setTimeout(resolve, 3000));
  }

  const [issuesRes, hotspotsRes] = await Promise.all([
    fetch(SONARQUBE_URL + '/api/issues/search?componentKeys=' + encodeURIComponent(projectKey), { headers: { Authorization: authHeader } }),
    fetch(SONARQUBE_URL + '/api/hotspots/search?projectKey=' + encodeURIComponent(projectKey), { headers: { Authorization: authHeader } }),
  ]);
  const issuesData = await issuesRes.json();
  const hotspotsData = await hotspotsRes.json();

  const issueFindings = (issuesData.issues || []).map((i) => ({
    tool: 'sonarqube',
    category: 'sast',
    file: sonarComponentPath(i.component || ''),
    issue: i.message,
    severity: mapSonarSeverity(i.severity),
  }));
  const hotspotFindings = (hotspotsData.hotspots || []).map((h) => ({
    tool: 'sonarqube',
    category: 'secrets',
    file: sonarComponentPath(h.component || ''),
    issue: h.message,
    severity: h.vulnerabilityProbability === 'HIGH' ? 'high' : h.vulnerabilityProbability === 'MEDIUM' ? 'medium' : 'low',
  }));
  return issueFindings.concat(hotspotFindings);
}

// ─── Combined entry point ───────────────────────────────────────────────────
// Deterministic summary (no LLM call) — the findings themselves are real
// tool output, so no AI-generated framing of them is needed here. AI only
// re-enters at the auto-fix stage, patching what these tools actually found.
async function runSecurityScan({ department, artifactId, files }) {
  const dir = await writeFilesToTempDir(files);
  const projectKey = 'codegen-' + artifactId + '-' + department.toLowerCase().replace(/[^a-z0-9]+/g, '-');

  try {
    const [semgrepFindings, sonarFindings] = await Promise.all([
      runSemgrepScan(dir).catch((err) => { console.error('[security-scan] semgrep failed:', err.message); return null; }),
      runSonarScan({ dir, projectKey }).catch((err) => { console.error('[security-scan] sonarqube failed:', err.message); return null; }),
    ]);

    const toolsRun = ['Semgrep'];
    if (SONARQUBE_TOKEN) toolsRun.push('SonarQube');

    const findings = (semgrepFindings || []).concat(sonarFindings || []);
    const failedTools = [];
    if (semgrepFindings === null) failedTools.push('Semgrep');
    if (SONARQUBE_TOKEN && sonarFindings === null) failedTools.push('SonarQube');

    const highOrAbove = findings.filter((f) => f.severity === 'high' || f.severity === 'critical').length;
    let summary = findings.length
      ? `${findings.length} finding(s) from ${toolsRun.join(' + ')} (${highOrAbove} high/critical).`
      : `No issues found by ${toolsRun.join(' + ')}.`;
    if (failedTools.length) summary += ` (${failedTools.join(', ')} failed to run — see server logs.)`;

    return {
      verdict: findings.length ? 'needs_fixes' : 'pass',
      summary,
      findings,
    };
  } finally {
    await cleanupTempDir(dir);
  }
}

module.exports = { runSecurityScan };
