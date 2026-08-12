/* WriteRoute runtime.
 *
 * One call surface, two backends. `WriteRoute.call(route, payload, headers)` reaches a
 * local FastAPI process when one is running, and otherwise boots the same Python
 * package inside Pyodide and calls it directly. The front end does not know which.
 *
 * The provider call is deliberately never Python's job in the browser build. Pyodide
 * has no sockets, so the fetch happens here — which means an API key travels from the
 * text field to the provider and nowhere else. No proxy of ours ever holds it. The
 * shipped server had the opposite arrangement: a client-supplied base_url forwarded
 * server-side with the key attached, which is an open credential proxy the moment it
 * is hosted. Generating candidates here and adjudicating them in Python removes that
 * surface instead of guarding it.
 */
const WriteRoute = (() => {
  const PYODIDE_CDN = 'https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.mjs';

  const state = {
    mode: null,          // 'server' | 'pyodide'
    pyodide: null,
    booting: null,
    listeners: new Set(),
  };

  function onStatus(fn) {
    state.listeners.add(fn);
    return () => state.listeners.delete(fn);
  }

  function status(phase, detail = '') {
    for (const fn of state.listeners) {
      try { fn({ phase, detail, mode: state.mode }); } catch (_) { /* a bad listener must not stop a boot */ }
    }
  }

  async function serverAvailable() {
    try {
      const r = await fetch('/api/health', { method: 'GET', cache: 'no-store' });
      if (!r.ok) return false;
      const d = await r.json();
      return d && d.ok !== false;
    } catch (_) {
      return false;
    }
  }

  async function bootPyodide() {
    status('loading-runtime', 'Fetching the Python runtime');
    const { loadPyodide } = await import(PYODIDE_CDN);
    const pyodide = await loadPyodide({
      indexURL: PYODIDE_CDN.replace('pyodide.mjs', ''),
    });

    status('loading-engine', 'Unpacking the WriteRoute engine');
    const manifest = await (await fetch('engine-manifest.json', { cache: 'no-store' })).json();
    const archive = await (await fetch(manifest.archive)).arrayBuffer();
    // Integrity is checked here rather than trusted: the archive and the manifest are
    // both served from this origin, but a stale cached zip would silently run an older
    // engine than the one the page claims to ship.
    const digest = await crypto.subtle.digest('SHA-256', archive);
    const hex = Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('');
    if (manifest.sha256 && hex !== manifest.sha256) {
      throw new Error(`engine archive checksum mismatch: expected ${manifest.sha256.slice(0, 12)}…, got ${hex.slice(0, 12)}…`);
    }
    await pyodide.unpackArchive(archive, 'zip');

    status('starting-engine', 'Starting the engine');
    await pyodide.runPythonAsync(`
import sys
if '/home/pyodide' not in sys.path:
    sys.path.insert(0, '/home/pyodide')
from writeroute.browser import route_json
import writeroute
_VERSION = writeroute.__version__
`);
    state.pyodide = pyodide;
    status('ready', `Engine ${pyodide.globals.get('_VERSION')} running in this browser`);
    return pyodide;
  }

  async function ensure() {
    if (state.mode) return state.mode;
    if (state.booting) return state.booting;
    state.booting = (async () => {
      if (await serverAvailable()) {
        state.mode = 'server';
        status('ready', 'Connected to the local WriteRoute service');
        return 'server';
      }
      await bootPyodide();
      state.mode = 'pyodide';
      return 'pyodide';
    })();
    try {
      return await state.booting;
    } finally {
      state.booting = null;
    }
  }

  async function callPyodide(route, payload) {
    const py = state.pyodide || await bootPyodide();
    const fn = py.globals.get('route_json');
    let raw;
    try {
      raw = fn(route, JSON.stringify(payload || {}));
    } finally {
      if (fn && fn.destroy) fn.destroy();
    }
    const parsed = JSON.parse(raw);
    if (parsed.error) {
      const err = new Error(parsed.error);
      err.status = parsed.status || 500;
      throw err;
    }
    return parsed.result;
  }

  async function callServer(route, payload, headers) {
    const r = await fetch(`/api/${route}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(headers || {}) },
      body: JSON.stringify(payload || {}),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      const err = new Error(d.detail || d.error || `request failed (${r.status})`);
      err.status = r.status;
      throw err;
    }
    return d;
  }

  async function call(route, payload, headers) {
    const mode = await ensure();
    return mode === 'server' ? callServer(route, payload, headers) : callPyodide(route, payload);
  }

  /* ---------------------------------------------------------------- providers */

  const PROVIDERS = {
    openai: { label: 'OpenAI', root: 'https://api.openai.com/v1', style: 'openai' },
    anthropic: { label: 'Anthropic', root: 'https://api.anthropic.com', style: 'anthropic' },
    deepseek: { label: 'DeepSeek', root: 'https://api.deepseek.com', style: 'openai' },
    openrouter: { label: 'OpenRouter', root: 'https://openrouter.ai/api/v1', style: 'openai',
                  // OpenRouter's catalogue is public, so the list loads before a key is entered.
                  publicModels: true },
    // OmniRoute is a self-hosted OpenAI-compatible gateway that fronts many providers,
    // including free tiers, from a container on the user's own machine. Its /v1/models
    // endpoint needs no key by default (REQUIRE_API_KEY=false), so discovery works as
    // soon as it is running. Listed as its own provider rather than buried under
    // "OpenAI-compatible" because the whole point is that nothing needs configuring.
    omniroute: { label: 'OmniRoute (self-hosted gateway)', root: 'http://localhost:20128/v1',
                 style: 'openai', publicModels: true, keyOptional: true },
    'openai-compatible': { label: 'OpenAI-compatible / local', root: '', style: 'openai',
                           keyOptional: true },
    // Chrome's built-in Gemini Nano. No key, no network, no cost: the model runs on the
    // machine. Availability is a browser capability rather than an account, so it is
    // detected at runtime and hidden where it does not exist.
    'chrome-nano': { label: 'Chrome built-in (Gemini Nano)', root: '', style: 'chrome' },
  };

  /* ------------------------------------------------------- Chrome built-in model */

  function chromeModelApi() {
    // The Prompt API moved from self.ai.languageModel to self.LanguageModel; support both
    // rather than pinning one Chrome version.
    return (typeof self !== 'undefined' && self.LanguageModel)
      || (typeof self !== 'undefined' && self.ai && self.ai.languageModel)
      || null;
  }

  async function chromeAvailability() {
    const api = chromeModelApi();
    if (!api) return 'unsupported';
    try {
      if (typeof api.availability === 'function') return await api.availability();
      if (typeof api.capabilities === 'function') {
        const caps = await api.capabilities();
        return caps.available === 'readily' ? 'available'
          : caps.available === 'after-download' ? 'downloadable' : 'unavailable';
      }
    } catch (_) { return 'unavailable'; }
    return 'unavailable';
  }

  async function chromeGenerate(contract, source, onProgress) {
    const api = chromeModelApi();
    if (!api) throw new Error('this browser has no built-in model; Chrome 138 or later is required');
    const state = await chromeAvailability();
    if (state === 'unavailable') {
      throw new Error('Chrome reports the built-in model as unavailable on this device');
    }
    const session = await api.create({
      initialPrompts: [{ role: 'system', content: SYSTEM }],
      monitor(m) {
        m.addEventListener('downloadprogress', e => {
          if (onProgress) onProgress(Math.round((e.loaded || 0) * 100));
        });
      },
    });
    try {
      const out = await session.prompt(`EDITORIAL CONTRACT\n${contract}\n\nSOURCE DOCUMENT\n${source}`);
      return String(out || '').trim();
    } finally {
      if (session.destroy) session.destroy();
    }
  }

  function providerRoot(provider, baseUrl) {
    const spec = PROVIDERS[provider] || PROVIDERS['openai-compatible'];
    const root = (baseUrl || '').trim().replace(/\/+$/, '') || spec.root;
    if (!root) throw new Error('a base URL is required for an OpenAI-compatible provider');
    return { spec, root };
  }

  /* Ask the provider what it can run, rather than making anyone type a model ID from
   * memory. Model names change constantly and a hard-coded list is wrong within weeks;
   * a typo produces a 404 from the provider that reads like a bug in this app. Every
   * supported provider exposes a models endpoint, so this uses the provider as the
   * source of truth. The key goes straight from the tab to the provider, as with a
   * rewrite. */
  async function listModels(provider, { apiKey = '', baseUrl = '' } = {}) {
    if (provider === 'chrome-nano') {
      const availability = await chromeAvailability();
      if (availability === 'unsupported') {
        throw new Error('this browser has no built-in model; Chrome 138 or later is required');
      }
      return {
        provider, label: PROVIDERS['chrome-nano'].label, availability,
        models: [{ id: 'gemini-nano', label: 'Gemini Nano (on this device)', free: true }],
      };
    }
    const { spec, root } = providerRoot(provider, baseUrl);
    const anthropic = spec.style === 'anthropic';
    if (!apiKey && !spec.publicModels && !spec.keyOptional) {
      throw new Error('add your API key to load the model list');
    }
    const url = anthropic ? `${root}/v1/models?limit=100` : `${root}/models`;
    const headers = anthropic
      ? { 'x-api-key': apiKey, 'anthropic-version': '2023-06-01',
          'anthropic-dangerous-direct-browser-access': 'true' }
      : (apiKey ? { authorization: `Bearer ${apiKey}` } : {});

    let response;
    try {
      response = await fetch(url, { headers });
    } catch (exc) {
      throw new Error(
        spec.root && /localhost|127\.0\.0\.1/.test(root)
          ? `could not reach ${spec.label} at ${root}. Is it running?`
          : `could not reach ${spec.label} at ${root}: ${exc.message}`);
    }
    const body = await response.text();
    if (!response.ok) {
      throw new Error(`${spec.label} returned HTTP ${response.status}: ${body.slice(0, 180)}`);
    }
    let payload;
    try { payload = JSON.parse(body); }
    catch (_) { throw new Error(`${spec.label} did not return JSON from ${url}`); }

    const rows = payload.data || payload.models || [];
    const models = rows.map(row => {
      // OpenRouter prices per token as decimal strings; "0" for both means free to run.
      const pricing = row.pricing || {};
      const id = row.id || row.name || row.model || '';
      const free = /:free$/.test(id)
        || /^auto\/best-free$/.test(id)
        || row.free === true
        || (pricing.prompt !== undefined && Number(pricing.prompt) === 0
            && Number(pricing.completion || 0) === 0);
      const arch = row.architecture || {};
      return {
        id,
        label: row.display_name || row.name || id,
        context: row.context_length || row.context_window || null,
        free: Boolean(free),
        outputs: arch.output_modalities || null,
      };
    }).filter(m => m.id);

    if (!models.length) throw new Error(`${spec.label} listed no models`);

    // Text-generation models only. Embedding, audio, image and moderation endpoints share
    // the same catalogue and cannot rewrite a document. Declared output modalities decide
    // it where the provider publishes them, because a name check is not enough: OpenRouter
    // prices Lyria's audio per second, so both token prices are zero and it sorted to the
    // top of the free list as though it were a free text model. Its declared output is
    // text+audio. The name pattern is the fallback for providers that declare nothing.
    const excluded = /(embed|whisper|tts|audio|speech|moderation|image|dall-e|vision-encoder|rerank|guard|lyria|imagen|veo|sora)/i;
    const usable = models.filter(m => {
      if (Array.isArray(m.outputs) && m.outputs.length) {
        return m.outputs.includes('text')
          && !m.outputs.some(o => o === 'audio' || o === 'image' || o === 'video');
      }
      return !excluded.test(m.id);
    });
    // Free models first, so a reader who has no budget sees what costs nothing without
    // scrolling a list of several hundred entries.
    const sorted = (usable.length ? usable : models)
      .sort((a, b) => (Number(b.free) - Number(a.free)) || a.id.localeCompare(b.id));
    return { provider, label: spec.label, models: sorted,
             freeCount: sorted.filter(m => m.free).length };
  }

  function providerRequest(provider, { apiKey, model, baseUrl, temperature, system, user }) {
    const { spec, root } = providerRoot(provider, baseUrl);
    if (spec.style === 'anthropic') {
      return {
        url: `${root}/v1/messages`,
        headers: {
          'content-type': 'application/json',
          'x-api-key': apiKey,
          'anthropic-version': '2023-06-01',
          // Required for a browser-origin call; the key stays in this tab either way.
          'anthropic-dangerous-direct-browser-access': 'true',
        },
        body: {
          model, max_tokens: 8192, temperature, system,
          messages: [{ role: 'user', content: user }],
        },
        read: d => (d.content || []).filter(b => b.type === 'text').map(b => b.text).join('').trim(),
      };
    }
    const headers = { 'content-type': 'application/json', authorization: `Bearer ${apiKey}` };
    if (provider === 'openrouter') headers['x-title'] = 'WriteRoute Studio';
    return {
      url: `${root}/chat/completions`,
      headers,
      body: {
        model, temperature,
        messages: [{ role: 'system', content: system }, { role: 'user', content: user }],
      },
      read: d => {
        let c = d?.choices?.[0]?.message?.content;
        if (Array.isArray(c)) c = c.map(x => (typeof x === 'string' ? x : x?.text || '')).join('');
        return String(c || '').trim();
      },
    };
  }

  const SYSTEM = "You are WriteRoute's revision engine. Follow the editorial contract exactly. Return only the revised document text.";

  async function generateCandidate(provider, opts, contract, source) {
    if (provider === 'chrome-nano') return chromeGenerate(contract, source, opts.onProgress);
    const req = providerRequest(provider, { ...opts, system: SYSTEM, user: `EDITORIAL CONTRACT\n${contract}\n\nSOURCE DOCUMENT\n${source}` });
    const r = await fetch(req.url, { method: 'POST', headers: req.headers, body: JSON.stringify(req.body) });
    const text = await r.text();
    let data;
    try { data = JSON.parse(text); } catch (_) { throw new Error(`provider returned non-JSON (${r.status}): ${text.slice(0, 200)}`); }
    if (!r.ok) throw new Error(`provider returned HTTP ${r.status}: ${text.slice(0, 400)}`);
    const out = req.read(data);
    if (!out) throw new Error('provider returned an empty candidate');
    return out;
  }

  /* Rewrite: contract from Python, candidates from the provider, adjudication in
   * Python. The gates never move to JavaScript. */
  async function rewrite({ text, genre, provider, apiKey, model, baseUrl, candidates = 3, temperature = 0.25, sourceText = false, onProgress }) {
    const spec = PROVIDERS[provider] || PROVIDERS['openai-compatible'];
    const local = provider === 'chrome-nano';
    if (!local && !apiKey && !spec.keyOptional) {
      throw new Error('add your API key to use generative rewrite');
    }
    if (!local && !model) throw new Error('choose a model');

    const plan = await call('contract', { text, genre, candidates });
    if (!plan.eligible) {
      return { changed: false, finalText: text, auditBefore: plan.auditBefore, auditAfter: plan.auditBefore, attempts: [], reason: plan.reason };
    }

    const settled = await Promise.allSettled(
      plan.contracts.map(c => generateCandidate(provider, { apiKey, model, baseUrl, temperature, onProgress }, c, text)),
    );
    const generated = settled.filter(s => s.status === 'fulfilled').map(s => s.value);
    const failures = settled.filter(s => s.status === 'rejected').map(s => String(s.reason && s.reason.message || s.reason));

    if (!generated.length) {
      // Fall back to the deterministic repair rather than reporting nothing: the safe
      // baseline does not depend on the provider having answered.
      const safe = await call('repair', { text, genre, source_text: sourceText });
      safe.providerErrors = failures;
      safe.reason = safe.reason || `no provider candidate was produced (${failures[0] || 'unknown error'})`;
      return safe;
    }

    const result = await call('rewrite', { text, genre, candidates: generated, source_text: sourceText });
    if (failures.length) result.providerErrors = failures;
    return result;
  }

  return { call, ensure, onStatus, rewrite, listModels, chromeAvailability,
           providers: PROVIDERS, get mode() { return state.mode; } };
})();

export default WriteRoute;
