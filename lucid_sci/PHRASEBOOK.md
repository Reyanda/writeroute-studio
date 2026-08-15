# LUCID-SCI Phrasebook

This phrasebook provides patterns, not mandatory wording. Repetition of templates can itself become mechanical. Use them to recover logic, then vary the prose naturally.

## 1. Explaining why a study was needed

Instead of:

> Despite an increasing body of literature, a paucity of evidence remains...

Use:

> Previous studies have described **[known finding]**, but they have not established **[specific unknown]**.

> Evidence is available for **[population or setting]**, whereas little is known about **[target population or setting]**.

> Most studies treat **[factors]** separately, which can obscure **[interaction, heterogeneity or mechanism]**.

> Existing estimates may be biased because **[specific design problem]**.

## 2. Introducing a technical term

> **[Plain description]**, referred to here as **[technical term]**, ...

> We used **[technical term]**, a method that **[function]**, to **[purpose]**.

> **[Technical term]** means **[operational meaning in this study]**.

> Unlike **[related concept]**, **[technical term]** addresses **[distinction]**.

## 3. Describing methods by purpose

Instead of:

> Multiple imputation by chained equations was implemented.

Use:

> Some covariate values were missing. We used multiple imputation by chained equations to generate several plausible completed datasets and combined the estimates across them, assuming that missingness was explainable by the observed data included in the imputation model.

Instead of:

> IPTW-adjusted MSMs were fitted.

Use:

> Treatment groups differed at baseline. We therefore used inverse-probability-of-treatment weights to balance measured baseline characteristics and fitted a marginal structural model to estimate the outcome under each treatment strategy.

Instead of:

> A random-intercept model accounted for clustering.

Use:

> Outcomes from participants in the same facility may be more similar than outcomes from different facilities. We therefore included a facility-level random intercept to account for this clustering.

## 4. Reporting descriptive findings

> Among **[denominator]**, **[count] ([percentage])** experienced **[outcome]** during **[time]**.

> The median **[variable]** was **[value]** (interquartile range **[range]**).

> Rates were highest in **[group]** and lowest in **[group]**.

> The distribution was right-skewed, with a small number of **[units]** accounting for **[proportion]**.

## 5. Reporting associations

> **[Exposure]** was associated with **[higher/lower] [outcome]** (**[measure, estimate, interval]**).

> The association weakened after adjustment for **[key confounders]**.

> The estimated association differed by **[modifier]**.

> We found little evidence that the association varied across **[groups]**.

> The data were compatible with effects ranging from **[lower bound interpretation]** to **[upper bound interpretation]**.

Avoid:

> **[Exposure]** led to **[outcome]**

unless the analysis supports a causal interpretation.

## 6. Reporting causal estimates

> Under the stated identification assumptions, assigning **[intervention]** rather than **[comparator]** was estimated to **[increase/reduce] [outcome]** by **[absolute effect]** over **[time]**.

> The estimated risk under **[strategy A]** was **[risk A]**, compared with **[risk B]** under **[strategy B]**, an absolute difference of **[difference]**.

> This estimate assumes adequate control of measured confounding, consistency of the intervention definition and sufficient treatment overlap.

> Because **[exposure]** is not a well-defined intervention, we interpret the estimate as **[stated target]** rather than as the effect of directly changing **[exposure]**.

## 7. Reporting prediction

> The model distinguished participants who did and did not experience **[outcome]** with an area under the curve of **[value]**.

> Predicted risks were systematically **[higher/lower]** than observed risks in **[range or subgroup]**, indicating poor calibration.

> Adding **[predictor set]** improved discrimination by **[amount]**, but the clinical value of this improvement remains uncertain.

> Feature importance identifies variables useful for prediction; it does not establish that changing those variables would change the outcome.

## 8. Reporting interaction and heterogeneity

> The estimated effect differed across levels of **[modifier]**.

> Among **[group 1]**, **[estimate]**; among **[group 2]**, **[estimate]**.

> The interaction estimate was imprecise, so the apparent subgroup difference should be interpreted cautiously.

> Heterogeneity was quantitative rather than qualitative: the direction was similar across groups, but the magnitude differed.

Avoid treating a significant result in one subgroup and a non-significant result in another as evidence of interaction without an explicit comparison.

