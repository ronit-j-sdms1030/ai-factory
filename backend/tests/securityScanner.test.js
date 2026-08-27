const {
  runSecurityBaselineTests,
  filterAlreadyRemediatedFindings,
} = require('../src/services/securityScanner.service');

describe('Security Scanner Service - 3-Stage Pipeline', () => {
  describe('runSecurityBaselineTests', () => {
    test('detects embedded private keys and assigns correct line number and critical severity', async () => {
      const files = [
        {
          path: 'src/config/keys.js',
          content: 'const fs = require("fs");\n// Private key below\nconst key = "-----BEGIN RSA PRIVATE KEY-----\\nMIIEowIBAAKCAQEA0...";\nmodule.exports = key;',
        },
      ];

      const findings = await runSecurityBaselineTests(files);
      expect(findings.length).toBeGreaterThanOrEqual(1);

      const keyFinding = findings.find((f) => f.ruleId === 'security.secrets.private-key-embedded');
      expect(keyFinding).toBeDefined();
      expect(keyFinding.tool).toBe('security-test');
      expect(keyFinding.category).toBe('secrets');
      expect(keyFinding.file).toBe('src/config/keys.js');
      expect(keyFinding.line).toBe(3);
      expect(keyFinding.severity).toBe('critical');
    });

    test('detects dangerous eval() call with exact line number', async () => {
      const files = [
        {
          path: 'src/utils/calc.js',
          content: 'function compute(expr) {\n  const result = eval(expr);\n  return result;\n}',
        },
      ];

      const findings = await runSecurityBaselineTests(files);
      const evalFinding = findings.find((f) => f.ruleId === 'security.sast.unsafe-eval');
      expect(evalFinding).toBeDefined();
      expect(evalFinding.tool).toBe('security-test');
      expect(evalFinding.file).toBe('src/utils/calc.js');
      expect(evalFinding.line).toBe(2);
      expect(evalFinding.severity).toBe('high');
    });

    test('detects disabled TLS verification with exact line number', async () => {
      const files = [
        {
          path: 'src/api/client.js',
          content: 'const axios = require("axios");\nconst agent = new https.Agent({ rejectUnauthorized: false });\nmodule.exports = agent;',
        },
      ];

      const findings = await runSecurityBaselineTests(files);
      const tlsFinding = findings.find((f) => f.ruleId === 'security.sast.disabled-tls-verification');
      expect(tlsFinding).toBeDefined();
      expect(tlsFinding.file).toBe('src/api/client.js');
      expect(tlsFinding.line).toBe(2);
      expect(tlsFinding.severity).toBe('high');
    });

    test('detects Dockerfile running as root', async () => {
      const files = [
        {
          path: 'Dockerfile',
          content: 'FROM node:20-alpine\nWORKDIR /app\nUSER root\nCOPY . .\nCMD ["node", "server.js"]',
        },
      ];

      const findings = await runSecurityBaselineTests(files);
      const rootFinding = findings.find((f) => f.ruleId === 'security.docker.root-user');
      expect(rootFinding).toBeDefined();
      expect(rootFinding.file).toBe('Dockerfile');
      expect(rootFinding.line).toBe(3);
    });

    test('clean code yields zero baseline findings', async () => {
      const files = [
        {
          path: 'src/server.js',
          content: 'const express = require("express");\nconst app = express();\nconst PORT = process.env.PORT || 3000;\napp.listen(PORT);',
        },
        {
          path: 'Dockerfile',
          content: 'FROM node:20-alpine\nUSER node\nWORKDIR /app\nCOPY --chown=node:node ["package.json", "./"]\nCMD ["npm", "start"]',
        },
      ];

      const findings = await runSecurityBaselineTests(files);
      expect(findings).toEqual([]);
    });
  });

  describe('filterAlreadyRemediatedFindings', () => {
    test('filters out remediated node:fs / node:path imports', () => {
      const files = [
        { path: 'src/index.js', content: "const fs = require('node:fs');\nconst path = require('node:path');" },
      ];
      const findings = [
        { file: 'src/index.js', issue: "Prefer `node:fs` over `fs`", tool: 'semgrep' },
        { file: 'src/index.js', issue: "Prefer `node:path` over `path`", tool: 'semgrep' },
        { file: 'src/index.js', issue: "Real issue", tool: 'semgrep' },
      ];

      const remaining = filterAlreadyRemediatedFindings(findings, files);
      expect(remaining.length).toBe(1);
      expect(remaining[0].issue).toBe('Real issue');
    });
  });
});
