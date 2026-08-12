/* Tests for provider model discovery.
 *
 * Nobody should type a model ID from memory: names change constantly, and a typo returns a
 * 404 from the provider that reads like a bug in this app. Discovery asks the provider what
 * it can run. These tests use recorded catalogue shapes rather than live calls, so they pass
 * offline and still pin the filtering rules that decide what a user is offered.
 *
 * Run with `node --test tests/js`.
 */
import { test, describe, before } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');

/* A trimmed OpenRouter payload. Lyria is included because it is the case that broke the
 * naive filter: OpenRouter prices its audio per second, so both token prices are zero and
 * it sorted to the top of the free list as though it were a free text model. */
const OPENROUTER = {
  data: [
    { id: 'liquid/lfm-2.5-2.6b:free', name: 'LFM 2.5', context_length: 128000,
      pricing: { prompt: '0', completion: '0' },
      architecture: { output_modalities: ['text'] } },
    { id: 'google/lyria-3-pro-preview', name: 'Lyria 3 Pro', context_length: 1049000,
      pricing: { prompt: '0', completion: '0' },
      architecture: { output_modalities: ['text', 'audio'] } },
    { id: 'anthropic/claude-opus-5', name: 'Claude Opus 5', context_length: 200000,
      pricing: { prompt: '0.000015', completion: '0.000075' },
      architecture: { output_modalities: ['text'] } },
    { id: 'openai/text-embedding-3-large', name: 'Embedding 3 Large',
      pricing: { prompt: '0.00000013', completion: '0' },
      architecture: { output_modalities: ['embedding'] } },
  ],
};

/* An OmniRoute-shaped payload: a self-hosted gateway with no pricing and routing aliases. */
const OMNIROUTE = {
  data: [
    { id: 'auto/best-free' },
    { id: 'auto/best-coding' },
    { id: 'deepseek/deepseek-chat' },
    { id: 'nomic/nomic-embed-text' },
  ],
};

const ANTHROPIC = {
  data: [
    { id: 'claude-opus-5-20260101', display_name: 'Claude Opus 5' },
    { id: 'claude-haiku-4-5-20251001', display_name: 'Claude Haiku 4.5' },
  ],
};

let WriteRoute;
let calls;

before(async () => {
  const source = readFileSync(join(ROOT, 'static', 'engine.js'), 'utf8')
    .replace(/const PYODIDE_CDN = .*/, 'const PYODIDE_CDN = "";');
  const url = 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
  WriteRoute = (await import(url)).default;
});

function stubFetch(payload, { ok = true, status = 200 } = {}) {
  calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), headers: (init && init.headers) || {} });
    if (payload instanceof Error) throw payload;
    return { ok, status, text: async () => JSON.stringify(payload) };
  };
}

describe('provider catalogue', () => {
  test('every provider in the menu is known to the runtime', () => {
    const markup = readFileSync(join(ROOT, 'static', 'studio.html'), 'utf8');
    const block = markup.match(/<select id="providerSelect">(.*?)<\/select>/s)[1];
    const options = [...block.matchAll(/value="([^"]+)"/g)].map(m => m[1]);
    assert.ok(options.length >= 5);
    for (const id of options) {
      assert.ok(WriteRoute.providers[id], `${id} is offered in the studio but unknown to engine.js`);
    }
  });

  test('the free, keyless options are present', () => {
    for (const id of ['chrome-nano', 'omniroute', 'openrouter']) {
      assert.ok(WriteRoute.providers[id], `${id} missing`);
    }
  });
});

describe('listModels', () => {
  test('audio models are excluded even when their token price is zero', async () => {
    stubFetch(OPENROUTER);
    const r = await WriteRoute.listModels('openrouter', {});
    const ids = r.models.map(m => m.id);
    assert.ok(!ids.includes('google/lyria-3-pro-preview'),
      'an audio model must not be offered for rewriting');
    assert.ok(!ids.includes('openai/text-embedding-3-large'));
    assert.ok(ids.includes('liquid/lfm-2.5-2.6b:free'));
    assert.ok(ids.includes('anthropic/claude-opus-5'));
  });

  test('free models are listed first and counted', async () => {
    stubFetch(OPENROUTER);
    const r = await WriteRoute.listModels('openrouter', {});
    assert.equal(r.freeCount, 1);
    assert.equal(r.models[0].id, 'liquid/lfm-2.5-2.6b:free');
    assert.equal(r.models[0].free, true);
  });

  test('OpenRouter is queried without a key', async () => {
    stubFetch(OPENROUTER);
    await WriteRoute.listModels('openrouter', {});
    assert.match(calls[0].url, /openrouter\.ai\/api\/v1\/models$/);
    assert.ok(!calls[0].headers.authorization, 'no key should be sent when none was given');
  });

  test('OmniRoute defaults to the documented local gateway and needs no key', async () => {
    stubFetch(OMNIROUTE);
    const r = await WriteRoute.listModels('omniroute', {});
    assert.equal(calls[0].url, 'http://localhost:20128/v1/models');
    assert.ok(r.models.some(m => m.id === 'auto/best-free'));
    assert.equal(r.models.find(m => m.id === 'auto/best-free').free, true,
      'the free routing alias should be marked free');
    assert.ok(!r.models.some(m => m.id === 'nomic/nomic-embed-text'));
  });

  test('an unreachable local gateway says so', async () => {
    stubFetch(new TypeError('fetch failed'));
    await assert.rejects(() => WriteRoute.listModels('omniroute', {}), /Is it running\?/);
  });

  test('Anthropic uses its own endpoint, header and version', async () => {
    stubFetch(ANTHROPIC);
    const r = await WriteRoute.listModels('anthropic', { apiKey: 'sk-test' });
    assert.match(calls[0].url, /api\.anthropic\.com\/v1\/models/);
    assert.equal(calls[0].headers['x-api-key'], 'sk-test');
    assert.equal(calls[0].headers['anthropic-version'], '2023-06-01');
    assert.ok(!calls[0].headers.authorization, 'Anthropic does not use bearer auth');
    assert.equal(r.models.length, 2);
  });

  test('a keyed provider refuses to guess before a key is entered', async () => {
    stubFetch(OPENROUTER);
    await assert.rejects(() => WriteRoute.listModels('openai', {}), /API key/);
    assert.equal(calls.length, 0, 'no request should be made without a key');
  });

  test('an HTTP error is reported with the provider name and status', async () => {
    stubFetch({ error: 'nope' }, { ok: false, status: 401 });
    await assert.rejects(() => WriteRoute.listModels('openai', { apiKey: 'bad' }), /HTTP 401/);
  });

  test('the Chrome built-in model is reported as unsupported off Chrome', async () => {
    await assert.rejects(() => WriteRoute.listModels('chrome-nano', {}),
      /Chrome 138 or later/);
  });

  test('the Chrome built-in model is offered when the browser exposes it', async () => {
    globalThis.self = { LanguageModel: { availability: async () => 'downloadable' } };
    try {
      const r = await WriteRoute.listModels('chrome-nano', {});
      assert.equal(r.availability, 'downloadable');
      assert.equal(r.models[0].id, 'gemini-nano');
      assert.equal(r.models[0].free, true);
    } finally {
      delete globalThis.self;
    }
  });
});
