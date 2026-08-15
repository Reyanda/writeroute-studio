# LUCID-SCI Examples

The examples illustrate precision-preserving revision. Values in brackets are placeholders and must not be invented.

## 1. Abstract background

### Dense

> Despite growing recognition of the multifactorial and intersectional aetiology of acute wasting, extant analytic paradigms have largely operationalised socioeconomic and maternal anthropometric determinants as additive homogeneous covariates, thereby obscuring non-linear and context-contingent risk surfaces.

### LUCID-SCI

> Risk of acute wasting reflects several biological and social conditions. Most studies, however, model household wealth and maternal height as separate, linear predictors. This approach can obscure non-linear, context-specific risk patterns produced by their combination.

### Why it is better

- Retains the scientific ideas: multicausality, interaction, non-linearity and context dependence.
- Replaces status language such as “extant analytic paradigms” and “operationalised”.
- Distributes three concepts across three sentences.
- Does not make the language colloquial.

## 2. Study objective

### Dense

> We sought to interrogate the distributional, intersectional and cross-context transportability properties of acute wasting risk using an integrated suite of multilevel and causal machine-learning estimators.

### LUCID-SCI

> We examined how acute wasting risk was distributed across social groups, whether combinations of maternal and household characteristics identified especially high-risk groups, and whether these risk patterns were consistent across countries. We used multilevel models and causal machine-learning methods for these distinct questions.

### Fidelity note

If the analysis did not identify causal effects, “causal machine learning” should be explained as the name of a method class, not as proof that the resulting contrasts are causal.

## 3. Target trial emulation

### Dense

> We emulated a hypothetical target trial to mitigate immortal-time bias and align eligibility, treatment assignment and time zero.

### LUCID-SCI

> We structured the observational analysis to resemble the trial we would ideally have conducted. Eligibility, treatment assignment and follow-up all began at the same time, which reduced the risk of immortal-time bias, a period during which the outcome could not have occurred by design.

### Why it is better

- Keeps the exact technical label available for later use.
- Explains the design problem before relying on the label.
- Defines the bias by its consequence.

## 4. Inverse-probability weighting

### Dense

> Stabilised IPTC weights were estimated using Super Learner and incorporated into a marginal structural Cox model to address treatment-confounder feedback and informative censoring.

### LUCID-SCI

> Treatment and continued follow-up depended on measured patient characteristics that also predicted the outcome. We estimated stabilised inverse-probability weights using Super Learner to balance these characteristics across treatment histories and to reduce bias from informative loss to follow-up. We then fitted a marginal structural Cox model to estimate the hazard under each treatment strategy.

### Boundary

The sentence should also state the relevant assumptions elsewhere: adequate measurement of confounders, model performance, positivity and correct time ordering.

## 5. Multiple imputation

### Dense

> Missingness was addressed using MICE under a MAR assumption, with Rubin's rules used for pooling.

### LUCID-SCI

> Some covariate values were missing. We used multiple imputation by chained equations to create several plausible completed datasets, assuming that missingness could be explained by the observed variables in the imputation model. We combined the estimates across datasets using Rubin's rules.

## 6. Results with effect size

### Dense

> Reduced-dose RUTF was non-inferior to the standard regimen for recovery (aRR 0.97, 95% CI 0.94–1.00; Pnon-inferiority<0.001).

### LUCID-SCI

> Recovery was similar under the reduced-dose and standard regimens (adjusted risk ratio 0.97, 95% CI 0.94 to 1.00). The lower confidence limit remained above the prespecified non-inferiority margin of **[margin]**, supporting non-inferiority for this outcome.

### Why it is better

- Names the comparison and measure.
- Explains why the non-inferiority conclusion follows.
- Requires the margin to be visible.

## 7. Imprecise estimate

### Dense

> There was no significant association between the intervention and mortality (HR 0.83, 95% CI 0.66–1.05; P=0.12).

### LUCID-SCI

> Mortality was estimated to be lower in the intervention group (hazard ratio 0.83, 95% CI 0.66 to 1.05), but the interval was compatible with a meaningful reduction, little difference or a small increase.

### Why it is better

- Avoids treating non-significance as evidence of no association.
- Makes the uncertainty decision-relevant.

## 8. Cross-sectional association

### Dense

> Household poverty increased the odds of wasting by 42%.

### LUCID-SCI

> Children in poorer households had higher odds of wasting (odds ratio 1.42, **[95% CI]**). Because the data were cross-sectional, this estimate describes an association and does not establish that a change in household wealth would change wasting risk.

### Why it is better

