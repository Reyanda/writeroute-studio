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

### Languages

Segmentation works across scripts. Sentence terminators include `。！？` (CJK), `।॥`
(Devanagari), `۔؟؛` (Urdu and Arabic), `።፧` (Ethiopic), `·` (Greek) and `։` (Armenian),
and words are matched by Unicode letter class rather than the Latin range. CJK is written
without spaces, so each ideograph counts as one token — the usual approximation where no
segmentation dictionary is available.

Verified on English, French, Spanish, German, Chinese, Japanese, Arabic, Hindi, Russian,
Greek, Swahili and Chichewa. Before this, a Chinese, Arabic, Hindi or Russian document
tokenised to **zero words** and read as a single unbroken sentence, so every statistic the
audit reports was meaningless. The pattern catalogue itself is still written in English
and will not recognise, say, a Mandarin assistant preface.

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

Rewriting with a language model is optional. Three routes cost nothing:

| Provider | Key | Notes |
|---|---|---|
| Chrome built-in (Gemini Nano) | none | Runs on the machine. Chrome 138+. Chrome downloads the model on first use. |
| OmniRoute | none by default | Self-hosted gateway fronting many providers including free tiers. Start it with `npx omniroute`; the studio looks for `http://localhost:20128/v1`. |
| OpenRouter | none to browse | Publishes its catalogue publicly; free models are listed first. A key is needed to run one. |

OpenAI, Anthropic, DeepSeek and any OpenAI-compatible endpoint work with a key.

Model IDs are discovered from the provider rather than typed. Names change constantly and
a hard-coded list goes stale, so the studio calls the provider's models endpoint and
offers what comes back, filtering out anything that cannot produce text. Your browser
calls the provider directly, so the key never reaches a server we run and is not saved.

The model only proposes. The tournament that ranks candidates and the gates above run in
Python, and a model candidate must also beat the deterministic repair to win. When
nothing clears the gates, your original comes back unchanged.

## Opening documents

A menu bar sits above the editor: **File** to open and export, **Review** to audit and
repair, **View** for focus mode and theme. `⌘O` opens a file, and dropping one anywhere on
the window works too.

Reads TXT, Markdown, reStructuredText, CSV, log files, DOCX, PDF with a text layer, and
RTF. Exports to DOCX, Markdown, plain text and HTML. All of it happens in the tab; nothing
is uploaded.

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

| Measure | As released | Defects fixed | Allow-list |
|---|---:|---:|---:|
| Documents the auditor could not finish | 1 | 0 | 0 |
| Audit of an 8 KB markdown paste with a code block | 21.4 s | 0.20 s | 0.20 s |
| False positives on published prose | 43 | 22 | **6** |
| False positives per 1,000 words of published prose | 0.97 | 0.50 | **0.14** |
| Control documents carrying any false positive | 7 of 24 | 7 of 24 | **2 of 24** |
| Table cells audited as sentences | 16 | 5 | 5 |
| Published articles told to "rebuild" | 3 | 1 | 1 |
| Meaning-preservation failures, independently re-checked | 0 | 0 | 0 |
| Source-text mode byte-exact | all | all | all |

Full aggregates, with document titles withheld because the corpus is a private research
archive: [`docs/benchmark.json`](docs/benchmark.json).

### The allow-list

Ten families of field-standard wording were being read as claims: WHO/JMP service labels
("improved water source", "safe drinking water"), PRISMA and reporting-checklist wording,
conditional treatment instructions ("repeat bolus in second hour if improved"), descriptive
measurements, and standard epidemiological risk phrasing. `writeroute/data/allowlists/`
holds them as data. Two rules govern every entry: it is written against a sentence the
benchmark actually produced, quoted in the entry's `evidence` field; and it is narrow
enough that the same word still fires when it really is a claim — "improved water sources"
is excused, "improved survival" is not. Every suppression appears in
`metrics.allowListExemptions` with its reason.

### Known limitations

- **The allow-list fixed the control class, not the author class.** Hard findings on the
  author's own documents moved from 47 to 43, because they are a different family: causal
  attribution inside prose, participial summary claims such as "demonstrating feasibility
  and value", and vague attribution. Those are largely the checks working as intended, but
  nine of the 25 author documents are still graded as needing a rebuild, which is a heavier
  verdict than that evidence supports.
- **The allow-list is domain-specific.** It is tuned to clinical nutrition and
  epidemiology and will not help with another field's standard vocabulary. Adding a domain
  means adding entries under the same two rules.
- **Genre inference is unreliable** — it agreed with the correct profile on none of the
  author-class documents. The studio therefore asks you to choose. `"auto"` still works
  and sets `metrics.genreAssumed` so a caller cannot mistake a guess for a choice.
- **This is not a blinded comparison against other tools.** It is one corpus, in one
  domain, measured honestly. The frozen regression cases in `evals/` are authored
  alongside the engine they gate, which limits what they can establish.
- **PDF decoding has no automated coverage.** `tests/js` covers the DOCX round trip, the
  export layer and provider discovery, but the PDF path depends on pdf.js loading from a
  CDN and is exercised by hand only.
- **The Chrome built-in model is small.** Gemini Nano is useful for tightening a
  paragraph and will not match a frontier model on a long manuscript. The preservation
  gates apply to it exactly as they do to any other candidate.
- **The logo is raster only.** `assets/logo-source.png` is a 1254×1254 PNG with the
  background, wordmark and tagline all baked in. `scripts/build_logo.py` derives a
  transparent mark, wordmark, lockup and favicon from it by knocking out the
  border-connected background. A vector original would be better than any of it.

## Tests

```bash
./scripts/validate.sh
```

Runs everything: 81 Python tests (42 engine, 4 end-to-end through the local service, 17
Gate 0 regressions, 8 allow-list regressions with 21 subtests, 10 structural-detector
regressions), 28 JavaScript tests for the
browser document layer, multilingual round trips and provider discovery, a rebuild of `docs/` with a checksum check, and a grep that fails
the build if the landing page ever claims an authorship verdict.

The JavaScript suite needs jsdom once: `npm install --no-save jsdom`.

## Layout

```
writeroute/        the engine — audit, substance, structure, integrity, candidates,
                   genres, voice, contracts, formatting, allow-list, browser dispatch
aiwd/              textmodel and statistics only; see aiwd/__init__.py for why
app.py             FastAPI service for local use
static/            studio and landing sources
scripts/           logo and site builders
docs/              generated static site (GitHub Pages)
evals/             frozen regression cases
examples/          worked audit, suggestion, repair and source-lock outputs
```

MIT licensed.
