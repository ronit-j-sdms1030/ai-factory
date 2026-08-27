// Real static-analysis tooling for generated code — 3-stage pipeline:
// 1. Security Tests & baseline audits (secrets, unsafe APIs, dangerous regex/injections)
// 2. Semgrep OSS (local venv, OWASP Top 10, Secrets, SAST)
// 3. SonarQube Community Edition (dedicated local instance)
// All operate on temporary directories without executing untrusted code directly.
const { execFile } = require('child_process');
const { promisify } = require('util');
const fs = require('fs/promises');
const path = require('path');
const { randomUUID } = require('crypto');

const execFileAsync = promisify(execFile);

const SEMGREP_BIN = path.join(__dirname, '..', '..', '.semgrep-venv', 'bin', 'semgrep');
const SONARQUBE_URL = process.env.SONARQUBE_URL || 'http://localhost:9000';
const SONARQUBE_TOKEN = process.env.SONARQUBE_TOKEN;
const SONAR_COMPOSE_DIR = path.join(__dirname, '..', '..', 'security-tools');

async function writeFilesToTempDir(files) {
  const dir = path.join(__dirname, '..', '..', '.runtime', 'scan-' + randomUUID());
  await fs.mkdir(dir, { recursive: true });
  // The official Sonar scanner image runs as a non-host UID and needs to
  // create .scannerwork inside this short-lived bind mount. The directory
  // contains generated code only and is deleted immediately after scanning.
  await fs.chmod(dir, 0o777);
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

// ─── Stage 1: Baseline Security Tests ───────────────────────────────────────
async function runSecurityBaselineTests(files) {
  const findings = [];

  for (const file of files) {
    if (!file.content || typeof file.content !== 'string') continue;
    const lines = file.content.split(/\r?\n/);
    const relPath = file.path.replace(/\\/g, '/');
    const ext = (path.extname(relPath) || '').toLowerCase();

    // 1. Check for private keys
    if (/-----BEGIN (?:[A-Z0-9_-]+\s+)?PRIVATE KEY-----/i.test(file.content)) {
      lines.forEach((lineText, idx) => {
        if (/-----BEGIN (?:[A-Z0-9_-]+\s+)?PRIVATE KEY-----/i.test(lineText)) {
          findings.push({
            tool: 'security-test',
            category: 'secrets',
            file: relPath,
            line: idx + 1,
            endLine: idx + 1,
            severity: 'critical',
            ruleId: 'security.secrets.private-key-embedded',
            issue: 'Hardcoded private cryptographic key detected in source code.',
          });
        }
      });
    }

    // 2. Check for hardcoded high-risk API secrets / tokens
    lines.forEach((lineText, idx) => {
      const isComment = /^\s*(\/\/|#|\/\*|\*)/.test(lineText);
      const hasPlaceholder = /(?:process\.env|<|YOUR_|CHANGE_ME|PLACEHOLDER|TODO|EXAMPLE|xxx)/i.test(lineText);

      if (!isComment && !hasPlaceholder) {
        const secretMatch = lineText.match(/\b(?:api[_-]?key|jwt[_-]?secret|auth[_-]?token|app[_-]?secret|db[_-]?pass(?:word)?)\s*[:=]\s*['"]([A-Za-z0-9_\-\.]{16,})['"]/i);
        if (secretMatch && !/['"](?:postgres|mongodb|redis|localhost|127\.0\.0\.1|none|test|dev|development)['"]/i.test(secretMatch[0])) {
          findings.push({
            tool: 'security-test',
            category: 'secrets',
            file: relPath,
            line: idx + 1,
            endLine: idx + 1,
            severity: 'high',
            ruleId: 'security.secrets.hardcoded-token',
            issue: `Hardcoded potential credential or secret assigned on line ${idx + 1}. Use environment variables instead.`,
          });
        }
      }

      // 3. Dangerous eval / new Function
      if (['.js', '.cjs', '.mjs', '.ts', '.jsx', '.tsx'].includes(ext)) {
        if (/\beval\s*\(/.test(lineText) && !isComment) {
          findings.push({
            tool: 'security-test',
            category: 'sast',
            file: relPath,
            line: idx + 1,
            endLine: idx + 1,
            severity: 'high',
            ruleId: 'security.sast.unsafe-eval',
            issue: 'Use of eval() allows arbitrary code execution and creates code injection vulnerabilities.',
          });
        }
        if (/\bnew\s+Function\s*\(/.test(lineText) && !isComment) {
          findings.push({
            tool: 'security-test',
            category: 'sast',
            file: relPath,
            line: idx + 1,
            endLine: idx + 1,
            severity: 'medium',
            ruleId: 'security.sast.unsafe-new-function',
            issue: 'Use of new Function(...) can lead to code injection from untrusted inputs.',
          });
        }

        // 4. Disabled TLS verification
        if (/rejectUnauthorized\s*:\s*false/i.test(lineText) || /NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*['"]?0['"]?/i.test(lineText)) {
          findings.push({
            tool: 'security-test',
            category: 'sast',
            file: relPath,
            line: idx + 1,
            endLine: idx + 1,
            severity: 'high',
            ruleId: 'security.sast.disabled-tls-verification',
            issue: 'TLS certificate verification is explicitly disabled, allowing man-in-the-middle attacks.',
          });
        }

        // 5. Insecure CORS with credentials wildcard
        if (/cors\s*\(\s*\{\s*origin\s*:\s*['"]\*['"]\s*,\s*credentials\s*:\s*true/i.test(lineText)) {
          findings.push({
            tool: 'security-test',
            category: 'sast',
            file: relPath,
            line: idx + 1,
            endLine: idx + 1,
            severity: 'high',
            ruleId: 'security.sast.insecure-cors-credentials',
            issue: 'CORS policy allows wildcard origin with credentials: true. Specify explicit allowed origins.',
          });
        }
      }

      // 6. Python security checks
      if (ext === '.py') {
        if (/\beval\s*\(/.test(lineText) || /\bexec\s*\(/.test(lineText)) {
          if (!isComment) {
            findings.push({
              tool: 'security-test',
              category: 'sast',
              file: relPath,
              line: idx + 1,
              endLine: idx + 1,
              severity: 'high',
              ruleId: 'security.python.unsafe-eval-exec',
              issue: 'Dynamic code execution with eval() or exec() poses severe arbitrary code execution risks.',
            });
          }
        }
      }

      // 7. Dockerfile security checks
      if (path.basename(relPath).toLowerCase() === 'dockerfile') {
        if (/^USER\s+root\b/i.test(lineText.trim())) {
          findings.push({
            tool: 'security-test',
            category: 'configuration',
            file: relPath,
            line: idx + 1,
            endLine: idx + 1,
            severity: 'medium',
            ruleId: 'security.docker.root-user',
            issue: 'Container explicitly runs as root. Use a non-root user (e.g. USER node) for security.',
          });
        }
      }
    });

    // Check Dockerfile for missing USER directive entirely
    if (path.basename(relPath).toLowerCase() === 'dockerfile' && !/^USER\s+/m.test(file.content)) {
      findings.push({
        tool: 'security-test',
        category: 'configuration',
        file: relPath,
        line: 1,
        endLine: 1,
        severity: 'medium',
        ruleId: 'security.docker.missing-user',
        issue: 'Dockerfile does not specify a non-root USER directive.',
      });
    }
  }

  return findings;
}

// ─── Stage 2: Semgrep OSS ───────────────────────────────────────────────────
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
  return (parsed.results || []).map((r) => {
    const startLine = r.start ? r.start.line : null;
    const endLine = r.end ? r.end.line : startLine;
    const col = r.start ? r.start.col : null;

    return {
      tool: 'semgrep',
      category: /secret/i.test(r.check_id) ? 'secrets' : 'sast',
      file: path.relative(dir, r.path).replace(/\\/g, '/'),
      line: startLine,
      endLine: endLine,
      col: col,
      ruleId: r.check_id,
      issue: (r.extra && r.extra.message) || r.check_id,
      severity: mapSemgrepSeverity(r.extra && r.extra.severity),
    };
  });
}

// ─── Stage 3: SonarQube Community Edition ───────────────────────────────────
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

function filterAlreadyRemediatedFindings(findings, files) {
  const byPath = new Map(files.map((file) => [file.path.replace(/\\/g, '/'), file.content || '']));
  return findings.filter((finding) => {
    const normFile = (finding.file || '').replace(/\\/g, '/');
    const content = byPath.get(normFile) || '';
    if (/Prefer `node:fs` over `fs`/.test(finding.issue)) {
      return /require\((['"])fs\1\)/.test(content);
    }
    if (/Prefer `node:path` over `path`/.test(finding.issue)) {
      return /require\((['"])path\1\)/.test(content);
    }
    if (path.basename(normFile).toLowerCase() === 'dockerfile') {
      if (/runs with "root"/.test(finding.issue)) return !/^USER\s+(?!root\b)\S+/m.test(content);
      if (/Copying recursively/.test(finding.issue)) return /^COPY\s+(?:--\S+\s+)*\.\s+\.?\/?$/m.test(content);
      if (/copied resource cannot be modified/.test(finding.issue)) return !/^COPY\s+--chown=(?!root)/m.test(content);
      if (/shell form with exec form|invalid JSON/.test(finding.issue)) {
        const command = content.match(/^(?:CMD|ENTRYPOINT)\s+(.+)$/m);
        if (!command) return true;
        try { return !Array.isArray(JSON.parse(command[1])); } catch (err) { return true; }
      }
    }
    return true;
  });
}

let sonarQueue = Promise.resolve();
function queueSonarScan(args) {
  if (!SONARQUBE_TOKEN) return Promise.resolve([]);

  const run = sonarQueue.then(async () => {
    await ensureSonarUp();
    try {
      return await runSonarScan(args);
    } finally {
      scheduleSonarShutdown();
    }
  });
  sonarQueue = run.catch(() => {});
  return run;
}

const SONAR_IDLE_SHUTDOWN_MS = 30000;
let sonarUpPromise = null;
let sonarShutdownTimer = null;

async function ensureSonarUp() {
  if (sonarShutdownTimer) {
    clearTimeout(sonarShutdownTimer);
    sonarShutdownTimer = null;
  }
  if (sonarUpPromise) return sonarUpPromise;

  sonarUpPromise = (async () => {
    await execFileAsync('docker', ['compose', 'up', '-d'], { cwd: SONAR_COMPOSE_DIR, timeout: 60000 });
    for (let i = 0; i < 40; i++) {
      try {
        const res = await fetch(SONARQUBE_URL + '/api/system/status');
        const data = await res.json();
        if (data.status === 'UP') return;
      } catch (err) {
        // Not accepting connections yet — keep polling.
      }
      await new Promise((resolve) => setTimeout(resolve, 3000));
    }
    throw new Error('SonarQube did not become ready after starting it');
  })();

  try {
    await sonarUpPromise;
  } catch (err) {
    sonarUpPromise = null;
    throw err;
  }
}

function scheduleSonarShutdown() {
  if (sonarShutdownTimer) clearTimeout(sonarShutdownTimer);
  sonarShutdownTimer = setTimeout(() => {
    sonarShutdownTimer = null;
    sonarUpPromise = null;
    execFileAsync('docker', ['compose', 'stop'], { cwd: SONAR_COMPOSE_DIR, timeout: 60000 }).catch((err) => {
      console.error('[security-scan] failed to stop sonarqube after idling:', err.message);
    });
  }, SONAR_IDLE_SHUTDOWN_MS);
  if (sonarShutdownTimer.unref) sonarShutdownTimer.unref();
}

async function runSonarScan({ dir, projectKey }) {
  if (!SONARQUBE_TOKEN) return [];

  const scanResult = await execFileAsync('docker', [
    'run', '--rm', '--user', '0:0', '--network', 'host',
    '-v', `${dir}:/usr/src`,
    'sonarsource/sonar-scanner-cli',
    '-Dsonar.host.url=' + SONARQUBE_URL,
    '-Dsonar.token=' + SONARQUBE_TOKEN,
    '-Dsonar.projectKey=' + projectKey,
    '-Dsonar.sources=.',
    '-Dsonar.sourceEncoding=UTF-8',
    '-Dsonar.scanner.metadataFilePath=/usr/src/report-task.txt',
    '-Dsonar.working.directory=/usr/src/.scannerwork',
  ], { timeout: 180000, maxBuffer: 20 * 1024 * 1024 });

  const reportTaskPath = path.join(dir, 'report-task.txt');
  let reportTaskRaw = '';
  try { reportTaskRaw = await fs.readFile(reportTaskPath, 'utf8'); } catch (err) { /* fallback */ }
  const ceTaskIdMatch = reportTaskRaw.match(/^ceTaskId=(.+)$/m) ||
    String(scanResult.stdout || '').match(/api\/ce\/task\?id=([A-Za-z0-9_-]+)/);
  if (!ceTaskIdMatch) return [];
  const taskId = ceTaskIdMatch[1].trim();

  const authHeader = 'Basic ' + Buffer.from(SONARQUBE_TOKEN + ':').toString('base64');

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

  const issueFindings = (issuesData.issues || []).map((i) => {
    const line = i.line || (i.textRange && i.textRange.startLine) || null;
    const endLine = (i.textRange && i.textRange.endLine) || line;
    return {
      tool: 'sonarqube',
      category: 'sast',
      file: sonarComponentPath(i.component || '').replace(/\\/g, '/'),
      line: line,
      endLine: endLine,
      ruleId: i.rule || i.key || 'sonarqube-issue',
      issue: i.message,
      severity: mapSonarSeverity(i.severity),
    };
  });

  const hotspotFindings = (hotspotsData.hotspots || []).map((h) => {
    const line = h.line || (h.textRange && h.textRange.startLine) || null;
    const endLine = (h.textRange && h.textRange.endLine) || line;
    return {
      tool: 'sonarqube',
      category: 'secrets',
      file: sonarComponentPath(h.component || '').replace(/\\/g, '/'),
      line: line,
      endLine: endLine,
      ruleId: h.ruleKey || h.key || 'sonarqube-hotspot',
      issue: h.message,
      severity: h.vulnerabilityProbability === 'HIGH' ? 'high' : h.vulnerabilityProbability === 'MEDIUM' ? 'medium' : 'low',
    };
  });

  return issueFindings.concat(hotspotFindings);
}

// ─── Combined 3-Stage Entry Point ───────────────────────────────────────────
async function runSecurityScan({ department, artifactId, files, onProgress }) {
  const dir = await writeFilesToTempDir(files);
  const projectKey = 'codegen-' + artifactId + '-' + department.toLowerCase().replace(/[^a-z0-9]+/g, '-');
  const notify = (msg) => { if (typeof onProgress === 'function') onProgress(msg); };

  const stages = [];
  const failedTools = [];

  try {
    // ── Stage 1: Security Tests ──
    notify('[Stage 1/3] Running baseline security tests & secret audits…');
    const t1Start = Date.now();
    let baselineFindings = [];
    try {
      baselineFindings = await runSecurityBaselineTests(files);
      stages.push({
        name: 'Security Tests',
        tool: 'security-test',
        passed: baselineFindings.length === 0,
        findingsCount: baselineFindings.length,
        durationMs: Date.now() - t1Start,
      });
      notify(`✓ [Stage 1/3] Security tests complete (${baselineFindings.length} issue(s) found).`);
    } catch (err) {
      console.error('[security-scan] baseline tests failed:', err.message);
      failedTools.push('Security Tests');
      stages.push({ name: 'Security Tests', tool: 'security-test', passed: false, findingsCount: 0, durationMs: Date.now() - t1Start, error: err.message });
    }

    // ── Stage 2: Semgrep OSS ──
    notify('[Stage 2/3] Running Semgrep OSS security audit (OWASP Top 10, Secrets, SAST)…');
    const t2Start = Date.now();
    let semgrepFindings = [];
    try {
      semgrepFindings = await runSemgrepScan(dir);
      stages.push({
        name: 'Semgrep OSS',
        tool: 'semgrep',
        passed: semgrepFindings.length === 0,
        findingsCount: semgrepFindings.length,
        durationMs: Date.now() - t2Start,
      });
      notify(`✓ [Stage 2/3] Semgrep scan complete (${semgrepFindings.length} issue(s) found).`);
    } catch (err) {
      console.error('[security-scan] semgrep failed:', err.message);
      failedTools.push('Semgrep');
      stages.push({ name: 'Semgrep OSS', tool: 'semgrep', passed: false, findingsCount: 0, durationMs: Date.now() - t2Start, error: err.message });
    }

    // ── Stage 3: SonarQube ──
    notify('[Stage 3/3] Running SonarQube code quality & security hotspot scan…');
    const t3Start = Date.now();
    let sonarFindings = [];
    if (SONARQUBE_TOKEN) {
      try {
        sonarFindings = await queueSonarScan({ dir, projectKey });
        stages.push({
          name: 'SonarQube',
          tool: 'sonarqube',
          passed: sonarFindings.length === 0,
          findingsCount: sonarFindings.length,
          durationMs: Date.now() - t3Start,
        });
        notify(`✓ [Stage 3/3] SonarQube scan complete (${sonarFindings.length} issue(s) found).`);
      } catch (err) {
        console.error('[security-scan] sonarqube failed:', err.message);
        failedTools.push('SonarQube');
        stages.push({ name: 'SonarQube', tool: 'sonarqube', passed: false, findingsCount: 0, durationMs: Date.now() - t3Start, error: err.message });
      }
    } else {
      stages.push({ name: 'SonarQube', tool: 'sonarqube', passed: true, findingsCount: 0, durationMs: 0, skipped: true });
      notify('[Stage 3/3] SonarQube skipped (no token configured).');
    }

    const rawFindings = (baselineFindings || []).concat(semgrepFindings || []).concat(sonarFindings || []);
    const filteredFindings = filterAlreadyRemediatedFindings(rawFindings, files);

    // Deduplicate and normalize findings
    const findings = [];
    const seen = new Set();
    let findingIdx = 1;
    for (const f of filteredFindings) {
      const key = `${f.tool}:${f.file}:${f.line || 0}:${f.issue}`;
      if (!seen.has(key)) {
        seen.add(key);
        findings.push({
          id: `finding-${findingIdx++}`,
          ...f,
        });
      }
    }

    // Sort findings by severity (critical > high > medium > low) then file/line
    const sevOrder = { critical: 0, high: 1, medium: 2, low: 3 };
    findings.sort((a, b) => {
      const sDiff = (sevOrder[a.severity] ?? 4) - (sevOrder[b.severity] ?? 4);
      if (sDiff !== 0) return sDiff;
      if (a.file !== b.file) return a.file.localeCompare(b.file);
      return (a.line || 0) - (b.line || 0);
    });

    const highOrAbove = findings.filter((f) => f.severity === 'high' || f.severity === 'critical').length;
    const toolsRun = ['Security Tests', 'Semgrep'];
    if (SONARQUBE_TOKEN) toolsRun.push('SonarQube');

    let summary = findings.length
      ? `${findings.length} finding(s) from ${toolsRun.join(' + ')} (${highOrAbove} high/critical).`
      : `No issues found by ${toolsRun.join(' + ')}.`;
    if (failedTools.length) summary += ` (${failedTools.join(', ')} failed to run — see server logs.)`;

    return {
      verdict: (findings.length || failedTools.length) ? 'needs_fixes' : 'pass',
      summary,
      stages,
      findings,
    };
  } finally {
    await cleanupTempDir(dir);
  }
}

module.exports = {
  runSecurityScan,
  runSecurityBaselineTests,
  runSemgrepScan,
  queueSonarScan,
  filterAlreadyRemediatedFindings,
};
