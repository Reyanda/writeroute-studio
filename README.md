# WriteRoute

A preservation-first writing and editing route. It names observable editorial defects in
a document, proposes the smallest edit that fixes each one, and refuses any rewrite that
changes a number, a modal verb, a negation, a scope word or an attribution.

It does not report a percentage of your prose as machine-written. Style does not identify
authorship, and a tool that claims otherwise is guessing at something it cannot see.

**Try it:** <https://reyanda.github.io/writeroute-studio/> — the engine runs in your
browser. Nothing is uploaded, because there is nowhere to upload it to.

---

## What it checks

Three layers, kept separate so that fluent-and-unsupported does not pass as clean.

| Layer | Examples |
|---|---|
| Surface | assistant meta prefaces, throat-clearing, formulaic contrasts, manufactured insight, importance puffery, stacked hedges, nominalisations, formatting debris |
| Substance | causal wording in an observational design, significance without an effect estimate, novelty claims with no bounded comparison, safety and efficacy claims with no stated condition |
| Shape | repeated paragraph templates, uniform cadence, connective scaffolding carrying the argument, recap loops, a conclusion restating an earlier paragraph |

Ten genre profiles — scientific, systematic review, policy brief, professional report,
grant, legal, technical, correspondence, essay/commentary, general — decide which
patterns are hard, which are advisory, and which are switched off because they describe
the register working correctly.

## What it refuses

Every candidate edit is compared against the original and rejected if any of these
changed: values and units and dates, statistical estimates, names and defined terms,
citations and quotations and URLs and DOIs, code and commands and file paths and
versions, negation, quantifier scope, modal force, comparison direction, causal strength,
source attribution.

Two further refusals:

- **Source-text mode** (`--source-text`) audits official, legal, archival or externally
  authored text and returns it byte for byte, marking every proposed edit as not
  applicable.
- **Not assessable.** When masking code, quotation and tabular content leaves too little
  prose to judge, the audit says so instead of returning `clean`. A verdict drawn from a
  mask is not a verdict.

## Where your text and your keys go

Auditing, suggesting, repairing and verifying are local. In the browser build the Python
engine runs in the tab under Pyodide; there is no account and no server.

Generative rewrite is optional and brings your own key. The browser calls the provider
directly — OpenAI, Anthropic, DeepSeek, OpenRouter, or any OpenAI-compatible endpoint
with a model ID you supply. The key is held in the page for the session, is not written
to storage, and does not pass through any server of ours.

The model only proposes. The tournament that ranks candidates and the gates above run in
Python, and a model candidate must also beat the deterministic repair to win. When
nothing clears the gates, your original comes back unchanged.

## Install and run

```bash
pip install -r requirements.txt
./run.sh          # http://127.0.0.1:8744
```

Or with Docker:

```bash
docker build -t writeroute .
docker run --rm -p 8744:8744 writeroute
```

Command line:

```bash
python -m writeroute audit manuscript.md --genre scientific --json
python -m writeroute suggest manuscript.md --genre scientific --json
python -m writeroute repair manuscript.md --genre scientific -o manuscript.clean.md
python -m writeroute verify manuscript.md manuscript.rewritten.md --genre scientific
python -m writeroute repair regulation.txt --genre legal --source-text
```

The local service binds to `127.0.0.1` and is built for one person on one machine. **It
has no authentication — do not expose it to a network.**

## Build the static site

```bash
python3 scripts/build_logo.py     # derive logo assets from assets/logo-source.png
python3 scripts/build_web.py      # write docs/ for GitHub Pages
```

`docs/` is generated: edit `static/` and rebuild. `build_web.py` zips the `writeroute`
and `aiwd` packages into `docs/writeroute-engine.zip` and records a SHA-256 in
`docs/engine-manifest.json`; the page verifies that checksum before running the archive,
so a stale cached zip cannot silently run an older engine than the page claims.

## Evidence

The engine was benchmarked on 179,807 words across 74 documents from a working doctoral
research archive: 24 third-party journal articles and supplements as a control class, 25
submission-bound author documents, and 25 machine-written working notes. The control
class matters most — it was written by other people and accepted by journals, so a hard
finding there is the tool being wrong.

Four defects were found and fixed. Measured before and after, same corpus:

| Measure | Before | After |
|---|---:|---:|
| Documents the auditor could not finish | 1 | 0 |
| Audit of an 8 KB markdown paste with a code block | 21.4 s | 0.20 s |
| False positives on published prose | 43 | 22 |
| Table cells audited as sentences | 16 | 5 |
| Published articles told to "rebuild" | 3 | 1 |
| Meaning-preservation failures, independently re-checked | 0 | 0 |
| Source-text mode byte-exact | all | all |

### Known limitations

- **The claim-support layer over-fires on clinical and epidemiological prose.** It reads
  "improved water source" and "safe drinking water" as causal and safety claims when they
  are standard WHO/JMP category labels, and it treats reporting-checklist boilerplate the
  same way. Nine of the 25 author documents are still graded as needing a rebuild largely
  on that basis. A domain term allow-list is the next piece of work and is not done.
- **Genre inference is unreliable** — it agreed with the correct profile on none of the
  author-class documents. The studio therefore asks you to choose. `"auto"` still works
  and sets `metrics.genreAssumed` so a caller cannot mistake a guess for a choice.
- **This is not a blinded comparison against other tools.** It is one corpus, in one
  domain, measured honestly. The frozen regression cases in `evals/` are authored
  alongside the engine they gate, which limits what they can establish.
- **No JavaScript test harness.** `static/files.js` (client-side DOCX/PDF decoding and
  export) is verified by hand in a browser, not by an automated suite. Two bugs were
  found and fixed that way; a third would not be caught automatically.
- **The logo is raster only.** `assets/logo-source.png` is a 1254×1254 PNG with the
  background, wordmark and tagline all baked in. `scripts/build_logo.py` derives a
  transparent mark, wordmark, lockup and favicon from it by knocking out the
  border-connected background. A vector original would be better than any of it.

## Tests

```bash
python3 -m pytest tests -q
```

63 tests: 42 engine, 4 end-to-end through the local service, 17 regressions that pin each
measured defect above rather than a paraphrase of it.

## Layout

```
writeroute/        the engine — audit, substance, structure, integrity, candidates,
                   genres, voice, contracts, formatting, browser dispatch
aiwd/              textmodel and statistics only; see aiwd/__init__.py for why
app.py             FastAPI service for local use
static/            studio and landing sources
scripts/           logo and site builders
docs/              generated static site (GitHub Pages)
evals/             frozen regression cases
examples/          worked audit, suggestion, repair and source-lock outputs
```

MIT licensed.
