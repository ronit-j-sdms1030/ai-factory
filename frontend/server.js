// Static frontend plus a streaming same-origin proxy to the Phase 1 API.
const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');

const PORT = process.env.PORT || 5173;
const ROOT = path.join(__dirname, 'public');
const PHASE1_API_URL = new URL(process.env.PHASE1_API_URL || 'http://127.0.0.1:8787');

const MIME_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.woff2': 'font/woff2',
  '.json': 'application/json; charset=utf-8',
};

const HOP_BY_HOP = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'trailers',
  'transfer-encoding',
  'upgrade',
]);

function clientHeaders(req) {
  const headers = {};
  for (const [key, value] of Object.entries(req.headers)) {
    if (HOP_BY_HOP.has(key.toLowerCase())) continue;
    headers[key] = value;
  }
  headers.host = PHASE1_API_URL.host;
  headers['x-forwarded-host'] = req.headers.host || '';
  headers['x-forwarded-proto'] = 'http';
  return headers;
}

function outgoingHeaders(incoming) {
  const headers = {};
  for (const [key, value] of Object.entries(incoming)) {
    if (HOP_BY_HOP.has(key.toLowerCase())) continue;
    headers[key] = value;
  }
  return headers;
}

function proxyToPhase1(req, res) {
  const transport = PHASE1_API_URL.protocol === 'https:' ? https : http;
  const basePath = PHASE1_API_URL.pathname.replace(/\/$/, '');
  const upstream = transport.request({
    protocol: PHASE1_API_URL.protocol,
    hostname: PHASE1_API_URL.hostname,
    port: PHASE1_API_URL.port || undefined,
    method: req.method,
    path: basePath + req.url,
    headers: clientHeaders(req),
  }, (upstreamResponse) => {
    const headers = outgoingHeaders(upstreamResponse.headers);
    const streaming = String(headers['content-type'] || '').includes('text/event-stream');
    if (streaming) {
      res.writeHead(upstreamResponse.statusCode || 502, headers);
      upstreamResponse.pipe(res);
      return;
    }
    const chunks = [];
    upstreamResponse.on('data', (chunk) => chunks.push(chunk));
    upstreamResponse.on('end', () => {
      const body = Buffer.concat(chunks);
      headers['content-length'] = String(body.length);
      res.writeHead(upstreamResponse.statusCode || 502, headers);
      res.end(body);
    });
    upstreamResponse.on('error', (error) => {
      if (res.headersSent) return res.destroy(error);
      res.writeHead(502, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ error: 'Phase 1 API is unavailable' }));
    });
  });
  upstream.on('error', (error) => {
    if (res.headersSent) return res.destroy(error);
    res.writeHead(502, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({ error: 'Phase 1 API is unavailable' }));
  });
  req.pipe(upstream);
}

function staticHeaders(ext) {
  const headers = { 'Content-Type': MIME_TYPES[ext] || 'application/octet-stream' };
  if (ext === '.html' || ext === '.js' || ext === '.css' || ext === '.woff2') {
    headers['Cache-Control'] = 'no-store';
  }
  return headers;
}

function createServer() {
  return http.createServer((req, res) => {
    if (req.url === '/api' || req.url.startsWith('/api/')) {
      return proxyToPhase1(req, res);
    }

    let urlPath = decodeURIComponent(req.url.split('?')[0]);
    if (urlPath === '/') urlPath = '/login.html';

    const filePath = path.normalize(path.join(ROOT, urlPath));
    if (!filePath.startsWith(ROOT)) {
      res.writeHead(403);
      return res.end('Forbidden');
    }

    fs.readFile(filePath, (err, data) => {
      if (err) {
        res.writeHead(404, { 'Content-Type': 'text/plain' });
        return res.end('Not found');
      }
      res.writeHead(200, staticHeaders(path.extname(filePath)));
      res.end(data);
    });
  });
}

function startServer(port = Number(PORT)) {
  const server = createServer();
  server.once('error', (err) => {
    if (err.code === 'EADDRINUSE') {
      console.warn(`[frontend] port ${port} in use, trying ${port + 1}...`);
      startServer(port + 1);
    } else {
      console.error('[frontend] server error:', err);
    }
  });
  server.listen(port, '0.0.0.0', () => console.log(`[frontend] serving ${ROOT} on http://127.0.0.1:${port}`));
  return server;
}

if (require.main === module) startServer();

module.exports = { createServer, startServer };
