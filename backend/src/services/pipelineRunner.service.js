const { execFile } = require('child_process');
const { promisify } = require('util');
const fs = require('fs/promises');
const path = require('path');
const { randomUUID } = require('crypto');

const execFileAsync = promisify(execFile);
const MAX_OUTPUT = 12000;

function safeRelativePath(filePath) {
  return typeof filePath === 'string' && filePath && !path.isAbsolute(filePath) &&
    !filePath.split(/[\\/]+/).includes('..');
}

async function materialize(files) {
  const dir = path.join(__dirname, '..', '..', '.runtime', 'pipeline-' + randomUUID());
  await fs.mkdir(dir, { recursive: true });
  // Docker Desktop/rootless daemons may remap container root to a host UID.
  // This directory contains only disposable generated code and is deleted
  // after the run, so allow the isolated container to create dependencies.
  await fs.chmod(dir, 0o777);
  for (const file of files) {
    if (!file.content || !safeRelativePath(file.path)) continue;
    const target = path.join(dir, file.path);
    await fs.mkdir(path.dirname(target), { recursive: true });
    await fs.writeFile(target, file.content, 'utf8');
  }
  return dir;
}

function truncate(value) {
  const text = String(value || '');
  return text.length > MAX_OUTPUT ? text.slice(-MAX_OUTPUT) : text;
}

