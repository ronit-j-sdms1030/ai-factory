const assert = require('node:assert/strict');
const http = require('node:http');
const test = require('node:test');

function listen(server) {
  return new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
}

function close(server) {
  return new Promise((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve());
  });
}

function get(port, path, headers = {}) {
  return new Promise((resolve, reject) => {
    http.get({ hostname: '127.0.0.1', port, path, headers }, (response) => {
      const chunks = [];
      response.on('data', (chunk) => chunks.push(chunk));
      response.on('end', () => resolve({
        status: response.statusCode,
        headers: response.headers,
        body: Buffer.concat(chunks).toString('utf8'),
      }));
    }).on('error', reject);
  });
}

test('proxies API cookies and streaming responses', async () => {
  const upstream = http.createServer((request, response) => {
    assert.equal(request.headers.cookie, 'phase1_session=test-token');
    assert.equal(request.url, '/api/runs/REQ-1/events');
    response.writeHead(200, {
      'content-type': 'text/event-stream',
      'set-cookie': 'phase1_session=refreshed; Path=/; HttpOnly',
      'connection': 'keep-alive',
      'transfer-encoding': 'chunked',
    });
    response.write('event: status\n');
    response.end('data: {"id":"REQ-1"}\n\n');
  });
  await listen(upstream);

  process.env.PHASE1_API_URL = `http://127.0.0.1:${upstream.address().port}`;
  delete require.cache[require.resolve('./server')];
  const frontend = require('./server').createServer();
  await listen(frontend);

  try {
    const response = await get(
      frontend.address().port,
      '/api/runs/REQ-1/events',
      { cookie: 'phase1_session=test-token' },
    );
    assert.equal(response.status, 200);
    assert.equal(response.headers['content-type'], 'text/event-stream');
    assert.match(response.headers['set-cookie'][0], /phase1_session=refreshed/);
    assert.equal(response.headers['transfer-encoding'] === undefined || response.headers['transfer-encoding'] === 'chunked', true);
    assert.equal(response.body, 'event: status\ndata: {"id":"REQ-1"}\n\n');
  } finally {
    await close(frontend);
    await close(upstream);
  }
});

function post(port, path, body) {
  return new Promise((resolve, reject) => {
    const request = http.request({
      hostname: '127.0.0.1',
      port,
      path,
      method: 'POST',
      headers: { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body) },
    }, (response) => {
      const chunks = [];
      response.on('data', (chunk) => chunks.push(chunk));
      response.on('end', () => resolve({
        status: response.statusCode,
        headers: response.headers,
        body: Buffer.concat(chunks).toString('utf8'),
      }));
    });
    request.on('error', reject);
    request.end(body);
  });
}

test('login proxy drops hop-by-hop headers and keeps the session cookie', async () => {
  const upstream = http.createServer((request, response) => {
    assert.equal(request.url, '/api/auth/login');
    response.writeHead(200, {
      'content-type': 'application/json',
      'set-cookie': 'phase1_session=chrome; Path=/; HttpOnly; SameSite=Lax',
      'connection': 'close',
    });
    response.end('{"ok":true}');
  });
  await listen(upstream);

  process.env.PHASE1_API_URL = `http://127.0.0.1:${upstream.address().port}`;
  delete require.cache[require.resolve('./server')];
  const frontend = require('./server').createServer();
  await listen(frontend);

  try {
    const response = await post(frontend.address().port, '/api/auth/login', '{}');
    assert.equal(response.status, 200);
    assert.equal(response.body, '{"ok":true}');
    assert.match(String(response.headers['set-cookie']), /phase1_session=chrome/);
    assert.equal(response.headers.connection === 'close', false);
  } finally {
    await close(frontend);
    await close(upstream);
  }
});
