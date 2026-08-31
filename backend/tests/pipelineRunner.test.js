const { safeRelativePath, stackFor, runPipeline } = require('../src/services/pipelineRunner.service');

describe('generated-code pipeline runner', () => {
  test('rejects paths that can escape the temporary workspace', () => {
    expect(safeRelativePath('../secret')).toBe(false);
    expect(safeRelativePath('/etc/passwd')).toBe(false);
    expect(safeRelativePath('src/app.js')).toBe(true);
  });

  test('detects supported project stacks', () => {
    expect(stackFor([{ path: 'package.json' }])).toBe('node');
    expect(stackFor([{ path: 'src/app.py' }])).toBe('python');
    expect(stackFor([{ path: 'index.html' }])).toBe('static');
    expect(stackFor([{ path: 'terraform/main.tf' }])).toBe('infra');
    expect(stackFor([{ path: '.github/workflows/ci.yml' }])).toBe('infra');
    expect(stackFor([{ path: '.circleci/config.yml' }])).toBe('infra');
  });

  test('executes a real generated Node test command', async () => {
    const result = await runPipeline({
      phase: 'test',
      files: [
        { path: 'package.json', content: JSON.stringify({ scripts: { test: 'node test.js' } }) },
        { path: 'test.js', content: "const assert = require('assert'); assert.equal(2 + 2, 4);" },
      ],
    });
    expect(result.verdict).toBe('pass');
    expect(result.checks.some((check) => check.command.includes('npm test'))).toBe(true);
  });

  test('records a failing generated test', async () => {
    const result = await runPipeline({
      phase: 'test',
      files: [
        { path: 'package.json', content: JSON.stringify({ scripts: { test: 'node test.js' } }) },
        { path: 'test.js', content: "throw new Error('generated test failed');" },
      ],
    });
    expect(result.verdict).toBe('fail');
  });

  test('validates a well-formed infra-as-code deliverable (no npm/pytest project)', async () => {
    const result = await runPipeline({
      phase: 'ci',
      files: [
        { path: '.github/workflows/ci.yml', content: 'name: CI\non:\n  push:\n    branches: [main]\njobs:\n  build:\n    runs-on: ubuntu-latest\n' },
        { path: 'scripts/deploy.sh', content: '#!/bin/bash\nset -e\necho "deploying"\n' },
      ],
    });
    expect(result.stack).toBe('infra');
    expect(result.verdict).toBe('pass');
  });

  test('catches malformed YAML in a generated workflow file', async () => {
    const result = await runPipeline({
      phase: 'ci',
      files: [
        { path: '.github/workflows/ci.yml', content: 'name: CI\non:\n  push:\n  branches: [unterminated\n' },
      ],
    });
    expect(result.stack).toBe('infra');
    expect(result.verdict).toBe('fail');
  });

  test('catches a shell syntax error in a generated deploy script', async () => {
    const result = await runPipeline({
      phase: 'ci',
      files: [
        { path: '.github/workflows/ci.yml', content: 'name: CI\non:\n  push:\n    branches: [main]\n' },
        { path: 'scripts/deploy.sh', content: '#!/bin/bash\nif [ "$X" = "1" ]; then\n  echo "missing fi"\n' },
      ],
    });
    expect(result.stack).toBe('infra');
    expect(result.verdict).toBe('fail');
  });
});
