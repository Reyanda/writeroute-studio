from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from .critic import AcademicCritic
from .guidelines import ReportingGuidelineRegistry
from .models import Issue, ManuscriptProfile, RevisionProposal, Severity, TextAnchor
from .ooxml import EM_DASH_RE, OOXMLPackage
from .rewrite import SafeCopyeditor


def _sanitize_output(value: Any) -> Any:
    if isinstance(value, str):
        return EM_DASH_RE.sub(" [em dash] ", value)
    if isinstance(value, list):
        return [_sanitize_output(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_output(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_output(item) for key, item in value.items()}
    return value


class ManuscriptDocxEngine:
    """Preservation-first academic manuscript editor for DOCX files."""

    def __init__(
        self,
        profile: ManuscriptProfile | None = None,
        *,
        use_negative_engine: bool = True,
    ):
        self.profile = profile or ManuscriptProfile()
        self.critic = AcademicCritic(use_negative_engine=use_negative_engine)
        self.core_critic = AcademicCritic(use_negative_engine=False)
        self.copyeditor = SafeCopyeditor()
        self.guidelines = ReportingGuidelineRegistry()

    def inspect(self, source: str | Path) -> dict[str, Any]:
        package = OOXMLPackage(source)
        return _sanitize_output(
            {
                "inventory": package.inventory(),
                "review_state": package.review_state(),
                "paragraphs": [
                    {
                        "index": record["index"],
                        "section": record["section"],
                        "style": record["style"],
                        "in_table": record["in_table"],
                        "text": record["text"],
                    }
                    for record in package.paragraph_records()
                ],
            }
        )

    def audit(
        self,
        source: str | Path,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        package = OOXMLPackage(source)
        metadata = dict(metadata or {})
        reporting_profiles = self.guidelines.recommend(metadata)
        issues = self._audit_package(package, metadata=metadata)
        return _sanitize_output(
            {
                "engine": "Auctor Academic Writing Engine",
                "version": "1.0.0",
                "inventory": package.inventory(),
                "review_state": package.review_state(),
                "reporting_guidelines": reporting_profiles,
                "issues": [issue.to_dict() for issue in issues],
                "release": self._release_decision(issues),
            }
        )

    def prepare(
        self,
        source: str | Path,
        output: str | Path,
        *,
        apply_safe_edits: bool = True,
        track_changes: bool | None = None,
        add_comments: bool | None = None,
        author: str | None = None,
        initials: str | None = None,
        citation_tags: Sequence[Mapping[str, str]] = (),
        reference_tags: Sequence[Mapping[str, str]] = (),
        bookmarks: Sequence[Mapping[str, str]] = (),
        ref_fields: Sequence[Mapping[str, str]] = (),
        max_editorial_comments: int = 80,
        report_path: str | Path | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        source = Path(source)
        output = Path(output)
        track_changes = self.profile.use_track_changes if track_changes is None else track_changes
        add_comments = self.profile.add_editorial_comments if add_comments is None else add_comments
        author = author or self.profile.editor_name
        initials = initials or self.profile.editor_initials
        metadata = dict(metadata or {})
        reporting_profiles = self.guidelines.recommend(metadata)

        package = OOXMLPackage(source)
        before_inventory = package.inventory()
        before_review = package.review_state()
        root = package.document_root()
        records = package.paragraph_records(root)
        applied: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        comment_count = 0

        if apply_safe_edits:
            for record in records:
                text = record["text"]
                if not text:
                    continue
                proposals = self.copyeditor.plan(
                    text,
                    paragraph_index=record["index"],
                    section=record["section"],
                )
                if record["section"] == "references":
                    proposals = [proposal for proposal in proposals if proposal.code == "AWE-STYLE-001"]
                proposals.sort(key=lambda proposal: int(proposal.metadata.get("start", 0)), reverse=True)
                for proposal in proposals:
                    comment_allowed = add_comments and comment_count < max_editorial_comments
                    # Mechanical dash removal is applied directly so the package contains no
                    # em dash character even inside a deleted revision run. The change remains
                    # recorded in the revision ledger and, when enabled, in a Word comment.
                    track_this = bool(track_changes and proposal.code != "AWE-STYLE-001")
                    result = package.replace_text_with_revision(
                        record["paragraph"],
                        proposal.target,
                        proposal.replacement,
                        author=author,
                        initials=initials,
                        reason=proposal.commentary or proposal.reason,
                        start_hint=int(proposal.metadata.get("start", 0)),
                        track_changes=track_this,
                        add_comment=comment_allowed,
                    )
                    item = {"proposal": proposal.to_dict(), "result": result}
                    if result.get("applied"):
                        applied.append(item)
                        if result.get("comment_id") is not None:
                            comment_count += 1
                    else:
                        skipped.append(item)
            package.save_document_root(root)

        tag_results = self._apply_semantic_tags(
            package,
            citation_tags=citation_tags,
            reference_tags=reference_tags,
            bookmarks=bookmarks,
            ref_fields=ref_fields,
        )
        package.request_field_update()
        package.normalize_manuscript_format(self.profile)

        issues = self._audit_package(package, metadata=metadata)
        for item in skipped:
            proposal = item["proposal"]
            issues.append(
                Issue(
                    code="AWE-REV-002",
                    title="Safe OOXML anchor was not available",
                    severity=Severity.LOW.value,
                    message=(
                        f"The proposal for '{proposal['target']}' was not applied because the text was missing or crossed a "
                        "protected Word structure such as a field, hyperlink, content control, or existing revision."
                    ),
                    evidence=proposal["target"],
                    action="Review the location manually. Do not flatten the protected structure merely to force an edit.",
                    anchor=TextAnchor(
                        paragraph_index=proposal.get("paragraph_index"),
                        section=proposal.get("section", "other"),
                        quote=proposal.get("target", ""),
                    ),
                    auto_fixable=False,
                )
            )

        commentary_comments: list[dict[str, Any]] = []
        if add_comments and comment_count < max_editorial_comments:
            commentary_root = package.document_root()
            commentary_records = {record["index"]: record for record in package.paragraph_records(commentary_root)}
            seen_comment_targets: set[tuple[int, str]] = set()
            for issue in issues:
                if issue.severity not in {Severity.HIGH.value, Severity.CRITICAL.value}:
                    continue
                paragraph_index = issue.anchor.paragraph_index
                target = issue.anchor.quote or issue.evidence
                if paragraph_index is None or not target or paragraph_index not in commentary_records:
                    continue
                key = (paragraph_index, target)
                if key in seen_comment_targets:
                    continue
                seen_comment_targets.add(key)
                commentary = issue.action.strip()
                if issue.message and issue.message.strip() not in commentary:
                    commentary = f"{commentary} The current wording was flagged because {issue.message.strip().lower()}"
                result = package.replace_text_with_revision(
                    commentary_records[paragraph_index]["paragraph"],
                    target,
                    target,
                    author=author,
                    initials=initials,
                    reason=commentary,
                    start_hint=issue.anchor.start,
                    track_changes=False,
                    add_comment=True,
                )
                if result.get("applied"):
                    comment_count += 1
                    commentary_comments.append({"issue_code": issue.code, "paragraph": paragraph_index, "result": result})
                if comment_count >= max_editorial_comments:
                    break
            package.save_document_root(commentary_root)
            package.normalize_manuscript_format(self.profile)

        package.write_qc_custom_xml(
            issues,
            profile=self.profile,
            metadata={
                "source": source.name,
                "output": output.name,
                "applied_revisions": len(applied),
                "skipped_revisions": len(skipped),
                "semantic_tags": tag_results,
                "commentary_comments": len(commentary_comments),
                "reporting_guidelines": reporting_profiles,
                "manuscript_metadata": metadata,
            },
        )
        validation = package.validate(self.profile)
        package.write(output)

        # Reopen the written package so the report reflects the deliverable,
        # not only the in-memory representation.
        reopened = OOXMLPackage(output)
        final_validation = reopened.validate(self.profile)
        final_issues = issues
        report = _sanitize_output(
            {
                "engine": "Auctor Academic Writing Engine",
                "version": "1.0.0",
                "source": str(source),
                "output": str(output),
                "profile": self.profile.to_dict(),
                "reporting_guidelines": reporting_profiles,
                "manuscript_metadata": metadata,
                "channel_contract": {
                    "substantive": "visible manuscript text and tracked insertions only",
                    "qc": "companion report and customXml/auctor_qc.xml",
                    "commentary": "Word comments only",
                },
                "before": {
                    "inventory": before_inventory,
                    "review_state": before_review,
                },
                "after": {
                    "inventory": reopened.inventory(),
                    "review_state": reopened.review_state(),
                },
                "applied": applied,
                "skipped": skipped,
                "semantic_tags": tag_results,
                "commentary_comments": commentary_comments,
                "issues": [issue.to_dict() for issue in final_issues],
                "validation_before_write": validation,
                "validation": final_validation,
                "release": self._release_decision(final_issues, final_validation),
            }
        )
        if report_path is not None:
            report_path = Path(report_path)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return report

    def finalize(
        self,
        source: str | Path,
        output: str | Path,
        *,
        revisions: str = "accept",
        comments: str = "remove",
        report_path: str | Path | None = None,
    ) -> dict[str, Any]:
        if revisions not in {"preserve", "accept", "reject"}:
            raise ValueError("revisions must be preserve, accept, or reject")
        if comments not in {"preserve", "remove"}:
            raise ValueError("comments must be preserve or remove")
        package = OOXMLPackage(source)
        before = {"inventory": package.inventory(), "review_state": package.review_state()}
        revision_result = {"insertions": 0, "deletions": 0}
        if revisions in {"accept", "reject"}:
            revision_result = package.resolve_tracked_changes(revisions)
        removed_comments = package.remove_comments() if comments == "remove" else 0
        if self.profile.zero_em_dash:
            root = package.document_root()
            for record in package.paragraph_records(root):
                proposals = [
                    proposal
                    for proposal in self.copyeditor.plan(
                        record["text"],
                        paragraph_index=record["index"],
                        section=record["section"],
                    )
                    if proposal.code == "AWE-STYLE-001"
                ]
                proposals.sort(key=lambda proposal: int(proposal.metadata.get("start", 0)), reverse=True)
                for proposal in proposals:
                    package.replace_text_with_revision(
                        record["paragraph"],
                        proposal.target,
                        proposal.replacement,
                        author=self.profile.editor_name,
                        initials=self.profile.editor_initials,
                        reason="",
                        start_hint=int(proposal.metadata.get("start", 0)),
                        track_changes=False,
                        add_comment=False,
                    )
            package.save_document_root(root)
        package.request_field_update()
        package.normalize_manuscript_format(self.profile)
        validation = package.validate(self.profile)
        package.write(output)
        reopened = OOXMLPackage(output)
        final_validation = reopened.validate(self.profile)
        report = _sanitize_output(
            {
                "engine": "Auctor Academic Writing Engine",
                "operation": "finalize",
                "source": str(source),
                "output": str(output),
                "revisions": revisions,
                "comments": comments,
                "resolved_revisions": revision_result,
                "removed_comments": removed_comments,
                "before": before,
                "after": {"inventory": reopened.inventory(), "review_state": reopened.review_state()},
                "validation_before_write": validation,
                "validation": final_validation,
            }
        )
        if report_path is not None:
            Path(report_path).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return report

    def _apply_semantic_tags(
        self,
        package: OOXMLPackage,
        *,
        citation_tags: Sequence[Mapping[str, str]],
        reference_tags: Sequence[Mapping[str, str]],
        bookmarks: Sequence[Mapping[str, str]],
        ref_fields: Sequence[Mapping[str, str]],
    ) -> dict[str, Any]:
        root = package.document_root()
        results: dict[str, list[dict[str, Any]]] = {
            "citations": [],
            "references": [],
            "bookmarks": [],
            "ref_fields": [],
        }

        def find_paragraph(target: str):
            for record in package.paragraph_records(root):
                if target in record["text"]:
                    return record["paragraph"], record["index"]
            return None, None

        for spec in citation_tags:
            target = spec.get("target", "")
            key = spec.get("key", "")
            paragraph, index = find_paragraph(target)
            applied = bool(
                paragraph is not None
                and target
                and key
                and package.tag_text(
                    paragraph,
                    target,
                    tag=f"AUCTOR:CITATION:{key}",
                    alias=f"Citation {key}",
                )
            )
            results["citations"].append({"target": target, "key": key, "paragraph": index, "applied": applied})

        for spec in reference_tags:
            target = spec.get("target", "")
            key = spec.get("key", "")
            paragraph, index = find_paragraph(target)
            applied = bool(
                paragraph is not None
                and target
                and key
                and package.tag_text(
                    paragraph,
                    target,
                    tag=f"AUCTOR:REFERENCE:{key}",
                    alias=f"Reference {key}",
                )
            )
            results["references"].append({"target": target, "key": key, "paragraph": index, "applied": applied})

        for spec in bookmarks:
            target = spec.get("target", "")
            name = spec.get("name", "")
            paragraph, index = find_paragraph(target)
            applied = bool(paragraph is not None and target and name and package.add_bookmark(paragraph, target, name=name))
            results["bookmarks"].append({"target": target, "name": name, "paragraph": index, "applied": applied})

        for spec in ref_fields:
            marker = spec.get("marker", "")
            bookmark = spec.get("bookmark", "")
            display_text = spec.get("display_text", bookmark)
            paragraph, index = find_paragraph(marker)
            applied = bool(
                paragraph is not None
                and marker
                and bookmark
                and package.replace_marker_with_ref_field(
                    paragraph,
                    marker,
                    bookmark=bookmark,
                    display_text=display_text,
                )
            )
            results["ref_fields"].append(
                {
                    "marker": marker,
                    "bookmark": bookmark,
                    "display_text": display_text,
                    "paragraph": index,
                    "applied": applied,
                }
            )

        package.save_document_root(root)
        return results

    def _audit_package(
        self,
        package: OOXMLPackage,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> list[Issue]:
        issues: list[Issue] = []
        section_blocks: dict[str, list[str]] = defaultdict(list)
        for record in package.paragraph_records():
            text = record["text"]
            if not text:
                continue
            if not record["in_table"]:
                section_blocks[record["section"]].append(text)
            critic = self.core_critic if record["section"] == "references" or record["in_table"] else self.critic
            paragraph_issues = critic.audit(text, section=record["section"])
            for issue in paragraph_issues:
                anchor = replace(
                    issue.anchor,
                    paragraph_index=record["index"],
                    section=record["section"],
                )
                issues.append(replace(issue, anchor=anchor))

        reporting_profiles = self.guidelines.recommend(metadata or {})
        if reporting_profiles:
            section_text = {section: "\n\n".join(blocks) for section, blocks in section_blocks.items()}
            issues.extend(self.guidelines.audit_document(section_text, profile_ids=reporting_profiles))

        reference_state = package.semantic_reference_state()
        for key in reference_state["orphan_citation_keys"]:
            issues.append(
                Issue(
                    code="AWE-REF-001",
                    title="Semantic citation has no matching reference",
                    severity=Severity.CRITICAL.value,
                    message=f"Citation key '{key}' does not resolve to an AUCTOR reference tag.",
                    evidence=key,
                    action="Add or repair the bibliography tag without replacing a live reference-manager field.",
                    metadata={"citation_key": key},
                )
            )
        for target in reference_state["missing_ref_targets"]:
            issues.append(
                Issue(
                    code="AWE-XREF-001",
                    title="Cross-reference field has no bookmark target",
                    severity=Severity.CRITICAL.value,
                    message=f"A REF field targets missing bookmark '{target}'.",
                    evidence=target,
                    action="Restore the bookmark or retarget the field before release.",
                    metadata={"bookmark": target},
                )
            )

        review_state = package.review_state()
        if review_state["comments"]:
            issues.append(
                Issue(
                    code="AWE-REVIEW-001",
                    title="Document contains review comments",
                    severity=Severity.INFO.value,
                    message=f"The document contains {len(review_state['comments'])} comment(s), all preserved by the engine.",
                    action="Resolve or retain comments according to the author and journal review workflow.",
                    metadata={"comments": len(review_state["comments"])},
                )
            )
        return self._deduplicate_issues(issues)

    @staticmethod
    def _deduplicate_issues(issues: Sequence[Issue]) -> list[Issue]:
        seen: set[tuple[Any, ...]] = set()
        result: list[Issue] = []
        for issue in issues:
            key = (
                issue.code,
                issue.anchor.paragraph_index,
                issue.anchor.start,
                issue.anchor.end,
                issue.evidence,
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(issue)
        return result

    @staticmethod
    def _release_decision(
        issues: Sequence[Issue],
        validation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        critical = [issue for issue in issues if issue.severity == Severity.CRITICAL.value]
        high = [issue for issue in issues if issue.severity == Severity.HIGH.value]
        validation_ok = True if validation is None else bool(validation.get("valid"))
        if critical or not validation_ok:
            status = "blocked"
        elif high:
            status = "author_review_required"
        else:
            status = "technically_ready"
        return {
            "status": status,
            "critical_issues": len(critical),
            "high_issues": len(high),
            "validation_passed": validation_ok,
            "authorship_inference": "not_performed",
        }
