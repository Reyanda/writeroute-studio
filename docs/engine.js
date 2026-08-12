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
    openai: { root: 'https://api.openai.com/v1', style: 'openai' },
    deepseek: { root: 'https://api.deepseek.com', style: 'openai' },
    openrouter: { root: 'https://openrouter.ai/api/v1', style: 'openai' },
    anthropic: { root: 'https://api.anthropic.com', style: 'anthropic' },
    'openai-compatible': { root: '', style: 'openai' },
  };

  function providerRequest(provider, { apiKey, model, baseUrl, temperature, system, user }) {
    const spec = PROVIDERS[provider] || PROVIDERS['openai-compatible'];
    const root = (baseUrl || '').trim().replace(/\/+$/, '') || spec.root;
    if (!root) throw new Error('a base URL is required for an OpenAI-compatible provider');
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
  async function rewrite({ text, genre, provider, apiKey, model, baseUrl, candidates = 3, temperature = 0.25, sourceText = false }) {
    if (!apiKey) throw new Error('add your API key to use generative rewrite');
    if (!model) throw new Error('enter the provider model ID');

    const plan = await call('contract', { text, genre, candidates });
    if (!plan.eligible) {
      return { changed: false, finalText: text, auditBefore: plan.auditBefore, auditAfter: plan.auditBefore, attempts: [], reason: plan.reason };
    }

    const settled = await Promise.allSettled(
      plan.contracts.map(c => generateCandidate(provider, { apiKey, model, baseUrl, temperature }, c, text)),
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

  return { call, ensure, onStatus, rewrite, providers: Object.keys(PROVIDERS), get mode() { return state.mode; } };
})();

export default WriteRoute;
