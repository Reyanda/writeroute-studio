from __future__ import annotations

import re
from importlib.resources import files
from typing import Any, Iterable, Mapping, Sequence

import yaml

from .models import Issue, Severity, TextAnchor


class ReportingGuidelineRegistry:
    """Versioned reporting-guideline routing and conservative coverage proxies.

    The registry does not reproduce or replace an official checklist. Its
    deterministic checks identify passages that require confirmation against
    the named guideline. A missing keyword is never treated as proof that an
    item is absent.
    """

    def __init__(self, data: Mapping[str, Any] | None = None):
        if data is None:
            registry_file = files("auctor_engine.data").joinpath("reporting_guidelines.yaml")
            with registry_file.open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
        self.data = dict(data)
        profiles = self.data.get("profiles", {})
        if not isinstance(profiles, Mapping):
            raise ValueError("reporting_guidelines.yaml must contain a profiles mapping")
        self.profiles: dict[str, Mapping[str, Any]] = {
            str(key): value for key, value in profiles.items() if isinstance(value, Mapping)
        }

    def available(self) -> list[dict[str, Any]]:
        return [
            {
                "id": profile_id,
                "title": profile.get("title", profile_id),
                "version": profile.get("version", ""),
                "applies_to": list(profile.get("applies_to", [])),
                "official_url": profile.get("official_url", ""),
            }
            for profile_id, profile in self.profiles.items()
        ]

    def recommend(self, metadata: Mapping[str, Any] | None = None) -> list[str]:
        metadata = metadata or {}
        explicit = metadata.get("reporting_guidelines")
        if isinstance(explicit, str):
            explicit = [item.strip() for item in explicit.split(",") if item.strip()]
        if isinstance(explicit, Sequence) and not isinstance(explicit, (str, bytes)):
            return self._validate_ids(str(item) for item in explicit)

        study_design = self._normalize(
            " ".join(
                str(metadata.get(key, ""))
                for key in ("study_design", "design", "manuscript_type", "analysis_type")
            )
        )
        if not study_design:
            return []

        protocol = "protocol" in study_design
        recommendations: list[str] = []
        for profile_id, profile in self.profiles.items():
            profile_kind = str(profile.get("manuscript_kind", "report"))
            if protocol and profile_kind == "completed_study":
                continue
            if not protocol and profile_kind == "protocol":
                continue
            aliases = [self._normalize(str(alias)) for alias in profile.get("aliases", [])]
            if any(alias and alias in study_design for alias in aliases):
                recommendations.append(profile_id)
        return recommendations

    def audit_section(
        self,
        text: str,
        *,
        section: str,
        profile_ids: Sequence[str],
        paragraph_index: int | None = None,
    ) -> list[Issue]:
        if not text.strip():
            return []
        issues: list[Issue] = []
        for profile_id in self._validate_ids(profile_ids):
            profile = self.profiles[profile_id]
            for check in profile.get("coverage_proxies", []):
                if not isinstance(check, Mapping):
                    continue
                check_section = str(check.get("section", "whole"))
                if check_section != section:
                    continue
                groups = check.get("concept_groups", [])
                if not groups:
                    continue
                missing_groups: list[list[str]] = []
                for group in groups:
                    patterns = [str(pattern) for pattern in group] if isinstance(group, Sequence) and not isinstance(group, str) else [str(group)]
                    if not any(re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE) for pattern in patterns):
                        missing_groups.append(patterns)
                if not missing_groups:
                    continue
                severity = str(check.get("severity", Severity.INFO.value))
                requirement = str(check.get("requirement", "Confirm coverage of this reporting domain."))
                action = str(check.get("action", "Check the official guideline and record the manuscript location."))
                issues.append(
                    Issue(
                        code=f"AWE-RG-{profile_id.upper()}-{check.get('id', 'CHECK')}",
                        title="Reporting-guideline coverage requires confirmation",
                        severity=severity,
                        message=(
                            f"A conservative text proxy did not find all concepts for {profile.get('title', profile_id)} "
                            f"{profile.get('version', '')}: {requirement}"
                        ).strip(),
                        evidence=f"Section audited: {section}",
                        action=action,
                        anchor=TextAnchor(paragraph_index=paragraph_index, section=section, quote=""),
                        confidence=float(check.get("confidence", 0.55)),
                        source="reporting_guideline_registry",
                        auto_fixable=False,
                        metadata={
                            "heuristic": True,
                            "profile_id": profile_id,
                            "profile_title": profile.get("title", profile_id),
                            "version": profile.get("version", ""),
                            "official_url": profile.get("official_url", ""),
                            "check_id": check.get("id", ""),
                            "missing_concept_groups": missing_groups,
                            "not_a_compliance_determination": True,
                        },
                    )
                )
        return issues

    def audit_document(
        self,
        sections: Mapping[str, str],
        *,
        profile_ids: Sequence[str],
    ) -> list[Issue]:
        issues: list[Issue] = []
        for section, text in sections.items():
            issues.extend(self.audit_section(text, section=section, profile_ids=profile_ids))
        whole = "\n\n".join(text for text in sections.values() if text.strip())
        issues.extend(self.audit_section(whole, section="whole", profile_ids=profile_ids))
        return self._deduplicate(issues)

    def profile(self, profile_id: str) -> Mapping[str, Any]:
        if profile_id not in self.profiles:
            raise KeyError(f"Unknown reporting-guideline profile: {profile_id}")
        return self.profiles[profile_id]

    def _validate_ids(self, profile_ids: Iterable[str]) -> list[str]:
        output: list[str] = []
        unknown: list[str] = []
        for value in profile_ids:
            profile_id = value.strip()
            if not profile_id:
                continue
            if profile_id not in self.profiles:
                unknown.append(profile_id)
            elif profile_id not in output:
                output.append(profile_id)
        if unknown:
            raise ValueError(f"Unknown reporting-guideline profile(s): {', '.join(unknown)}")
        return output

    @staticmethod
    def _normalize(value: str) -> str:
        value = value.casefold().replace("+", " plus ")
        return re.sub(r"[^a-z0-9]+", " ", value).strip()

    @staticmethod
    def _deduplicate(issues: Sequence[Issue]) -> list[Issue]:
        seen: set[tuple[str, str, str]] = set()
        output: list[Issue] = []
        for issue in issues:
            key = (issue.code, issue.anchor.section, issue.message)
            if key in seen:
                continue
            seen.add(key)
            output.append(issue)
        return output
