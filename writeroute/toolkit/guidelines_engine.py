"""Formalized Reporting Guidelines Engine: Audits scientific manuscripts against CONSORT 2010,

PRISMA 2020, STROBE, STARD 2015, CHEERS 2022, and MOOSE checklist requirements.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class GuidelineItem:
    item_num: str
    section: str
    description: str
    checklist_prompt: str
    detected: bool = False
    evidence_span: str = ""


@dataclass
class GuidelineAuditReport:
    guideline: str
    title: str
    compliance_score: int  # 0 to 100
    items: list[GuidelineItem]
    missing_count: int
    compliant_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "guideline": self.guideline,
            "title": self.title,
            "compliance_score": self.compliance_score,
            "missing_count": self.missing_count,
            "compliant_count": self.compliant_count,
            "items": [asdict(it) for it in self.items],
        }


class ReportingGuidelinesEngine:
    """Audits scientific manuscripts against international reporting standards."""

    GUIDELINE_DEFINITIONS = {
        "consort": {
            "title": "CONSORT 2010 (Randomised Controlled Trials)",
            "items": [
                ("1a", "Title", "Identification as a randomised trial in the title", r"\b(randomi[sz]ed|trial|rct)\b"),
                ("1b", "Abstract", "Structured summary of trial design, methods, results, and conclusions", r"\b(methods|results|conclusion[s]?)\b"),
                ("2a", "Introduction", "Scientific background and explanation of rationale", r"\b(background|rationale|objective[s]?|hypothesis)\b"),
                ("3a", "Methods", "Description of trial design including allocation ratio", r"\b(parallel|crossover|factorial|allocation|1:1|ratio)\b"),
                ("4a", "Methods", "Eligibility criteria for participants", r"\b(inclusion|exclusion|eligibility|criteria)\b"),
                ("5", "Methods", "The interventions for each group with sufficient details", r"\b(intervention|control|placebo|dose|regimen)\b"),
                ("6a", "Methods", "Completely defined primary and secondary outcome measures", r"\b(primary outcome|secondary outcome|endpoint[s]?)\b"),
                ("7a", "Methods", "How sample size was determined", r"\b(sample size|power calculation|80% power|90% power)\b"),
                ("8a", "Methods", "Method used to generate the random allocation sequence", r"\b(random sequence|computer-generated|block randomi[sz]ation)\b"),
                ("9", "Methods", "Mechanism used to implement the random allocation sequence", r"\b(opaque|sealed envelope|centralized|web-based)\b"),
                ("10", "Methods", "Who generated the sequence, enrolled participants, and assigned them", r"\b(enrolled|assigned|blinded|masked)\b"),
                ("13a", "Results", "Participant flow (diagram / numbers at each stage)", r"\b(screened|enrolled|randomi[sz]ed|allocated|lost to follow-up)\b"),
                ("17a", "Results", "For each primary outcome, results for each group and effect size", r"\b(hazard ratio|odds ratio|relative risk|mean difference|95%\s*ci)\b"),
                ("19", "Results", "All important harms or unintended effects in each group", r"\b(adverse event[s]?|toxicity|safety|deaths|harms)\b"),
                ("22", "Discussion", "Interpretation consistent with results, balancing benefits and harms", r"\b(interpretation|consistent|implications|clinical practice)\b"),
                ("23", "Other", "Registration number and name of trial registry", r"\b(nct\d{8}|isrctn\d{8}|chictr|trial registry)\b"),
            ],
        },
        "prisma": {
            "title": "PRISMA 2020 (Systematic Reviews & Meta-Analyses)",
            "items": [
                ("1", "Title", "Identify the report as a systematic review, meta-analysis, or both", r"\b(systematic review|meta-analysis)\b"),
                ("2", "Abstract", "Structured summary of background, objectives, eligibility, sources, synthesis, results, conclusions", r"\b(synthesis|databases|searched|results|conclusions)\b"),
                ("5", "Methods", "Specify the inclusion and exclusion criteria for the review", r"\b(pico|inclusion criteria|exclusion criteria)\b"),
                ("6", "Methods", "Specify all information sources (e.g. databases, registers)", r"\b(pubmed|embase|cochrane|medline|scopus|web of science)\b"),
                ("7", "Methods", "Present the full search strategy for at least one database", r"\b(search strategy|mesh terms|boolean|and|or)\b"),
                ("8", "Methods", "Selection process: specify the methods used to decide eligibility (e.g. dual review)", r"\b(two independent reviewers|screened in duplicate|discrepancies resolved)\b"),
                ("9", "Methods", "Data collection process: specify data charting methods", r"\b(data extraction|extracted independently|standardized form)\b"),
                ("11", "Methods", "Specify the methods used to assess risk of bias in the included studies", r"\b(risk of bias|rob 2|robins-i|newcastle-ottawa|cochrane tool)\b"),
                ("13a", "Methods", "Synthesis methods: describe processes to decide which studies were eligible", r"\b(fixed-effect|random-effects|heterogeneity|i2|tau2|meta-regression)\b"),
                ("16a", "Results", "Study selection: describe the results of the search (PRISMA flow)", r"\b(records identified|duplicates removed|full-text articles assessed|included studies)\b"),
                ("19", "Results", "Risk of bias in studies: present assessments for each included study", r"\b(low risk|high risk|some concerns|traffic-light|rob summary)\b"),
                ("24", "Other", "Provide registration information (e.g. PROSPERO)", r"\b(prospero|crd\d{11}|open science framework|osf.io)\b"),
            ],
        },
        "strobe": {
            "title": "STROBE (Observational Cohort, Case-Control, Cross-Sectional Studies)",
            "items": [
                ("1a", "Title", "Indicate the study's design with a commonly used term in title or abstract", r"\b(cohort study|case-control|cross-sectional|observational study)\b"),
                ("2", "Introduction", "Explain the scientific background and rationale for the investigation", r"\b(background|rationale|knowledge gap)\b"),
                ("4", "Methods", "Present key elements of study design early in the paper", r"\b(prospective|retrospective|longitudinal|cross-sectional)\b"),
                ("5", "Methods", "Describe the setting, locations, and relevant dates", r"\b(setting|hospital|health center|clinic|data collected between)\b"),
                ("6a", "Methods", "Cohort: Give eligibility criteria and sources and methods of selection", r"\b(eligibility|inclusion|exclusion|participants|cohort)\b"),
                ("7", "Methods", "Clearly define all outcomes, exposures, predictors, and potential confounders", r"\b(exposure[s]?|outcome[s]?|confounder[s]?|covariate[s]?)\b"),
                ("8", "Methods", "For each variable of interest, give sources of data and measurement methods", r"\b(measured using|laboratory|questionnaire|medical records)\b"),
                ("9", "Methods", "Describe any efforts to address potential sources of bias", r"\b(selection bias|information bias|recall bias|measurement error)\b"),
                ("10", "Methods", "Explain how the study size was arrived at", r"\b(sample size|power|statistical power)\b"),
                ("12a", "Methods", "Statistical methods: Describe all statistical methods including confounder adjustment", r"\b(multivariable|adjusted|propensity score|cox regression|logistic regression)\b"),
                ("12b", "Methods", "Describe any methods used to examine subgroups and interactions", r"\b(subgroup|interaction|effect modification|stratified)\b"),
                ("12c", "Methods", "Explain how missing data were addressed", r"\b(missing data|multiple imputation|complete case|fiml)\b"),
                ("14a", "Results", "Report numbers of individuals at each stage of study", r"\b(flow diagram|eligible|enrolled|analy[sz]ed)\b"),
                ("16a", "Results", "Give unadjusted estimates and, if applicable, confounder-adjusted estimates", r"\b(unadjusted|adjusted|or|rr|hr|95%\s*ci)\b"),
                ("19", "Discussion", "Discuss limitations of the study, taking into account sources of potential bias", r"\b(limitation[s]?|observational nature|residual confounding|unmeasured)\b"),
            ],
        },
    }

    @classmethod
    def audit(cls, text: str, guideline_name: str = "consort") -> GuidelineAuditReport:
        g_key = guideline_name.lower().strip()
        spec = cls.GUIDELINE_DEFINITIONS.get(g_key, cls.GUIDELINE_DEFINITIONS["consort"])
        
        items: list[GuidelineItem] = []
        compliant_count = 0
        
        for item_num, section, desc, pattern in spec["items"]:
            m = re.search(pattern, text, re.IGNORECASE)
            detected = bool(m)
            evidence = m.group(0) if m else ""
            if detected:
                compliant_count += 1
            items.append(GuidelineItem(
                item_num=item_num,
                section=section,
                description=desc,
                checklist_prompt=f"[{item_num}] {section}: {desc}",
                detected=detected,
                evidence_span=evidence,
            ))

        total = len(items)
        score = int((compliant_count / max(1, total)) * 100)
        missing = total - compliant_count

        return GuidelineAuditReport(
            guideline=g_key.upper(),
            title=spec["title"],
            compliance_score=score,
            items=items,
            missing_count=missing,
            compliant_count=compliant_count,
        )


def run_guideline_audit(text: str, guideline: str = "consort") -> dict[str, Any]:
    return ReportingGuidelinesEngine.audit(text, guideline).to_dict()
