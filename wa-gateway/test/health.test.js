// Gateway P1 tests (node:test, no extra deps).
const assert = require('node:assert');
const test = require('node:test');

const { createApp } = require('../src/index.js');

function listen(app) {
  return new Promise((resolve) => {
    const server = app.listen(0, '127.0.0.1', () => resolve(server));
  });
}

test('GET /health returns ok without credentials', async () => {
  const server = await listen(createApp());
  try {
    const port = server.address().port;
    const res = await fetch(`http://127.0.0.1:${port}/health`);
    assert.strictEqual(res.status, 200);
    const body = await res.json();
    assert.strictEqual(body.ok, true);
    assert.strictEqual(body.service, 'wa-gateway');
  } finally {
    server.close();
  }
});

test('requireServiceKey rejects missing key with 401', async () => {
  delete process.env.WA_GATEWAY_SERVICE_KEY;
  const { requireServiceKey } = require('../src/auth.js');
  const express = require('express');
  const app = express();
  app.get('/guarded', requireServiceKey, (_req, res) => res.json({ ok: true }));
  const server = await listen(app);
  try {
    const port = server.address().port;
    const res = await fetch(`http://127.0.0.1:${port}/guarded`);
    // Without WA_GATEWAY_SERVICE_KEY set, every guarded call 401s.
    assert.strictEqual(res.status, 401);
  } finally {
    server.close();
  }
});
