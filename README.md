# WriteRoute

An editing tool for documents where the facts matter. It lists specific problems in your text, quotes the sentence each one is in, and suggests the narrowest fix. It blocks any rewrite that alters a number, a modal verb, a negation, or who said something. It does not estimate how much of your writing was produced by a machine.

---

## What it checks

Three sets of checks run over every document. They are reported separately, so a polished sentence with no support behind it does not pass on fluency.

- **SURFACE — Wording**: Assistant prefaces, throat-clearing, formulaic contrasts, importance puffery, stacked hedges, nominalisations, leftover formatting.
- **SUBSTANCE — Claims**: Causal wording where the design is observational. Statistical significance reported with no effect estimate. Novelty claims with no stated comparison set. Safety and efficacy claims with no condition attached.
- **STRUCTURE — Document shape**: Repeated paragraph templates, uniform sentence length, connective scaffolding, recap loops, conclusions that restate an earlier paragraph.

---

## What it will not change

Every proposed edit is compared against the original before it can be applied, whether a rule or a language model produced it. If any of the following differs, the edit is rejected.

- **Values**: magnitudes, units, dates, statistical estimates
- **Names**: people, organisations, acronyms, defined terms
- **Sources**: citations, quotations, URLs, DOIs, cross-references
- **Code**: commands, API names, file paths, versions
- **Negation**: "did not improve" cannot become "improved"
- **Scope**: only, all, some, at least
- **Modal force**: must, shall, should, may, might
- **Comparison direction**: higher and lower cannot swap
- **Causal strength**: "was associated with" cannot become "caused"
- **Attribution**: who made the claim

Source-text mode handles official, legal, archival and third-party documents. It runs the audit and shows the suggestions, returns the file byte for byte, and marks every proposed edit as not applicable.

Documents that are mostly tables, code or quotation are reported as not assessable. Too little prose remains for the result to mean anything, and the audit says so instead of returning a clean verdict.

---

## Where your text goes

Nowhere. The page loads the Python engine and runs it in your browser under Pyodide. Auditing, suggestions, repairs and verification need no account, no server and no API key. Rewriting with a language model is optional, and there are three ways to do it at no cost.

### Three ways to rewrite for nothing
- **Chrome 138 and later** ship Gemini Nano, which runs on your machine with no key and no network.
- **OmniRoute** is a gateway you host yourself; start it and the studio finds it on `localhost:20128`.
- **OpenRouter** publishes its catalogue without a key, and its free models are listed first.

### Or bring a key, and keep it
OpenAI, Anthropic, DeepSeek and any OpenAI-compatible endpoint also work. The studio asks the provider which models it can run, so nothing is typed from memory and no list goes stale. Your browser calls the provider directly: the key never reaches a server we run, and it is not saved.

Whichever you pick, the model only supplies candidate text. Ranking and the checks above run in the Python engine in your browser, a model candidate must beat the rule-based repair to be offered, and if nothing passes you get your original back.

---

## Benchmark

The engine was tested on 179,807 words across 74 documents from a working doctoral research archive: 24 third-party journal articles and supplements, 25 submission-bound documents by the archive's owner, and 25 sets of working notes written by software agents.

The journal articles are the control. Other people wrote them and journals accepted them, so anything flagged there is an error by the tool. Four defects were found in the released version. The table shows what fixing them changed.

Full aggregates in [benchmark.json](https://reyanda.github.io/writeroute-studio/benchmark.json). Document titles are withheld; the corpus is a private research archive.

| Measure | As released | Defects fixed | Allow-list |
| :--- | :--- | :--- | :--- |
| Documents the auditor could not finish | 1 | 0 | 0 |
| Audit of an 8 KB markdown paste containing a code block | 21.4 s | 0.20 s | 0.20 s |
| Errors on published prose | 43 | 22 | 6 |
| Errors per 1,000 words of published prose | 0.97 | 0.50 | 0.14 |
| Control documents carrying any error | 7 of 24 | 7 of 24 | 2 of 24 |
| Published articles told to "rebuild" | 3 | 1 | 1 |
| Meaning-preservation failures, independently re-checked | 0 | 0 | 0 |
| Source-text mode byte-exact | all | all | all |

The last column comes from an allow-list. Ten families of standard terminology were being read as claims: WHO/JMP service labels such as "improved water source" and "safe drinking water", PRISMA checklist wording, treatment instructions such as "repeat bolus in second hour if improved", and descriptive measurements. Each entry is written against a sentence the benchmark produced, and each is narrow enough that the same word still fires when it carries a claim. "Improved water sources" is excused; "improved survival" is not. Suppressions appear in the audit with a reason.

---

## What is still wrong

The allow-list fixed the control class and had little effect on the author's own documents, where findings went from 47 to 43. Those are a different group: causal attribution inside prose, participial summary claims such as "demonstrating feasibility and value", and vague attribution. Most look correct, but nine of 25 documents are still graded as needing a rebuild, which overstates what the findings support.

Genre detection is unreliable, so the studio asks you which kind of document you are writing. Picking the wrong one changes the severity thresholds.

There is no blinded comparison against other tools. This is one corpus in one field, clinical nutrition and epidemiology. The allow-list is written for that field and will not cover another one's standard vocabulary.

---

## Document types

Ten profiles set which checks are strict, which are advisory, and which are disabled. Passive voice in a methods section and hedging in a legal clause are conventions of the form, so the profile switches those checks off.

- Scientific journal
- Systematic review
- Policy brief
- Professional report
- Grant proposal
- Legal
- Technical documentation
- Correspondence
- Essay and commentary
- General professional

---

## Install

The studio needs nothing installed. To run the engine on your own machine, for a long manuscript or for scripted use, the same package works as a local service and as a command-line tool.

### Local service

```bash
# from the repository root
pip install -r requirements.txt
./run.sh
# then open http://127.0.0.1:8744
```

### Command line

```bash
python -m writeroute audit manuscript.md --genre scientific --json
python -m writeroute repair manuscript.md --genre scientific -o manuscript.clean.md
python -m writeroute repair regulation.txt --genre legal --source-text
```

The local service binds to `127.0.0.1` and has no authentication. Do not expose it to a network.
