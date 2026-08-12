# Release notes

## 1.0.0

First public release. Merges the WriteRoute 2.0.0 engine and the Studio front end into
one tree, fixes the four defects a 74-document benchmark found in that engine, and ships
the whole thing as a static site that runs in the browser.

### Fixed

- **The auditor hung on markdown containing a fenced code block.** Protected spans were
  blanked to spaces, so a code fence became one long whitespace run and every pattern
  quantifying whitespace had to divide it. Masking now writes NUL, and the bold-label
  scan is anchored per line. An 8 KB paste went from 21.4 s to 0.20 s over HTTP; 120 KB
  now audits in 0.18 s.
- **A document could be graded from a mask.** Span merging once covered 87,973 of 88,283
  characters of a real document and the audit still returned a gradeable verdict.
  Coverage is now reported, and at or above 50% the status is `not_assessable`; repair
  and rewrite return the text untouched with the coverage in the reason.
- **Table cells were audited as sentences.** "Improved WASH | 0.39 | 0.15" was read as a
  causal claim. Tabular content is now protected: three or more cells on a line is a row
  on its own, and a two-column row counts only inside a run, so an isolated prose pipe
  cannot mask a paragraph.
- **Genre was guessed silently.** Inference agreed with the correct profile on none of the
  author-class benchmark documents, and genre sets the severity thresholds. It is now a
  required choice everywhere; `"auto"` still works and sets `metrics.genreAssumed`.

### Changed

- The engine no longer ships a copy of the private detection engine. A previous build
  bundled a stale `aiwd` whose `scoring`, `skillengine` and feature packs predated
  false-positive work done elsewhere, so installing it silently reverted that work. Only
  `aiwd/textmodel.py` and `aiwd/statistics.py` remain — the two modules `writeroute`
  actually imports.
- **The BYOK key no longer transits a server.** The previous service accepted a
  client-supplied `base_url` and forwarded the request server-side with the key attached,
  which is an open credential-forwarding proxy once hosted. The browser now calls the
  provider directly; the tournament and every preservation gate still run in Python.
- `rewrite_with_candidates` and `run_tournament` split the generator from the
  adjudication, with `_pre_tournament_guard` shared by both paths so they cannot disagree
  about when mutation is permitted.
- `formatting_advice` moved from `app.py` into `writeroute/formatting.py`; two front ends
  consume it and one copy is the point.
- The local service serves the landing page at `/` and the studio at `/studio`.

### Corrections to earlier reporting

- An earlier benchmark run read the integrity verdict from a key named `ok`, which the
  report does not emit — it emits `passes`. That check was therefore vacuous. Re-measured
  against the real key: 0 preservation failures across 74 documents, 10 of which were
  changed by repair. The number did not move; the check now actually happens.
- Two bugs in the new client-side file layer were found by hand-testing in a browser and
  fixed: a DOCX export/re-import round trip lost a paragraph-internal line break and
  fused two sentences, changing what the audit measured; and the genre validator rejected
  `essay`, an alias the engine understands and the studio offers.

### Not claimed

No blinded or independent comparison against other tools. The claim-support layer still
over-fires on clinical and epidemiological prose; see the known limitations in the README.