## 9. Discussing mechanisms

Weak:

> Inflammation explains the association.

Calibrated alternatives:

> Inflammation is one plausible explanation for the association.

> The biomarker pattern is consistent with an inflammatory pathway.

> The temporal and intervention evidence supports a contributory role for inflammation.

> The mediation analysis estimated that **[proportion or effect]** operated through **[mediator]**, under assumptions of no unmeasured exposure–mediator, mediator–outcome or exposure–outcome confounding.

## 10. Comparing with prior work

> Our estimate was larger than that reported in **[study]**, possibly because **[difference in population, design, exposure, outcome or follow-up]**.

> The direction was consistent with previous studies, but direct comparison is limited by **[difference]**.

> Unlike studies based on **[design]**, our analysis **[specific contribution]**.

> The studies address related but distinct questions: **[study A question]** versus **[present question]**.

Avoid:

> Our findings are in line with the literature.

unless the relevant dimensions of agreement are named.

## 11. Strengths and limitations

Weak:

> This study has several strengths and limitations.

Better:

> Repeated outcome measurement reduced uncertainty about timing, and the prespecified analysis limited data-driven model selection. However, loss to follow-up was related to baseline illness severity. Even after weighting, residual selection bias may have led us to underestimate mortality.

Useful patterns:

> Because **[limitation]**, the estimate may be biased **[towards/away from the null or unknown direction]**.

> Measurement error in **[variable]** is likely to have **[specific consequence]**.

> The study included **[settings]**, which improves relevance to **[target]**, but does not establish transportability to **[excluded settings]**.

> The analysis was exploratory; the interval estimates should be prioritised over binary significance labels.

## 12. Implications

> These findings suggest that **[specific actor]** should consider **[specific action]**, particularly for **[group or condition]**.

> The results support testing **[intervention]** in a prospective study rather than immediate implementation.

> The model may help prioritise assessment, but it should not replace clinical judgement until externally validated.

> National averages concealed substantial subgroup variation, indicating that resource allocation based only on average risk may miss **[group]**.

> The evidence is insufficient to recommend **[action]** because **[missing evidence]**.

## 13. Conclusions

Prefer:

> In **[population and setting]**, **[main finding]**. **[Calibrated implication]**.

> **[Intervention]** reduced **[outcome]** over **[time]**, but uncertainty remains for **[group or outcome]**.

> **[Exposure]** identified children at higher risk, although the cross-sectional design does not establish that changing the exposure would reduce that risk.

Avoid:

> These groundbreaking findings underscore the urgent need for further research.

## 14. Common academic-to-clear transformations

| Inflated or indirect | Clearer default | Note |
|---|---|---|
| owing to the fact that | because | |
| in light of the fact that | because or given that | |
| in order to | to | |
| a large proportion of | many or exact percentage | Prefer the number |
| a small number of | few or exact count | Prefer the number |
| has the ability to | can | |
| is able to | can | |
| serves to demonstrate | shows | Check claim strength |
| provide an explanation for | explain | Only if evidence supports |
| conduct an analysis of | analyse | |
| make an assessment of | assess | |
| was indicative of | indicated or was consistent with | Choose by evidence |
| in the event that | if | |
| with the exception of | except | |
| in close proximity to | near | |
| at this point in time | now | Avoid “now” in timeless methods |
| on the basis of | based on | |
| pertaining to | about or related to | |
| prior to | before | |
| subsequent to | after | |
| with respect to | for, about or in | |
| in terms of | delete or specify dimension | |
| plays a role in | contributes to, affects or is involved in | Choose exact relation |
| robust | stable across **[named checks]** | Never leave vague |
| significant | statistically significant or substantively important | Specify which |
| novel | first to **[specific contribution]**, if verified | Often delete |

## 15. Linkers with explicit logic

Use transitions only when they name a real relation.

### Addition

- also
- in addition
- a second explanation is

### Contrast

- however
- by contrast
- whereas
- despite this

### Cause or reason

- because
- therefore
- this may reflect

### Qualification

- although
- under these assumptions
- in this population
- for the primary outcome

### Example

- for example
- specifically
- including

Avoid decorating every sentence with “moreover”, “furthermore” or “notably”.

