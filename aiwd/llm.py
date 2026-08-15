"""LLM revision callbacks for the controlled rewriting loop.

DeepSeek is the default provider. Its API is OpenAI-compatible plain JSON, so
the callback uses stdlib urllib — no SDK dependency. The Claude callback is
kept as an alternative (lazy anthropic import). Both produce the same
model-agnostic `(contract, passage) -> revision` callable that revision.py
consumes; the preservation gate treats every provider identically.

Credentials: DEEPSEEK_API_KEY env var (or api_key=). Claude: SDK chain.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .revision import RevisionCallback

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"
CLAUDE_MODEL = "claude-opus-4-8"

_SYSTEM = """You are a precise human editor executing a bounded revision contract.

You repair only the defects the contract names, using the minimum effective edit.
You preserve the writer's meaning, claims, and voice. You never touch the listed
invariants: numbers, thresholds, normative modals (must/shall/should/may/required/
prohibited/mandatory), citations, URLs, or quotations. If repairing a defect would
require changing an invariant, leave that defect unrepaired.

You return only the revised passage — no preamble, no explanation, no fences."""


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip()
        if text.startswith(("text\n", "markdown\n", "md\n")):
            text = text.split("\n", 1)[1]
    return text.strip()


def deepseek_callback(model: str = DEEPSEEK_MODEL, api_key: str | None = None,
                      base_url: str = DEEPSEEK_BASE_URL, max_tokens: int = 8000,
                      timeout: float = 300.0) -> RevisionCallback:
    """Build a revision callback backed by the DeepSeek API (stdlib only)."""
    key = api_key or os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set (and no api_key was passed)")

    def revise(contract: str, passage: str) -> str:
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user",
                 "content": f"{contract}\n\n<passage>\n{passage}\n</passage>"},
            ],
        }
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise RuntimeError(f"DeepSeek API error {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"DeepSeek API unreachable: {exc.reason}") from exc
        choice = body["choices"][0]
        if choice.get("finish_reason") == "length":
            raise RuntimeError("DeepSeek response truncated (finish_reason=length); "
                               "raise max_tokens or split the passage")
        return _strip_fences(choice["message"]["content"])

    return revise


def claude_callback(model: str = CLAUDE_MODEL, max_tokens: int = 16000) -> RevisionCallback:
    """Build a revision callback backed by the Claude API (optional alternative)."""
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "The anthropic SDK is required for the Claude callback: pip install anthropic"
        ) from exc

    client = anthropic.Anthropic()

    def revise(contract: str, passage: str) -> str:
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            system=[{
                "type": "text",
                "text": _SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": f"{contract}\n\n<passage>\n{passage}\n</passage>",
            }],
        ) as stream:
            message = stream.get_final_message()
        if message.stop_reason == "refusal":
            raise RuntimeError("Claude declined the revision request (stop_reason=refusal)")
        return _strip_fences(
            "".join(b.text for b in message.content if b.type == "text"))

    return revise


PROVIDERS = {"deepseek": deepseek_callback, "claude": claude_callback}
DEFAULT_MODELS = {"deepseek": DEEPSEEK_MODEL, "claude": CLAUDE_MODEL}


def get_callback(provider: str = "deepseek", model: str | None = None) -> RevisionCallback:
    if provider not in PROVIDERS:
        raise RuntimeError(f"unknown provider {provider!r}; one of {sorted(PROVIDERS)}")
    return PROVIDERS[provider](model=model or DEFAULT_MODELS[provider])
