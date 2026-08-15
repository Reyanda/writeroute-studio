from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class LucidFinding:
    id: str
    category: str
    message: str
    matched_text: str
    start: int
    end: int
    severity: str = "warning"
    suggestion: str = ""


class LucidSciEvaluator:
    def __init__(self, config_path: str | Path | None = None) -> None:
        if config_path is None:
            config_path = Path(__file__).parent / "lint_rules.yaml"
        self.config_path = Path(config_path)
        with open(self.config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)
        
        self.flag_terms = self.config.get("flag_terms", {})
        self.sentence_triggers = self.config.get("sentence_triggers", {})

    def evaluate(self, text: str) -> dict[str, Any]:
        findings: list[LucidFinding] = []
        
        # 1. Flag term scans
        for category, terms in self.flag_terms.items():
            for term in terms:
                pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
                for m in pattern.finditer(text):
                    findings.append(
                        LucidFinding(
                            id=f"lucid_{category}",
                            category=category,
                            message=f"Flagged term in category '{category}': '{m.group(0)}'",
                            matched_text=m.group(0),
                            start=m.start(),
                            end=m.end(),
                            severity="critical" if category in ("ai_slop_phrases", "causal_watchlist") else "warning"
                        )
                    )

        # 2. Sentence complexity triggers
        sentences = re.split(r"(?<=[.!?])\s+", text)
        offset = 0
        long_sentence_count = 0
        for s in sentences:
            s_clean = s.strip()
            if not s_clean:
                continue
            words = s_clean.split()
            wcount = len(words)
            s_start = text.find(s_clean, offset)
            s_end = s_start + len(s_clean) if s_start != -1 else offset + len(s_clean)

            if wcount >= self.sentence_triggers.get("critical_word_count", 50):
                findings.append(
                    LucidFinding(
                        id="lucid_critical_sentence_length",
                        category="sentence_complexity",
                        message=f"Critical sentence length ({wcount} words >= 50). Consider splitting.",
                        matched_text=s_clean[:60] + "..." if len(s_clean) > 60 else s_clean,
                        start=s_start,
                        end=s_end,
                        severity="warning",
                    )
                )
                long_sentence_count += 1
            elif wcount >= self.sentence_triggers.get("review_word_count", 35):
                long_sentence_count += 1

            if s_start != -1:
                offset = s_end

        # Calculate a quality score (0 - 100)
        penalty = sum(10 if f.severity == "critical" else 4 for f in findings)
        score = max(0, min(100, 100 - penalty))

        return {
            "score": score,
            "findings_count": len(findings),
            "findings": [
                {
                    "id": f.id,
                    "category": f.category,
                    "message": f.message,
                    "matched_text": f.matched_text,
                    "start": f.start,
                    "end": f.end,
                    "severity": f.severity,
                    "suggestion": f.suggestion,
                }
                for f in findings
            ],
            "long_sentences": long_sentence_count,
        }
