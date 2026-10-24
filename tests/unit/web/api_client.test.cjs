// Verify API-client serialization and uncertain-response state without a browser or DOM.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { randomUUID, webcrypto } = require('node:crypto');

function client(fetch) {
  const file = path.resolve(__dirname, '../../..', 'src/refcollector/web/static/app.js');
  const source = fs.readFileSync(file, 'utf8');
  const start = source.indexOf('  const pendingMutations = new Map();');
  const end = source.indexOf('  async function initSession()', start);
  assert.ok(start >= 0 && end > start, 'API client section must be present');
  const context = vm.createContext({
    fetch, state: { csrfToken: 'test-csrf' },
    crypto: { randomUUID, subtle: webcrypto.subtle, getRandomValues: webcrypto.getRandomValues.bind(webcrypto) },
    TextEncoder,
    FormData: class FormData {}, initSession: async () => {},
  });
  vm.runInContext(source.slice(start, end) + '\nglobalThis.request = api;\n' +
    'globalThis.pendingKeys = () => Array.from(pendingMutations.keys());', context);
  context.request.pendingKeys = context.pendingKeys;
  return context.request;
}

function response(json = async () => ({ done: true })) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json };
}

test('scratch lookup keeps its exact non-mutating DTO without an idempotency field', async () => {
  let payload;
  const api = client(async (_path, options) => {
    payload = JSON.parse(options.body);
    return response();
  });
  await api('/api/resolve-citation', { method: 'POST', body: { citation: 'Smith 2020' } });
  assert.deepEqual(payload, { citation: 'Smith 2020' });
});

test('lost successful response body keeps the logical mutation key for retry', async () => {
  const payloads = [];
  const api = client(async (_path, options) => {
    payloads.push(JSON.parse(options.body));
    if (payloads.length === 1) return response(async () => { throw new Error('body disconnected'); });
    return response();
  });
  await assert.rejects(api('/api/projects', {
    method: 'POST', body: { name: 'one', idempotency_key: 'original-key' },
  }), /body disconnected/);
  await api('/api/projects', {
    method: 'POST', body: { name: 'one', idempotency_key: 'new-click-key' },
  });
  assert.equal(payloads[1].idempotency_key, payloads[0].idempotency_key);
});

test('transport failure preserves the key while a later new operation gets its own key', async () => {
  const payloads = [];
  const api = client(async (_path, options) => {
    payloads.push(JSON.parse(options.body));
    if (payloads.length === 1) throw new Error('connection lost');
    return response();
  });
  const operation = key => ({ method: 'POST', body: { name: 'same', idempotency_key: key } });
  await assert.rejects(api('/api/projects', operation('first-key')), /connection lost/);
  await api('/api/projects', operation('retry-click-key'));
  await api('/api/projects', operation('intentional-new-key'));
  assert.equal(payloads[1].idempotency_key, 'first-key');
  assert.equal(payloads[2].idempotency_key, 'intentional-new-key');
});

test('a failed key-setting intent does not retain the raw secret in pending fingerprints', async () => {
  const secret = 'independent-client-test-key-not-a-real-credential';
  const api = client(async () => { throw new Error('connection lost'); });
  await assert.rejects(api('/api/settings/intent', {
    method: 'POST',
    body: {
      expected_settings_revision: 'initial',
      change: { type: 'set_provider_key', provider: 'openalex_key', value: secret },
      idempotency_key: 'private-key-intent',
    },
  }), /connection lost/);
  for (const fingerprint of api.pendingKeys()) {
    assert.equal(String(fingerprint).includes(secret), false);
  }
});
