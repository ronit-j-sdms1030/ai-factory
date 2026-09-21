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

function proxyToPhase1(req, res) {
  const transport = PHASE1_API_URL.protocol === 'https:' ? https : http;
  const basePath = PHASE1_API_URL.pathname.replace(/\/$/, '');
  const headers = Object.assign({}, req.headers, {
    host: PHASE1_API_URL.host,
    'x-forwarded-host': req.headers.host || '',
    'x-forwarded-proto': 'http',
  });
  const upstream = transport.request({
    protocol: PHASE1_API_URL.protocol,
    hostname: PHASE1_API_URL.hostname,
    port: PHASE1_API_URL.port || undefined,
    method: req.method,
    path: basePath + req.url,
    headers,
  }, (upstreamResponse) => {
    res.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers);
    upstreamResponse.pipe(res);
  });
  upstream.on('error', (error) => {
    if (res.headersSent) return res.destroy(error);
    res.writeHead(502, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({ error: 'Phase 1 API is unavailable' }));
  });
  req.pipe(upstream);
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
    const ext = path.extname(filePath);
    res.writeHead(200, { 'Content-Type': MIME_TYPES[ext] || 'application/octet-stream' });
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
  server.listen(port, () => console.log(`[frontend] serving ${ROOT} on http://localhost:${port}`));
  return server;
}

if (require.main === module) startServer();

module.exports = { createServer, startServer };