function stackFor(files) {
  const paths = files.map((file) => file.path);
  if (paths.includes('package.json')) return 'node';
  if (paths.includes('requirements.txt') || paths.some((p) => /\.py$/i.test(p))) return 'python';
  if (paths.some((p) => /\.html$/i.test(p))) return 'static';
  // A DevOps-style department's real deliverable is infrastructure-as-code —
  // Terraform, CI/CD workflow definitions, deploy shell scripts — not an
  // npm/pytest project. Without this, such a department can never pass CI:
  // "no supported executable stack" isn't a description of anything wrong
  // with the generated files, so no amount of AI rewriting can satisfy it.
  if (paths.some((p) => /\.tf$/i.test(p) || /(^|\/)\.github\/workflows\//i.test(p) || /(^|\/)\.circleci\/config\.ya?ml$/i.test(p))) return 'infra';
  return 'generic';
}

async function runCommand(file, args, cwd, timeout = 120000) {
  const startedAt = Date.now();
  try {
    const result = await execFileAsync(file, args, { cwd, timeout, maxBuffer: 10 * 1024 * 1024, env: { ...process.env, CI: 'true', NODE_ENV: 'test' } });
    return { command: [file].concat(args).join(' '), passed: true, exitCode: 0, durationMs: Date.now() - startedAt, stdout: truncate(result.stdout), stderr: truncate(result.stderr) };
  } catch (err) {
    return { command: [file].concat(args).join(' '), passed: false, exitCode: Number.isInteger(err.code) ? err.code : null, durationMs: Date.now() - startedAt, stdout: truncate(err.stdout), stderr: truncate(err.stderr || err.message) };
  }
}

async function runSandboxed(image, command, cwd, networkEnabled) {
  if (process.env.NODE_ENV === 'test' || process.env.PIPELINE_RUNNER === 'host') {
    return runCommand('sh', ['-lc', command], cwd, 180000);
  }
  const args = [
    'run', '--rm', '--user', `${process.getuid()}:${process.getgid()}`, '--cap-drop', 'ALL', '--read-only', '--tmpfs', '/tmp:rw,nosuid,size=1g',
    '--memory', '768m', '--cpus', '1', '--pids-limit', '192',
    '--network', networkEnabled ? 'bridge' : 'none',
    '-e', 'HOME=/tmp', '-e', 'npm_config_cache=/tmp/npm-cache',
    '-v', `${cwd}:/workspace`, '-w', '/workspace', '--entrypoint', 'sh', image, '-lc', command,
  ];
  return runCommand('docker', args, cwd, 300000);
}

async function runNodeChecks(dir, phase) {
  const pkgPath = path.join(dir, 'package.json');
  let pkg;
  try { pkg = JSON.parse(await fs.readFile(pkgPath, 'utf8')); } catch (err) {
    return [{ command: 'parse package.json', passed: false, exitCode: null, durationMs: 0, stdout: '', stderr: err.message }];
  }
  const checks = [];
  const jsFiles = [];
  async function walk(current) {
    for (const item of await fs.readdir(current, { withFileTypes: true })) {
      if (['node_modules', '.git'].includes(item.name)) continue;
      const full = path.join(current, item.name);
      if (item.isDirectory()) await walk(full);
      else if (/\.(js|cjs|mjs)$/i.test(item.name)) jsFiles.push(full);
    }
  }
  await walk(dir);
  for (const file of jsFiles.slice(0, 100)) checks.push(await runCommand(process.execPath, ['--check', file], dir, 15000));
  if (Object.keys(pkg.dependencies || {}).concat(Object.keys(pkg.devDependencies || {})).length) {
    checks.push(await runSandboxed('node:20-alpine', 'npm install --ignore-scripts --no-audit --no-fund', dir, true));
  }
  if (phase === 'test' && pkg.scripts && pkg.scripts.test) checks.push(await runSandboxed('node:20-alpine', 'npm test -- --runInBand', dir, false));
  else if (phase === 'ci' && pkg.scripts && pkg.scripts.build) checks.push(await runSandboxed('node:20-alpine', 'npm run build', dir, false));
  if (!checks.length) checks.push({ command: 'project inspection', passed: false, exitCode: null, durationMs: 0, stdout: '', stderr: 'No executable JavaScript, build script, or test script was generated.' });
  return checks;
}

async function runPythonChecks(dir, phase) {
  const checks = [await runSandboxed('python:3.12-alpine', 'python -m compileall -q .', dir, false)];
  if (phase === 'ci' || phase === 'test') {
    try {
      await fs.access(path.join(dir, 'requirements.txt'));
      checks.push(await runSandboxed('python:3.12-alpine', 'python -m pip install --target /workspace/.pipeline-deps -r requirements.txt', dir, true));
    } catch (err) { /* no dependency manifest */ }
  }
  if (phase === 'test') checks.push(await runSandboxed('python:3.12-alpine', 'PYTHONPATH=/workspace/.pipeline-deps python -m pytest -q', dir, false));
  return checks;
}

// Repo root — where node_modules (and js-yaml) actually live. The YAML check
// below runs directly on the host rather than sandboxed, the same way
// runNodeChecks' `node --check` syntax checks do above: parsing untrusted
// YAML with a safe loader never executes anything, so there's no isolation
// benefit to spinning up a container for it, only latency.
const BACKEND_ROOT = path.join(__dirname, '..', '..');
const YAML_CHECK_SCRIPT =
  "try{require('js-yaml').load(require('fs').readFileSync(process.argv[1],'utf8'));process.exit(0);}" +
  'catch(e){console.error(e.message);process.exit(1);}';

async function runInfraChecks(dir) {
  const tfFiles = [];
  const yamlFiles = [];
  const shFiles = [];
  async function walk(current) {
    for (const item of await fs.readdir(current, { withFileTypes: true })) {
      if (['node_modules', '.git'].includes(item.name)) continue;
      const full = path.join(current, item.name);
      if (item.isDirectory()) await walk(full);
      else if (/\.tf$/i.test(item.name)) tfFiles.push(full);
      else if (/\.ya?ml$/i.test(item.name)) yamlFiles.push(full);
      else if (/\.sh$/i.test(item.name)) shFiles.push(full);
    }
  }
  await walk(dir);

  const checks = [];
  for (const file of yamlFiles.slice(0, 50)) checks.push(await runCommand(process.execPath, ['-e', YAML_CHECK_SCRIPT, file], BACKEND_ROOT, 15000));
  for (const file of shFiles.slice(0, 50)) checks.push(await runCommand('bash', ['-n', file], dir, 15000));
  // fmt (not validate) deliberately — validate needs `terraform init` to
  // download real provider schemas, which means real network access for
  // something that's supposed to be an isolated syntax check. No -check
  // flag: this only needs to catch genuinely malformed/truncated HCL (fmt
  // still has to parse a file before it can reformat it, so that alone
  // fails the same way -check would) — not gate on canonical `=` alignment,
  // which would fail plenty of otherwise-correct generated code on a purely
  // cosmetic basis and send the AI fixer chasing whitespace instead of bugs.
  if (tfFiles.length) checks.push(await runSandboxed('hashicorp/terraform:latest', 'terraform fmt -recursive .', dir, false));
  if (!checks.length) checks.push({ command: 'infra inspection', passed: false, exitCode: null, durationMs: 0, stdout: '', stderr: 'No Terraform, workflow, or shell files were found to validate.' });
  return checks;
}

async function runPipeline({ files, phase }) {
  const dir = await materialize(files);
  const stack = stackFor(files);
  const startedAt = Date.now();
  try {
    let checks;
    if (stack === 'node') checks = await runNodeChecks(dir, phase);
    else if (stack === 'python') checks = await runPythonChecks(dir, phase);
    else if (stack === 'static') checks = [{ command: 'static artifact validation', passed: true, exitCode: 0, durationMs: 0, stdout: 'HTML artifact is available for browser UAT.', stderr: '' }];
    else if (stack === 'infra') checks = await runInfraChecks(dir);
    else checks = [{ command: 'pipeline detection', passed: false, exitCode: null, durationMs: 0, stdout: '', stderr: 'No supported executable stack was detected.' }];
    return { phase, stack, verdict: checks.every((c) => c.passed) ? 'pass' : 'fail', checks, startedAt, completedAt: Date.now(), durationMs: Date.now() - startedAt };
  } finally {
    // Cleanup must never turn an already-completed CI result into a 502.
    // Files are normally owned by the host UID above; retain the directory
    // for diagnosis if a remapped Docker daemon still makes cleanup fail.
    await fs.rm(dir, { recursive: true, force: true }).catch((err) => {
      console.warn('[pipeline] cleanup failed:', err.message);
    });
  }
}

module.exports = { safeRelativePath, stackFor, runPipeline };