- Removes unsupported causal language.
- Avoids calling an odds ratio a risk increase.

## 9. Interaction

### Dense

> A statistically significant wealth-by-maternal-height interaction was observed.

### LUCID-SCI

> The wealth gradient differed by maternal height. Among children of shorter mothers, **[estimate and interval]**; among children of taller mothers, **[estimate and interval]**. The interaction estimate was **[estimate and interval]**.

### Why it is better

- States what the interaction means.
- Requires subgroup estimates and the direct interaction estimate.

## 10. Machine-learning feature importance

### Dense

> SHAP analyses identified inflammation as the dominant driver of mortality.

### LUCID-SCI

> Inflammatory markers contributed most to the model's mortality predictions according to SHAP values. This predictive importance does not show that inflammation caused mortality or that reducing these markers would reduce risk.

## 11. Mediation

### Dense

> Gut permeability mediated 36% of the effect of malnutrition on mortality.

### LUCID-SCI

> The mediation model attributed 36% of the estimated association between malnutrition and mortality to the pathway through gut permeability. A causal interpretation requires no unmeasured confounding of the exposure–mediator, mediator–outcome and exposure–outcome relations, as well as correct temporal ordering and model specification.

### Fidelity note

If the exposure was randomised and the mediator was measured after treatment, the language can be strengthened only to the extent supported by the mediation design and assumptions.

## 12. Discussion implication

### Dense

> These findings underscore the imperative for holistic, contextually tailored, intersectionality-responsive programming to address the multifaceted burden of child wasting.

### LUCID-SCI

> National averages concealed marked differences between social and maternal subgroups. Programmes that allocate resources using average risk alone may therefore miss children in smaller, high-risk groups. Prospective evaluation is needed before using these patterns to assign individual treatment.

## 13. Limitation

### Ceremonial

> This study has limitations, including its observational design and possible residual confounding.

### LUCID-SCI

> Treatment was not randomised, and illness severity was measured incompletely. Children receiving the standard regimen may therefore have been sicker in ways not captured by the data, which would bias the estimated benefit of the reduced-dose regimen upwards.

### Why it is better

- Names the causal pathway from limitation to possible bias.
- States the likely direction when defensible.

## 14. Technical report recommendation

### Dense

> It is recommended that stakeholders leverage an integrated multisectoral framework to optimise implementation synergies.

### LUCID-SCI

> The Ministry of Health should combine nutrition screening with the existing community immunisation visits in districts where both services reach fewer than **[threshold]** of eligible children. Implementation should be monitored for referral completion, staff workload and missed vaccinations.

## 15. Title revision

### Dense catalogue title

> Distributional, Intersectional, and Transportability Analyses of Acute Wasting in Four Sub-Saharan African Countries Reveal Dissociation from Stunting, Maternal-Height Buffering of Material Deprivation, and Spatially Structured Causal Risk with Limited Cross-Setting Transportability

### More accessible scientific title

> Acute wasting risk varies across maternal, socioeconomic and spatial contexts in four African countries

### Alternative retaining the principal contribution

> Maternal and socioeconomic context shape acute wasting risk across four African countries

### Caution

Use “shape” only if a causal or sufficiently explanatory claim is intended. For purely associational analyses, “is patterned by” or “varies across” may be safer.

## 16. Full abstract skeleton

> Acute wasting remains a major cause of child illness and death, but national prevalence estimates can conceal substantial differences between social groups. Most analyses model household and maternal characteristics separately and assume that their relations with wasting are similar across settings. We analysed nationally representative survey data from **[countries and years]** to examine joint risk patterns across household wealth, maternal height and place, and to assess whether these patterns were consistent across countries. **[Primary result with absolute risk or effect and uncertainty]**. **[Central heterogeneity or transportability result]**. These findings suggest that average-based targeting may miss small groups with high risk, although the cross-sectional design does not establish the effects of changing household or maternal characteristics.

## 17. Nature-style compression without loss

### Overloaded

> In the present investigation, we utilised a nationally representative, cross-sectional, population-based dataset in order to conduct an assessment of the association between maternal educational attainment and the likelihood of childhood vaccination uptake.

### LUCID-SCI

> We used nationally representative cross-sectional data to estimate the association between maternal education and childhood vaccination.

### Preserved elements

- data representativeness;
- cross-sectional design;
- exposure;
- outcome;
- associational claim.

### Removed elements

- “in the present investigation”;
- “utilised”;
- “in order to”;
- “conduct an assessment of”;
- redundant “population-based” if nationally representative already conveys the sampling frame in context.

