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
});
