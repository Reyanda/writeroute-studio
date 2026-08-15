import asyncio, uvicorn, threading, time, sys
from playwright.sync_api import sync_playwright
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app as fast_app

ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)

config = uvicorn.Config(fast_app.app, host="127.0.0.1", port=8812, log_level="error")
server = uvicorn.Server(config)
thread = threading.Thread(target=server.run, daemon=True)
thread.start()
time.sleep(1.5)

sample_rich = """<h1>Neonatal Survival in Low-Resource Clinical Settings</h1>

<h2>1. Abstract</h2>
<p>Improved water sources included piped water, boreholes and protected springs. In today's rapidly evolving scientific landscape, our prospective observational cohort study evaluates 1,200 infants across 14 health centers in Sub-Saharan Africa.</p>

<div class="callout-box note">
  <div class="callout-title"><span>ℹ</span> <strong>Methodological Preregistration</strong></div>
  <p>Study protocol was prospectively registered in ClinicalTrials.gov (NCT04918231) following STROBE and CONSORT reporting guidelines.</p>
</div>

<h2>2. Quantitative Findings & Odds Ratio</h2>
<p>The adjusted odds ratio was 0.58 (95% CI: 0.44 to 0.76; p &lt; 0.001) as detailed in Table 1 below.</p>

<table class="scientific-table">
  <caption>Table 1: Baseline Characteristics and 30-Day Clinical Outcomes</caption>
  <tbody>
    <tr>
      <th>Cohort Group</th>
      <th>Sample (n)</th>
      <th>30-Day Mortality</th>
      <th>Adjusted Odds Ratio (95% CI)</th>
    </tr>
    <tr>
      <td>Intervention Arm</td>
      <td>600</td>
      <td>4.2%</td>
      <td>0.58 (0.44–0.76)</td>
    </tr>
    <tr>
      <td>Standard Care</td>
      <td>600</td>
      <td>7.8%</td>
      <td>1.00 (Reference)</td>
    </tr>
  </tbody>
</table>

<div class="math-equation" data-tex="OR = \frac{p_1 / (1 - p_1)}{p_0 / (1 - p_0)} = \frac{a \cdot d}{b \cdot c}">
  $$ OR = \frac{p_1 / (1 - p_1)}{p_0 / (1 - p_0)} = \frac{a \cdot d}{b \cdot c} $$
</div>

<h2>3. Discussion & References</h2>
<p>However, unmeasured confounding and residual selection bias cannot be entirely ruled out <span class="citation-tag">(Smith et al., 2024)</span>.</p>

<ol class="references-list">
  <li>Smith J, Doe J. Neonatal health interventions in Sub-Saharan Africa. <em>The Lancet</em>. 2024;403:112-124.</li>
</ol>
"""

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page.goto("http://127.0.0.1:8812/studio.html", wait_until="networkidle")
    
    # Open workspace and inject rich scientific text
    page.evaluate("""
        (html) => {
            const startBtn = document.getElementById('startBlank');
            startBtn.click();
            const ed = document.getElementById('editor');
            ed.innerHTML = html;
            ed.dispatchEvent(new Event('input'));
            document.getElementById('docTitle').value = 'Neonatal Survival — Lancet Manuscript';
            document.documentElement.dataset.theme = 'dark';
        }
    """, sample_rich)
    
    time.sleep(0.5)
    
    # Run Super Audit
    page.evaluate("""
        async () => {
            const t = document.getElementById('editor').innerText;
            const res = await fetch('/api/super-audit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text: t,
                    section: 'general',
                    study_design: 'observational_cohort',
                    target_guideline: 'CONSORT',
                }),
            });
            const data = await res.json();
            document.getElementById('superScoreLabel').textContent = 'Integrity Score: ' + data.summary.overall_score + '/100';
            document.getElementById('superScoreSummary').textContent = data.summary.total_findings_count + ' total issues flagged across statistical, style, prose, and clarity engines.';
            document.getElementById('superFatalCount').textContent = data.summary.fatal_findings_count;
            document.getElementById('superCritCount').textContent = data.summary.critical_findings_count;
            document.getElementById('superTotalCount').textContent = data.summary.total_findings_count;
            document.getElementById('superScoreStats').textContent = data.summary.statistical_score + '%';
            document.getElementById('superScoreStyle').textContent = data.summary.style_burden_score + '%';
            document.getElementById('superScoreProse').textContent = data.summary.prose_quality_score + '%';
            document.getElementById('superScoreLucid').textContent = data.summary.lucid_clarity_score + '%';
            document.getElementById('superScoreGuide').textContent = data.summary.guidelines_score + '%';
            document.getElementById('superScoreOverall').textContent = data.summary.overall_score + '%';
            
            const list = document.getElementById('superFindingList');
            list.classList.remove('empty-state');
            list.innerHTML = '';
            const allFindings = [
                ...(data.statistical_findings || []).map(f => ({ ...f, engine: 'STATS-BRAIN', msg: f.summary || f.title || f.message, sev: f.severity })),
                ...(data.pattern_findings || []).map(f => ({ ...f, engine: 'STYLE-PATTERN', msg: f.message, sev: f.severity })),
                ...(data.lucid_findings || []).map(f => ({ ...f, engine: 'LUCID-SCI', msg: f.message, sev: f.severity })),
                ...(data.prose_findings || []).map(f => ({ ...f, engine: 'PROSE', msg: f.rationale || f.message, sev: f.severity })),
            ];
            for (const item of allFindings.slice(0, 6)) {
                const el = document.createElement('div');
                el.className = 'finding';
                const sevClass = (item.sev === 'fatal' || item.sev === 'critical') ? 'dot hard' : 'dot review';
                el.innerHTML = '<div class="finding-top"><h4>' + item.engine + ': ' + (item.rule_id || item.id || 'Finding') + '</h4><span class="badge ' + sevClass + '">' + (item.sev || 'info') + '</span></div><p>' + (item.msg || '') + '</p>';
                list.appendChild(el);
            }
        }
    """)
    
    time.sleep(0.5)
    page.screenshot(path=str(ASSETS / "super_engine_ui_dark.png"))
    print("Saved assets/super_engine_ui_dark.png")

    # Light Mode
    page.evaluate("() => { document.documentElement.dataset.theme = 'light'; }")
    time.sleep(0.3)
    page.screenshot(path=str(ASSETS / "super_engine_ui.png"))
    print("Saved assets/super_engine_ui.png")

    # Dark Mode Outline Tab
    page.evaluate("() => { document.documentElement.dataset.theme = 'dark'; }")
    page.click('.rail-button[data-panel="outline"]')
    page.click('#refreshOutlineBtn')
    time.sleep(0.3)
    page.screenshot(path=str(ASSETS / "authoring_outline_ui.png"))
    print("Saved assets/authoring_outline_ui.png")

    # Analytics Panel
    page.click('.rail-button[data-panel="analytics"]')
    time.sleep(0.3)
    page.screenshot(path=str(ASSETS / "authoring_analytics_ui.png"))
    print("Saved assets/authoring_analytics_ui.png")

    # Overleaf LaTeX Split View
    page.click('#latexSplitBtn')
    page.click('.rail-button[data-panel="latex"]')
    time.sleep(0.5)
    page.screenshot(path=str(ASSETS / "overleaf_latex_split_ui.png"))
    print("Saved assets/overleaf_latex_split_ui.png")

    # Adobe PDF Studio Panel
    page.click('.rail-button[data-panel="pdfstudio"]')
    time.sleep(0.3)
    page.screenshot(path=str(ASSETS / "adobe_pdf_studio_ui.png"))
    print("Saved assets/adobe_pdf_studio_ui.png")

    # Word Comments & Review Panel
    page.evaluate("""
        () => {
            const comments = [
                { id: 'c1', author: 'Senior Reviewer', time: '10:42 AM', quote: 'The adjusted odds ratio was 0.58', text: 'Consider reporting unadjusted odds ratio alongside the multivariable model estimates.', resolved: false },
                { id: 'c2', author: 'Statistical Editor', time: '11:15 AM', quote: 'Table 1: Baseline Characteristics', text: 'Please add exact p-values for baseline balance across arms.', resolved: false }
            ];
            localStorage.setItem('writeroute-comments', JSON.stringify(comments));
            location.reload();
        }
    """)
    time.sleep(1.0)
    page.evaluate("""
        (html) => {
            const startBtn = document.getElementById('startBlank');
            startBtn.click();
            const ed = document.getElementById('editor');
            ed.innerHTML = html;
            document.documentElement.dataset.theme = 'dark';
        }
    """, sample_rich)
    page.click('.rail-button[data-panel="comments"]')
    time.sleep(0.4)
    page.screenshot(path=str(ASSETS / "word_comments_ui.png"))
    print("Saved assets/word_comments_ui.png")

    # Auctor Writing Doctrine Panel
    page.click('.rail-button[data-panel="doctrine"]')
    page.click('#runDoctrineAuditBtn')
    time.sleep(0.6)
    page.screenshot(path=str(ASSETS / "auctor_doctrine_ui.png"))
    print("Saved assets/auctor_doctrine_ui.png")

    # Native Citation Manager Panel
    page.click('.rail-button[data-panel="citations"]')
    time.sleep(0.6)
    page.screenshot(path=str(ASSETS / "citation_manager_ui.png"))
    print("Saved assets/citation_manager_ui.png")

    browser.close()



