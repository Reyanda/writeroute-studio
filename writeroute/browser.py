"""The surface the browser build calls, mirroring the local server's HTTP routes.

One dispatch function, JSON in and JSON out, so the front end can talk to a local
FastAPI process or to this module running under Pyodide without knowing which. The
routes and their payload keys are deliberately identical to `app.py`.

Two things stay outside Python in the browser:

* the provider call. Pyodide has no sockets, so JavaScript performs the fetch and
  passes the candidate strings to `rewrite`. The key therefore never reaches a server
  — not a provider proxy, not ours. The gates that decide whether a candidate is
  acceptable still run here, in the same code the server path runs.
* file decoding. The browser hands over already-extracted text.

Nothing in this module infers authorship, and no route mutates text that the audit
could not read.
"""
from __future__ import annotations

import json
from typing import Any

from .audit import audit_text
from .formatting import formatting_advice
from .genres import get_genre, load_genres
from .route import repair_text, rewrite_with_candidates, suggest_text, verify_text

MAX_CHARS = 300_000


class RouteError(Exception):
    """A caller-visible failure: bad input or an unknown route."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _text(payload: dict[str, Any], key: str = "text") -> str:
    value = payload.get(key) or ""
    if not isinstance(value, str) or not value.strip():
        raise RouteError(f"{key} is required")
    if len(value) > MAX_CHARS:
        raise RouteError(f"{key} exceeds the {MAX_CHARS:,}-character limit", 413)
    return value


def _genre(payload: dict[str, Any]) -> str:
    """Genre is required, and "auto" is not a genre.

    Inference agreed with the correct profile on none of the author-class documents in
    the benchmark corpus, so the front end must ask rather than guess. A caller that
    genuinely wants the guess passes "auto" explicitly and gets `genreAssumed: true`
    back in the metrics.
    """
    genre = (payload.get("genre") or "").strip()
    if not genre:
        raise RouteError("genre is required; pass 'auto' explicitly to accept an inferred genre")
    # Aliases count. The profiles carry them — "essay" resolves to "op-ed" — and a check
    # against ids alone rejected a genre the engine understands and the studio offers.
    known = {"auto"}
    for profile in load_genres().values():
        known.add(profile.id)
        known.update(profile.aliases)
    if genre not in known:
        raise RouteError(f"unknown genre {genre!r}; expected one of {', '.join(sorted(known))}")
    return genre


def _with_formatting(report: dict[str, Any], text: str, genre: str) -> dict[str, Any]:
    return {"audit": report, "formatting": formatting_advice(text, report["genre"])}


def route(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one call. `name` matches the server path without the /api prefix."""
    if name == "health":
        return {
            "ok": True,
            "runtime": "pyodide",
            "genres": [
                {"id": g.id, "name": g.name, "purpose": g.purpose}
                for g in sorted(load_genres().values(), key=lambda g: g.name)
            ],
        }

    if name == "audit":
        text, genre = _text(payload), _genre(payload)
        report = audit_text(text, genre=genre,
                            include_quoted=bool(payload.get("include_quoted")))
        return _with_formatting(report.to_dict(), text, genre)

    if name == "suggest":
        text, genre = _text(payload), _genre(payload)
        limit = int(payload.get("max_candidates", 3))
        return suggest_text(text, genre, max_candidates=max(1, min(5, limit)),
                            source_text=bool(payload.get("source_text")))

    if name == "repair":
        text, genre = _text(payload), _genre(payload)
        return repair_text(text, genre, source_text=bool(payload.get("source_text")))

    if name == "verify":
        original = _text(payload, "original")
        candidate = _text(payload, "candidate")
        return verify_text(original, candidate, _genre(payload))

    if name == "rewrite":
        # Candidates were generated in JavaScript. The tournament, the preservation
        # gate and the net-improvement gate all run here.
        text, genre = _text(payload), _genre(payload)
        candidates = payload.get("candidates") or []
        if not isinstance(candidates, list):
            raise RouteError("candidates must be a list of strings")
        strings = [c for c in candidates if isinstance(c, str) and c.strip()]
        if not strings:
            raise RouteError("no usable candidate text was supplied", 422)
        return rewrite_with_candidates(text, strings, genre,
                                       source_text=bool(payload.get("source_text")))

    if name == "contract":
        # The exact instruction the browser must send to the provider, so the two
        # paths cannot drift apart.
        from .contracts import compile_revision_contract
        from .route import candidate_contract

        text, genre = _text(payload), _genre(payload)
        selected = get_genre(genre if genre != "auto" else audit_text(text, genre="auto").genre)
        before = audit_text(text, genre=selected.id)
        base = compile_revision_contract(text, before, selected)
        count = max(1, min(5, int(payload.get("candidates", 3))))
        return {
            "genre": selected.id,
            "auditBefore": before.to_dict(),
            "contracts": [candidate_contract(base, i) for i in range(1, count + 1)],
            "eligible": before.status not in {"clean", "not_assessable"},
            "reason": (
                "clean input needs no rewrite" if before.status == "clean"
                else "document not assessable: most of it is protected content"
                if before.status == "not_assessable" else ""
            ),
        }

    if name == "formatting":
        text, genre = _text(payload), _genre(payload)
        resolved = genre if genre != "auto" else audit_text(text, genre="auto").genre
        return formatting_advice(text, resolved)

    raise RouteError(f"unknown route {name!r}", 404)


def route_json(name: str, payload_json: str) -> str:
    """JSON-string wrapper. Pyodide marshals strings cheaply and without proxies."""
    try:
        payload = json.loads(payload_json or "{}")
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"invalid JSON payload: {exc}", "status": 400})
    try:
        return json.dumps({"ok": True, "result": route(name, payload)})
    except RouteError as exc:
        return json.dumps({"error": str(exc), "status": exc.status})
    except ValueError as exc:
        return json.dumps({"error": str(exc), "status": 422})
    except Exception as exc:  # surfaced to the user rather than swallowed
        return json.dumps({"error": f"{type(exc).__name__}: {exc}", "status": 500})
