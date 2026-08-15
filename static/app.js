import WriteRoute from './engine.js';
import { extract, exportDocument, download } from './files.js';

const $ = (q, root=document) => root.querySelector(q);
const $$ = (q, root=document) => [...root.querySelectorAll(q)];
const state = { sourceText:'', lastAudit:null, lastFormatting:null, candidate:null, filename:'Untitled document', dirty:false };
const editor=$('#editor'), workspace=$('#workspace'), hero=$('#hero'), toast=$('#toast');

function showToast(message){ toast.textContent=message; toast.classList.add('show'); clearTimeout(showToast.t); showToast.t=setTimeout(()=>toast.classList.remove('show'),2600); }
function escapeHtml(str){ return String(str ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
function text(){ return editor.innerText.replace(/\u00a0/g,' ').trim(); }

function updateCounts(){
  const n=(text().match(/[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]|[\p{L}][\p{L}'\u2019-]*/gu)||[]).length;
  $('#wordCount').textContent=`${n.toLocaleString()} words`;
  state.dirty=true;
  $('#saveState').textContent='Edited locally';
  if(typeof updateAnalytics === 'function') updateAnalytics();
  if(typeof updateOutline === 'function') updateOutline();
}
function openWorkspace(){ hero.classList.add('hidden'); workspace.classList.remove('hidden'); setTimeout(()=>editor.focus(),50); WriteRoute.ensure().catch(e=>showToast(`Engine failed to start: ${e.message}`)); }
function setDocument(content, name='Untitled document', htmlContent=null){
  openWorkspace();
  state.sourceText = content;
  state.filename = name.replace(/\.[^.]+$/, '') || 'Untitled document';
  $('#docTitle').value = state.filename;
  if (htmlContent) {
    editor.innerHTML = htmlContent;
  } else {
    editor.textContent = content;
  }
  state.dirty = false;
  $('#saveState').textContent = 'Loaded';
  updateCounts();
  state.dirty = false;
  $('#saveState').textContent = 'Ready';
}

const savedTheme=localStorage.getItem('writeroute-theme');
const prefersDark=matchMedia('(prefers-color-scheme:dark)').matches;
document.documentElement.dataset.theme=savedTheme || (prefersDark?'dark':'light');
$('#themeToggle').onclick=()=>{ const next=document.documentElement.dataset.theme==='dark'?'light':'dark'; document.documentElement.dataset.theme=next; localStorage.setItem('writeroute-theme',next); };

$('#startBlank').onclick=()=>setDocument('', 'Untitled document');
$('#newDoc').onclick=()=>{ if(state.dirty && text() && !confirm('Start a new document? Unsaved editor changes will be cleared.')) return; setDocument('','Untitled document'); resetReports(); };

const postJSON = (route, payload, extraHeaders={}) =>
 WriteRoute.call(route.replace(/^\/api\//,''), payload, extraHeaders);

// Engine boot is visible rather than a spinner with no explanation: the first audit in
// the static build downloads a Python runtime, and a reader deserves to know that is
// what the wait is.
WriteRoute.onStatus(({phase, detail, mode}) => {
 const el=$('#engineStatus'); if(!el) return;
 el.dataset.phase=phase; el.textContent=detail;
 el.classList.toggle('ready', phase==='ready');
 if(phase==='ready') el.title = mode==='server' ? 'Local WriteRoute service' : 'Engine running entirely in this browser';
});

async function openFile(file){
 if(!file)return;
 showToast('Reading document…');
 try{
  const {text:content, html:htmlContent, sourceFormat}=await extract(file);
  setDocument(content, file.name, htmlContent);
  if(!$('#genreSelect').value){
   showToast(`${sourceFormat.toUpperCase()} loaded. Choose the document type to audit it.`);
   $('#genreSelect').focus();
   return;
  }
  const d=await postJSON('audit',{text:content,genre:$('#genreSelect').value});
  renderAudit(d.audit,d.formatting);
  showToast(`${sourceFormat.toUpperCase()} loaded and audited`);
 }catch(err){showToast(err.message)}
}

['#fileInput','#fileInputWorkspace'].forEach(sel=>{
 const el=$(sel); if(!el)return;
 el.onchange=async e=>{ await openFile(e.target.files[0]); e.target.value='' };
});

// Drag a file anywhere onto the editor.
['dragover','dragenter'].forEach(ev=>document.addEventListener(ev,e=>{
 if(!e.dataTransfer?.types?.includes('Files'))return;
 e.preventDefault(); document.body.classList.add('drop-target');
}));
['dragleave','drop'].forEach(ev=>document.addEventListener(ev,e=>{
 if(ev==='dragleave' && e.relatedTarget)return;
 document.body.classList.remove('drop-target');
}));
document.addEventListener('drop',async e=>{
 const file=e.dataTransfer?.files?.[0]; if(!file)return;
 e.preventDefault(); await openFile(file);
});

editor.addEventListener('input',updateCounts);
$$('.toolbar button[data-command]').forEach(b=>b.onclick=()=>{document.execCommand(b.dataset.command,false,null);editor.focus()});
$('#blockFormat').onchange=e=>{document.execCommand('formatBlock',false,e.target.value);editor.focus()};
$('#linkButton').onclick=()=>{const u=prompt('Link URL');if(u)document.execCommand('createLink',false,u)};
$('#focusMode').onclick=()=>{document.body.classList.toggle('focus-mode');$('#focusMode').textContent=document.body.classList.contains('focus-mode')?'Exit focus':'Focus'};

function panel(name){ $$('.panel').forEach(p=>p.classList.toggle('active',p.id===`panel-${name}`)); $$('.rail-button[data-panel]').forEach(b=>b.classList.toggle('active',b.dataset.panel===name)); $('.inspector').classList.remove('closed'); }
$$('.rail-button[data-panel]').forEach(b=>b.onclick=()=>panel(b.dataset.panel));
$$('.panel-close').forEach(b=>b.onclick=()=>$('.inspector').classList.add('closed'));

function resetReports(){ state.lastAudit=null; state.lastFormatting=null; state.candidate=null; $('#scoreLabel').textContent='Not analysed';$('#scoreSummary').textContent='Run an audit to see what is in the current document.';$('#hardCount').textContent='0';$('#reviewCount').textContent='0';$('#softCount').textContent='0';$('#findingList').innerHTML='<p>No findings yet.</p>';$('#findingList').classList.add('empty-state');$('#formatMetrics').innerHTML='';$('#formatAdvice').innerHTML='<div class="advice"><span>01</span><p>Run an audit to generate formatting recommendations.</p></div>';$('#comparison').classList.add('hidden');$('#suggestionList').innerHTML=''; }

async function runAudit(){ const t=text();if(!t){showToast('Add some text first');return}
 if(!$('#genreSelect').value){showToast('Choose the document job first — it sets the severity thresholds');$('#genreSelect').focus();return}
 const btn=$('#auditButton');btn.classList.add('loading');btn.textContent='Auditing…'; try{const d=await postJSON('/api/audit',{text:t,genre:$('#genreSelect').value});renderAudit(d.audit,d.formatting);showToast('Audit complete')}catch(e){showToast(e.message)}finally{btn.classList.remove('loading');btn.textContent='Run audit'} }
$('#auditButton').onclick=runAudit; $('#genreSelect').onchange=()=>{if(text()&&$('#genreSelect').value)runAudit()};

function renderAudit(audit, formatting){
 state.lastAudit=audit;state.lastFormatting=formatting;
 const c=audit.counts;
 const label={clean:'No findings',line_edit:'Minor edits',substantive_edit:'Substantive edits',
              rebuild_required:'Extensive edits',not_assessable:'Not assessable'}[audit.status]||'Reviewed';
 $('#scoreLabel').textContent=label;
 // Say what is in the document and what was excused. No composite score: one number
 // standing for a document is exactly what this tool declines to report.
 const excused=(audit.metrics&&audit.metrics.allowListExemptionCount)||0;
 let summary = audit.status==='not_assessable'
   ? `Mostly tables, code or quotation. Too little prose to judge.`
   : `${c.findings} finding${c.findings===1?'':'s'} in ${c.words.toLocaleString()} words.`;
 if(excused) summary += ` ${excused} field-standard term${excused===1?'':'s'} excused.`;
 $('#scoreSummary').textContent=summary;
 $('#hardCount').textContent=c.hard;$('#reviewCount').textContent=c.review;$('#softCount').textContent=c.soft;
 const list=$('#findingList'); list.classList.toggle('empty-state',!audit.findings.length);
 list.innerHTML=audit.findings.length?'':'<p>Nothing crossed the threshold for this document type.</p>';
 audit.findings.forEach(f=>{ const el=document.createElement('div');el.className='finding';
  el.innerHTML=`<div class="finding-top"><h4>${escapeHTML(f.title)}</h4><span class="badge">${escapeHTML(f.severity)}</span></div><q>${escapeHTML(shorten(f.original,130))}</q><p>${escapeHTML(f.rationale)}</p>`;
  el.onclick=()=>showToast(f.action);list.appendChild(el)});
 renderFormatting(formatting); }

function renderFormatting(f){ if(!f)return; $('#formatIntro').textContent=`${f.label}. Advice is adapted to this document job.`; const m=f.metrics;$('#formatMetrics').innerHTML=`<div><strong>${m.paragraphs}</strong><span>Paragraphs</span></div><div><strong>${m.longSentences}</strong><span>Long sentences</span></div><div><strong>${m.headingsDetected}</strong><span>Headings</span></div>`; const advice=[...f.diagnostics,...f.recommendations];$('#formatAdvice').innerHTML=advice.map((x,i)=>`<div class="advice"><span>${String(i+1).padStart(2,'0')}</span><p>${escapeHTML(x)}</p></div>`).join(''); }

$('#repairSafe').onclick=async()=>{const t=text();if(!t)return;try{const d=await postJSON('/api/repair',{text:t,genre:$('#genreSelect').value}); if(!d.changed){showToast('No safe deterministic repair was available');return} state.candidate=d.finalText;renderCandidate(d.finalText,d.auditBefore?.editorialBurden,d.auditAfter?.editorialBurden);panel('suggestions');showToast('Safe repair prepared for review')}catch(e){showToast(e.message)}};

$('#suggestButton').onclick=async()=>{const t=text();if(!t){showToast('Add some text first');return}const b=$('#suggestButton');b.classList.add('loading');b.textContent='Analysing…';try{const d=await postJSON('/api/suggest',{text:t,genre:$('#genreSelect').value,max_candidates:3});renderSuggestions(d);panel('suggestions')}catch(e){showToast(e.message)}finally{b.classList.remove('loading');b.textContent='Generate local suggestions'}};
function renderSuggestions(d){const box=$('#suggestionList');box.innerHTML=''; if(!d.findings?.length){box.innerHTML='<div class="empty-state"><p>No local suggestion is needed.</p></div>';return} d.findings.forEach(f=>{const c=(f.candidates||[])[0];if(!c)return;const el=document.createElement('div');el.className='suggestion';el.innerHTML=`<h4>${escapeHTML(f.title)}</h4><div class="before">${escapeHTML(shorten(f.original,160))}</div><div class="arrow">↓</div><div class="after">${escapeHTML(c.preview||c.replacement||'Needs author input')}</div>${c.safeToApply?'<button class="button secondary compact">Apply this edit</button>':''}`;const btn=$('button',el);if(btn)btn.onclick=()=>applySpan(f.span,c.replacement||c.preview);box.appendChild(el)})}
function applySpan(span,replacement){const t=text();if(!span||replacement==null)return; const next=t.slice(0,span.start)+replacement+t.slice(span.end);editor.textContent=next;updateCounts();showToast('Suggestion applied');runAudit()}

/* Provider panel. Model IDs are discovered from the provider rather than typed: they
   change often, and a mistyped one returns a 404 that reads like a bug in this app. */

function currentProvider(){ return $('#providerSelect').value }
function isLocalModel(){ return currentProvider()==='chrome-nano' }

function syncProviderFields(){
 const p=currentProvider();
 $('#baseUrlLabel').classList.toggle('hidden', p!=='openai-compatible');
 $('#apiKeyLabel').classList.toggle('hidden', isLocalModel());
 $('#modelSelect').innerHTML='<option value="">Load the model list first</option>';
 $('#modelName').classList.add('hidden');
 $('#modelHint').textContent = isLocalModel()
  ? 'Runs on this device. No key, no network, no cost. Chrome 138 or later.'
  : p==='openrouter' ? 'OpenRouter lists its catalogue without a key. Free models are listed first.'
  : p==='omniroute' ? 'Expects OmniRoute on http://localhost:20128. Start it with: npx omniroute'
  : '';
 if(isLocalModel()) loadModels();
}
$('#providerSelect').onchange=syncProviderFields;

$('#keyVisibility').onclick=()=>{const i=$('#apiKey');i.type=i.type==='password'?'text':'password';$('#keyVisibility').textContent=i.type==='password'?'Show':'Hide'};

async function loadModels(){
 const b=$('#loadModels'); const sel=$('#modelSelect');
 b.classList.add('loading'); b.textContent='Loading…';
 try{
  const r=await WriteRoute.listModels(currentProvider(),{apiKey:$('#apiKey').value, baseUrl:$('#baseUrl').value});
  sel.innerHTML='';
  if(r.availability && r.availability!=='available'){
   // Chrome downloads the model on first use; say so rather than appearing to hang.
   $('#modelHint').textContent = r.availability==='downloadable'
    ? 'Chrome will download the model the first time you rewrite. This takes a few minutes.'
    : `Chrome reports the built-in model as ${r.availability}.`;
  }
  const free=r.models.filter(m=>m.free), paid=r.models.filter(m=>!m.free);
  const add=(list,label)=>{ if(!list.length)return;
   const g=document.createElement('optgroup'); g.label=label;
   list.forEach(m=>{const o=document.createElement('option');o.value=m.id;
    o.textContent=m.context?`${m.label} · ${Math.round(m.context/1000)}k`:m.label; g.appendChild(o)});
   sel.appendChild(g)};
  add(free, free.length && paid.length ? 'Free' : (free.length?'Available':''));
  add(paid, free.length ? 'Paid' : 'Available');
  if(!sel.options.length) sel.innerHTML='<option value="">No usable model was listed</option>';
  sel.selectedIndex=0;
  $('#providerStatus').textContent=`${r.models.length} model${r.models.length===1?'':'s'} from ${r.label}`
   + (r.freeCount? `, ${r.freeCount} free.` : '.');
  showToast(`${r.models.length} models loaded`);
 }catch(e){
  // Falling back to a text field beats a dead end: some local endpoints serve no list.
  sel.innerHTML='<option value="">Could not load the list</option>';
  $('#modelName').classList.remove('hidden');
  $('#modelHint').textContent='Enter a model ID manually.';
  $('#providerStatus').textContent=e.message;
  showToast(e.message);
 }finally{ b.classList.remove('loading'); b.textContent='Load available models' }
}
$('#loadModels').onclick=loadModels;
function chosenModel(){ return $('#modelName').classList.contains('hidden') ? $('#modelSelect').value : $('#modelName').value }
syncProviderFields();

$('#rewriteButton').onclick=async()=>{
 const t=text(); if(!t)return showToast('Add some text first');
 const local=isLocalModel();
 if(!local && !$('#apiKey').value){panel('provider');showToast('Add your API key, or switch to the Chrome built-in model');return}
 if(!local && !chosenModel()){panel('provider');showToast('Load the model list and choose a model');return}
 if(!$('#genreSelect').value){showToast('Choose the document type first');return}
 const b=$('#rewriteButton');b.classList.add('loading');b.textContent='Generating…';
 try{
  const d=await WriteRoute.rewrite({
   text:t, genre:$('#genreSelect').value,
   provider:currentProvider(), apiKey:$('#apiKey').value,
   model:chosenModel(), baseUrl:$('#baseUrl').value,
   candidates:Number($('#candidateCount').value), temperature:Number($('#temperature').value),
   onProgress:pct=>{b.textContent=`Downloading model ${pct}%`},
  });
  if(d.providerErrors?.length) showToast(d.providerErrors[0]);
  if(!d.changed){
   showToast(d.reason||'No candidate passed the checks');
   if(d.auditBefore)renderAudit(d.auditBefore,state.lastFormatting);
   return;
  }
  state.candidate=d.finalText;
  renderCandidate(d.finalText,d.auditBefore?.editorialBurden,d.auditAfter?.editorialBurden);
  showToast('A rewrite passed the checks');
 }catch(e){showToast(e.message)}
 finally{b.classList.remove('loading');b.textContent='Rewrite with BYOK'}
};
function renderCandidate(candidate,before,after){state.candidate=candidate;$('#candidatePreview').textContent=candidate;$('#comparison').classList.remove('hidden'); if(before!=null&&after!=null)$('#rewriteDelta').textContent=`Burden ${Number(before).toFixed(0)} → ${Number(after).toFixed(0)}`;else $('#rewriteDelta').textContent='Preservation checked'}
$('#acceptRewrite').onclick=()=>{if(state.candidate==null)return;editor.textContent=state.candidate;state.candidate=null;$('#comparison').classList.add('hidden');updateCounts();runAudit();showToast('Rewrite accepted')};
$('#rejectRewrite').onclick=()=>{state.candidate=null;$('#comparison').classList.add('hidden');showToast('Original retained')};

$('#exportOpen').onclick=()=>$('#exportDialog').showModal();
$$('[data-export]').forEach(b=>b.onclick=async e=>{
 e.preventDefault();
 try{
  const out=await exportDocument({text:text(), html:editor.innerHTML,
   filename:$('#docTitle').value||'writeroute-document', format:b.dataset.export});
  download(out);
  $('#exportDialog').close();
  showToast(`${b.dataset.export.toUpperCase()} exported`);
 }catch(err){showToast(err.message)}
});
function shorten(s,n){s=(s||'').replace(/\s+/g,' ').trim();return s.length>n?s.slice(0,n-1)+'…':s}
function escapeHTML(s){return String(s??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]))}

// Initial keyboard shortcuts
addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key==='Enter'){e.preventDefault();runAudit()}if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='e'&&e.shiftKey){e.preventDefault();$('#exportDialog').showModal()}});


/* ------------------------------------------------------------------ Super Engine & Subsystems */
async function runSuperAudit() {
  const t = text();
  if (!t) { showToast('Add some text first'); return; }
  const btn = $('#superAuditButton');
  btn.classList.add('loading');
  btn.textContent = 'Running Super-Audit…';
  try {
    const res = await fetch('/api/super-audit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: t,
        section: 'general',
        study_design: $('#statsDesignSelect')?.value || 'observational_cohort',
        target_guideline: 'CONSORT',
      }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    renderSuperAudit(data);
    showToast('Super-Audit complete across all 5 engines');
  } catch (err) {
    showToast('Super-audit error: ' + err.message);
  } finally {
    btn.classList.remove('loading');
    btn.textContent = 'Run Super-Audit';
  }
}
$('#superAuditButton').onclick = runSuperAudit;

function renderSuperAudit(data) {
  const s = data.summary;
  $('#superScoreLabel').textContent = `Integrity Score: ${s.overall_score}/100`;
  $('#superScoreSummary').textContent = `${s.total_findings_count} total issues flagged across statistical, style, prose, and clarity engines.`;
  $('#superFatalCount').textContent = s.fatal_findings_count;
  $('#superCritCount').textContent = s.critical_findings_count;
  $('#superTotalCount').textContent = s.total_findings_count;

  $('#superScoreStats').textContent = `${s.statistical_score}%`;
  $('#superScoreStyle').textContent = `${s.style_burden_score}%`;
  $('#superScoreProse').textContent = `${s.prose_quality_score}%`;
  $('#superScoreLucid').textContent = `${s.lucid_clarity_score}%`;
  $('#superScoreGuide').textContent = `${s.guidelines_score}%`;
  $('#superScoreOverall').textContent = `${s.overall_score}%`;

  const list = $('#superFindingList');
  list.classList.remove('empty-state');
  list.innerHTML = '';

  const allFindings = [
    ...(data.statistical_findings || []).map(f => ({ ...f, engine: 'STATS-BRAIN', msg: f.summary || f.message, sev: f.severity })),
    ...(data.pattern_findings || []).map(f => ({ ...f, engine: 'STYLE-PATTERN', msg: f.message, sev: f.severity })),
    ...(data.lucid_findings || []).map(f => ({ ...f, engine: 'LUCID-SCI', msg: f.message, sev: f.severity })),
    ...(data.prose_findings || []).map(f => ({ ...f, engine: 'PROSE', msg: f.rationale || f.message, sev: f.severity })),
  ];

  if (!allFindings.length) {
    list.innerHTML = '<p>No findings — text passed all engine gates!</p>';
    list.classList.add('empty-state');
    return;
  }

  for (const item of allFindings.slice(0, 15)) {
    const el = document.createElement('div');
    el.className = 'finding';
    el.innerHTML = `
      <div class="finding-top">
        <h4>${escapeHTML(item.engine)}: ${escapeHTML(item.rule_id || item.id || 'Finding')}</h4>
        <span class="badge ${item.sev === 'fatal' || item.sev === 'critical' ? 'dot hard' : 'dot review'}">${escapeHTML(item.sev || 'info')}</span>
      </div>
      <p>${escapeHTML(item.msg || '')}</p>
      ${item.matched_text ? `<q>“${escapeHTML(shorten(item.matched_text, 100))}”</q>` : ''}
    `;
    list.appendChild(el);
  }
}

// STATS-BRAIN
let lastAuctorPacket = null;
$('#runStatsButton').onclick = async () => {
  const t = text();
  if (!t) { showToast('Add some text first'); return; }
  const btn = $('#runStatsButton');
  btn.classList.add('loading');
  try {
    const res = await fetch('/api/stats-review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: t,
        study_design: $('#statsDesignSelect').value,
        section: 'general',
      }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    lastAuctorPacket = data.auctor_packet;
    $('#downloadAuctorPacket').style.display = 'block';
    renderStatsReview(data.report);
    showToast('STATS-BRAIN review complete');
  } catch (e) {
    showToast(e.message);
  } finally {
    btn.classList.remove('loading');
  }
};

function renderStatsReview(report) {
  $('#statsScoreLabel').textContent = `Score: ${report.score}/100 (${report.release_decision.toUpperCase()})`;
  $('#statsScoreSummary').textContent = `${report.findings.length} findings across estimand, design, and estimation dimensions.`;
  const list = $('#statsFindingList');
  list.classList.remove('empty-state');
  list.innerHTML = '';
  for (const f of report.findings) {
    const el = document.createElement('div');
    el.className = 'finding';
    el.innerHTML = `
      <div class="finding-top">
        <h4>${escapeHTML(f.rule_id)}</h4>
        <span class="badge ${f.severity === 'fatal' ? 'dot hard' : 'dot review'}">${escapeHTML(f.severity)}</span>
      </div>
      <p>${escapeHTML(f.summary)}</p>
      ${f.preservation_action ? `<small style="color:var(--blue)">Action: ${escapeHTML(f.preservation_action)}</small>` : ''}
    `;
    list.appendChild(el);
  }
}

$('#downloadAuctorPacket').onclick = () => {
  if (!lastAuctorPacket) return;
  const blob = new Blob([JSON.stringify(lastAuctorPacket, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${state.filename || 'manuscript'}.auctor_packet.json`;
  a.click();
  URL.revokeObjectURL(url);
  showToast('Auctor Evidence Packet downloaded');
};

// Pattern Engine
$('#runPatternButton').onclick = async () => {
  const t = text();
  if (!t) { showToast('Add text first'); return; }
  const btn = $('#runPatternButton');
  btn.classList.add('loading');
  try {
    const res = await fetch('/api/pattern-audit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: t, section: 'general' }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    $('#patternScoreLabel').textContent = `Burden Index: ${data.style_burden_index.toFixed(2)}`;
    $('#patternScoreSummary').textContent = `Defect purity score: ${(data.defect_purity_score * 100).toFixed(0)}%. ${data.findings.length} findings.`;
    const list = $('#patternFindingList');
    list.classList.remove('empty-state');
    list.innerHTML = '';
    for (const f of data.findings) {
      const el = document.createElement('div');
      el.className = 'finding';
      el.innerHTML = `
        <div class="finding-top">
          <h4>${escapeHTML(f.rule_id)} (${escapeHTML(f.category)})</h4>
          <span class="badge">${escapeHTML(f.severity)}</span>
        </div>
        <p>${escapeHTML(f.message)}</p>
        ${f.matched_text ? `<q>“${escapeHTML(shorten(f.matched_text, 100))}”</q>` : ''}
      `;
      list.appendChild(el);
    }
    showToast('Pattern scan complete');
  } catch (e) {
    showToast(e.message);
  } finally {
    btn.classList.remove('loading');
  }
};

// Lucid
$('#runLucidButton').onclick = async () => {
  const t = text();
  if (!t) { showToast('Add text first'); return; }
  const btn = $('#runLucidButton');
  btn.classList.add('loading');
  try {
    const res = await fetch('/api/lucid-lint', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: t }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    $('#lucidScoreLabel').textContent = `Clarity Score: ${data.score}/100`;
    $('#lucidScoreSummary').textContent = `${data.findings_count} flagged items, ${data.long_sentences} long sentences.`;
    const list = $('#lucidFindingList');
    list.classList.remove('empty-state');
    list.innerHTML = '';
    for (const f of data.findings) {
      const el = document.createElement('div');
      el.className = 'finding';
      el.innerHTML = `
        <div class="finding-top">
          <h4>${escapeHTML(f.category)}</h4>
          <span class="badge ${f.severity === 'critical' ? 'dot hard' : 'dot review'}">${escapeHTML(f.severity)}</span>
        </div>
        <p>${escapeHTML(f.message)}</p>
        <q>“${escapeHTML(shorten(f.matched_text, 80))}”</q>
      `;
      list.appendChild(el);
    }
    showToast('Lucid clarity scan complete');
  } catch (e) {
    showToast(e.message);
  } finally {
    btn.classList.remove('loading');
  }
};

// Word DOCX Processor
let lastProcessedDocxBlob = null;
$('#docxInput').onchange = async (e) => {
  const file = e.target.files?.[0];
  if (!file) return;
  showToast('Processing Word document with Auctor OOXML engine…');
  $('#docxStatusLabel').textContent = 'Processing…';
  $('#docxStatusSummary').textContent = `Analyzing ${file.name} for tracked changes and editorial comments.`;
  const form = new FormData();
  form.append('file', file);
  form.append('author', 'WriteRoute SuperEngine');
  form.append('apply_safe_edits', 'true');
  form.append('track_changes', 'true');
  form.append('add_comments', 'true');
  try {
    const res = await fetch('/api/docx/prepare', {
      method: 'POST',
      body: form,
    });
    if (!res.ok) throw new Error(await res.text());
    lastProcessedDocxBlob = await res.blob();
    $('#downloadTrackedDocx').style.display = 'block';
    $('#docxStatusLabel').textContent = 'Ready for Download';
    $('#docxStatusSummary').textContent = `Tracked revisions and comments injected into ${file.name}.`;
    showToast('Word document prepared with tracked changes');
  } catch (err) {
    $('#docxStatusLabel').textContent = 'Processing Error';
    $('#docxStatusSummary').textContent = err.message;
    showToast('DOCX error: ' + err.message);
  }
};

$('#downloadTrackedDocx').onclick = () => {
  if (!lastProcessedDocxBlob) return;
  const url = URL.createObjectURL(lastProcessedDocxBlob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `reviewed_${state.filename || 'manuscript'}.docx`;
  a.click();
  URL.revokeObjectURL(url);
  showToast('Downloaded reviewed Word DOCX');
};

/* ------------------------------------------------------------------ menu bar & shortcuts */
/* ------------------------------------------------------------------ menu bar & shortcuts */
const MENUS = [['#menuFileBtn', '#menuFile'], ['#menuEditBtn', '#menuEdit'], ['#menuReviewBtn', '#menuReview'], ['#menuViewBtn', '#menuView']];
function closeMenus() {
  $$('.menu').forEach(m => {
    m.classList.remove('open');
    const b = $('.menu-title', m);
    if (b) b.setAttribute('aria-expanded', 'false');
  });
}
MENUS.forEach(([btnSel]) => {
  const btn = $(btnSel);
  if (!btn) return;
  btn.onclick = e => {
    e.stopPropagation();
    const menu = btn.closest('.menu');
    const wasOpen = menu?.classList.contains('open');
    closeMenus();
    if (!wasOpen && menu) {
      menu.classList.add('open');
      btn.setAttribute('aria-expanded', 'true');
    }
  };
});
document.addEventListener('click', closeMenus);
addEventListener('keydown', e => { if (e.key === 'Escape') closeMenus(); });
$$('.menu-list button').forEach(b => b.addEventListener('click', () => setTimeout(closeMenus, 0)));

$('#menuOpen')?.addEventListener('click', () => $('#fileInputWorkspace')?.click());
$('#menuNew')?.addEventListener('click', () => $('#newDoc')?.click());
$('#menuAudit')?.addEventListener('click', runSuperAudit);
$('#menuSuggest')?.addEventListener('click', () => $('#suggestButton')?.click());
$('#menuRepair')?.addEventListener('click', () => $('#repairSafe')?.click());
$('#menuModel')?.addEventListener('click', () => panel('provider'));
$('#menuFocus')?.addEventListener('click', () => $('#focusMode')?.click());
$('#menuInspector')?.addEventListener('click', () => $('.inspector')?.classList.toggle('closed'));
$('#menuTheme')?.addEventListener('click', () => $('#themeToggle')?.click());

/* ------------------------------------------------------------------ Comprehensive Authoring Suite */

// Helper to insert HTML safely at current cursor selection
function insertHTMLAtCursor(html) {
  editor.focus();
  const sel = window.getSelection();
  if (sel && sel.rangeCount) {
    const range = sel.getRangeAt(0);
    range.deleteContents();
    const el = document.createElement('div');
    el.innerHTML = html;
    const frag = document.createDocumentFragment();
    let node, lastNode;
    while ((node = el.firstChild)) {
      lastNode = frag.appendChild(node);
    }
    range.insertNode(frag);
    if (lastNode) {
      range.setStartAfter(lastNode);
      range.collapse(true);
      sel.removeAllRanges();
      sel.addRange(range);
    }
  } else {
    editor.innerHTML += html;
  }
  updateCounts();
  updateOutline();
}

// Inline Code formatting toggle
$('#inlineCodeBtn')?.addEventListener('click', () => {
  const sel = window.getSelection();
  if (!sel || !sel.rangeCount || sel.isCollapsed) {
    insertHTMLAtCursor('<code>code</code>');
    return;
  }
  const text = sel.toString();
  document.execCommand('insertHTML', false, `<code>${escapeHTML(text)}</code>`);
});

// Horizontal Divider
$('#toolbarHrBtn')?.addEventListener('click', () => {
  insertHTMLAtCursor('<hr style="margin:24px 0; border:0; border-top:1px solid var(--line);"><p><br></p>');
});

// 1. Table Builder
$('#toolbarTableBtn')?.addEventListener('click', () => $('#modalTable')?.showModal());
$('#menuInsertTable')?.addEventListener('click', () => $('#modalTable')?.showModal());
$('#insertTableConfirm')?.addEventListener('click', () => {
  const rows = Math.max(2, parseInt($('#tableRows').value) || 4);
  const cols = Math.max(1, parseInt($('#tableCols').value) || 3);
  const caption = $('#tableCaption').value.trim();
  const hasHeader = $('#tableHeaderCheck').checked;

  let tableHtml = '<table class="scientific-table">\n';
  if (caption) tableHtml += `  <caption>${escapeHTML(caption)}</caption>\n`;
  tableHtml += '  <tbody>\n';

  for (let r = 0; r < rows; r++) {
    tableHtml += '    <tr>\n';
    for (let c = 0; c < cols; c++) {
      if (r === 0 && hasHeader) {
        tableHtml += `      <th>Header ${c + 1}</th>\n`;
      } else {
        tableHtml += `      <td>Data ${r},${c + 1}</td>\n`;
      }
    }
    tableHtml += '    </tr>\n';
  }
  tableHtml += '  </tbody>\n</table>\n<p><br></p>';
  insertHTMLAtCursor(tableHtml);
  $('#modalTable')?.close();
  showToast('Table inserted');
});

// 2. LaTeX / Equation Builder
$('#toolbarEqBtn')?.addEventListener('click', () => $('#modalEquation')?.showModal());
$('#menuInsertEq')?.addEventListener('click', () => $('#modalEquation')?.showModal());
$$('.quick-symbols button').forEach(btn => {
  btn.onclick = () => {
    const input = $('#equationInput');
    const sym = btn.dataset.symbol;
    input.value += (input.value ? ' ' : '') + sym;
    input.focus();
  };
});
$('#insertEquationConfirm')?.addEventListener('click', () => {
  const eq = $('#equationInput').value.trim();
  if (!eq) return;
  const isBlock = $('input[name="eqMode"]:checked')?.value === 'block';
  if (isBlock) {
    insertHTMLAtCursor(`<div class="math-equation" data-tex="${escapeHTML(eq)}">$$ ${escapeHTML(eq)} $$</div><p><br></p>`);
  } else {
    insertHTMLAtCursor(`<span class="inline-equation" data-tex="${escapeHTML(eq)}">\\( ${escapeHTML(eq)} \\)</span> `);
  }
  $('#modalEquation')?.close();
  showToast('Equation inserted');
});

// 3. Callout Box Builder
$('#toolbarCalloutBtn')?.addEventListener('click', () => $('#modalCallout')?.showModal());
$('#menuInsertCallout')?.addEventListener('click', () => $('#modalCallout')?.showModal());
$('#insertCalloutConfirm')?.addEventListener('click', () => {
  const type = $('#calloutTypeSelect').value;
  const title = $('#calloutTitleInput').value.trim() || type.toUpperCase();
  const content = $('#calloutContentInput').value.trim() || 'Callout text here...';
  const svgMap = {
    note: '<svg class="svg-icon" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
    method: '<svg class="svg-icon" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
    warning: '<svg class="svg-icon" viewBox="0 0 24 24"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
    tip: '<svg class="svg-icon" viewBox="0 0 24 24"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>',
  };
  const iconSvg = svgMap[type] || svgMap.note;

  const html = `<div class="callout-box ${type}"><div class="callout-title"><span>${iconSvg}</span> <strong>${escapeHTML(title)}</strong></div><p>${escapeHTML(content)}</p></div><p><br></p>`;
  insertHTMLAtCursor(html);
  $('#modalCallout')?.close();
  showToast('Callout box inserted');
});


// 4. Figure Inserter
$('#toolbarFigBtn')?.addEventListener('click', () => $('#modalFigure')?.showModal());
$('#menuInsertFig')?.addEventListener('click', () => $('#modalFigure')?.showModal());
$('#insertFigureConfirm')?.addEventListener('click', () => {
  const url = $('#figureUrlInput').value.trim() || 'https://images.unsplash.com/photo-1507668077129-56e32842fceb?w=800';
  const caption = $('#figureCaptionInput').value.trim() || 'Figure: Overview diagram.';
  const html = `<figure class="scientific-figure"><img src="${escapeHTML(url)}" alt="${escapeHTML(caption)}" /><figcaption>${escapeHTML(caption)}</figcaption></figure><p><br></p>`;
  insertHTMLAtCursor(html);
  $('#modalFigure')?.close();
  showToast('Figure inserted');
});

// 5. Find & Replace System
const findBar = $('#findReplaceBar');
let searchMatches = [];
let currentMatchIndex = -1;

function toggleFindBar(show) {
  const isHidden = findBar.classList.contains('hidden');
  const shouldShow = show !== undefined ? show : isHidden;
  findBar.classList.toggle('hidden', !shouldShow);
  if (shouldShow) {
    $('#findInput').focus();
    $('#findInput').select();
    performFind();
  } else {
    clearSearchHighlights();
  }
}
$('#findToggleBtn')?.addEventListener('click', () => toggleFindBar());
$('#menuFind')?.addEventListener('click', () => toggleFindBar(true));
$('#closeFindBtn')?.addEventListener('click', () => toggleFindBar(false));

function clearSearchHighlights() {
  $$('.search-highlight', editor).forEach(el => {
    const parent = el.parentNode;
    parent.replaceChild(document.createTextNode(el.textContent), el);
    parent.normalize();
  });
  searchMatches = [];
  currentMatchIndex = -1;
  $('#matchCount').textContent = '0 of 0';
}

function performFind() {
  const query = $('#findInput').value;
  clearSearchHighlights();
  if (!query) return;

  const isCase = $('#caseSensitiveCheck').checked;
  const isRegex = $('#regexCheck').checked;

  let regex;
  try {
    regex = isRegex ? new RegExp(query, isCase ? 'g' : 'gi') : new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), isCase ? 'g' : 'gi');
  } catch (err) {
    $('#matchCount').textContent = 'Invalid regex';
    return;
  }

  // Walk text nodes
  const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT, null, false);
  const textNodes = [];
  let n;
  while ((n = walker.nextNode())) textNodes.push(n);

  textNodes.forEach(node => {
    const textVal = node.nodeValue;
    let match;
    let lastIdx = 0;
    const frags = [];
    let matched = false;

    while ((match = regex.exec(textVal)) !== null) {
      matched = true;
      if (match.index > lastIdx) {
        frags.push(document.createTextNode(textVal.substring(lastIdx, match.index)));
      }
      const span = document.createElement('span');
      span.className = 'search-highlight';
      span.textContent = match[0];
      frags.push(span);
      searchMatches.push(span);
      lastIdx = regex.lastIndex;
      if (!match[0].length) break; // Avoid infinite loop on empty regex matches
    }

    if (matched) {
      if (lastIdx < textVal.length) {
        frags.push(document.createTextNode(textVal.substring(lastIdx)));
      }
      const parent = node.parentNode;
      frags.forEach(f => parent.insertBefore(f, node));
      parent.removeChild(node);
    }
  });

  if (searchMatches.length > 0) {
    currentMatchIndex = 0;
    updateActiveMatch();
  } else {
    $('#matchCount').textContent = '0 matches';
  }
}

function updateActiveMatch() {
  searchMatches.forEach((el, idx) => {
    el.classList.toggle('active-match', idx === currentMatchIndex);
  });
  if (searchMatches[currentMatchIndex]) {
    searchMatches[currentMatchIndex].scrollIntoView({ behavior: 'smooth', block: 'center' });
    $('#matchCount').textContent = `${currentMatchIndex + 1} of ${searchMatches.length}`;
  } else {
    $('#matchCount').textContent = `${searchMatches.length} matches`;
  }
}

$('#findInput')?.addEventListener('input', performFind);
$('#caseSensitiveCheck')?.addEventListener('change', performFind);
$('#regexCheck')?.addEventListener('change', performFind);

$('#findNextBtn')?.addEventListener('click', () => {
  if (!searchMatches.length) return;
  currentMatchIndex = (currentMatchIndex + 1) % searchMatches.length;
  updateActiveMatch();
});
$('#findPrevBtn')?.addEventListener('click', () => {
  if (!searchMatches.length) return;
  currentMatchIndex = (currentMatchIndex - 1 + searchMatches.length) % searchMatches.length;
  updateActiveMatch();
});

$('#replaceBtn')?.addEventListener('click', () => {
  if (currentMatchIndex >= 0 && searchMatches[currentMatchIndex]) {
    const target = searchMatches[currentMatchIndex];
    const rep = $('#replaceInput').value;
    target.parentNode.replaceChild(document.createTextNode(rep), target);
    updateCounts();
    performFind();
  }
});

$('#replaceAllBtn')?.addEventListener('click', () => {
  const query = $('#findInput').value;
  const rep = $('#replaceInput').value;
  if (!query) return;
  const isCase = $('#caseSensitiveCheck').checked;
  const isRegex = $('#regexCheck').checked;
  try {
    const regex = isRegex ? new RegExp(query, isCase ? 'g' : 'gi') : new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), isCase ? 'g' : 'gi');
    const t = text();
    const count = (t.match(regex) || []).length;
    editor.textContent = t.replace(regex, rep);
    clearSearchHighlights();
    updateCounts();
    showToast(`Replaced ${count} occurrences`);
  } catch (e) {
    showToast(e.message);
  }
});

// 6. Live Outline Tree Navigator
function updateOutline() {
  const tree = $('#outlineTree');
  if (!tree) return;
  const headings = $$('h1, h2, h3, h4', editor);
  if (!headings.length) {
    tree.innerHTML = '<div class="empty-state"><p>No headings found. Add titles (H1) or sections (H2) to see the outline.</p></div>';
    return;
  }
  tree.innerHTML = '';
  headings.forEach((h, i) => {
    const tag = h.tagName.toLowerCase();
    const title = h.innerText.trim() || 'Untitled section';
    const item = document.createElement('div');
    item.className = `outline-item ${tag}`;
    item.innerHTML = `<span>${escapeHTML(title)}</span><span class="outline-word-tag">${tag.toUpperCase()}</span>`;
    item.onclick = () => {
      h.scrollIntoView({ behavior: 'smooth', block: 'start' });
      h.style.transition = 'background 0.3s';
      h.style.background = 'rgba(66,97,136,0.15)';
      setTimeout(() => { h.style.background = 'transparent'; }, 1000);
    };
    tree.appendChild(item);
  });
}
$('#refreshOutlineBtn')?.addEventListener('click', () => {
  updateOutline();
  showToast('Outline refreshed');
});

// 7. Citations & References Library
let citationsLibrary = JSON.parse(localStorage.getItem('writeroute-citations') || '[]');

// Seed default reference if empty
if (!citationsLibrary.length) {
  citationsLibrary = [
    {
      id: 'ref-default-1',
      cite_key: 'smith2024neonatal',
      item_type: 'article-journal',
      title: 'Neonatal Survival in Low-Resource Clinical Settings',
      authors: [{ family: 'Smith', given: 'John' }, { family: 'Jones', given: 'Alice' }],
      year: '2024',
      journal: 'The Lancet',
      volume: '403',
      issue: '10432',
      pages: '120-128',
      doi: '10.1016/S0140-6736(24)00123-4',
      pmid: '38192831',
    }
  ];
}

function saveCitations() {
  localStorage.setItem('writeroute-citations', JSON.stringify(citationsLibrary));
  renderCitations();
}

async function renderCitations(filterQuery = '') {
  const list = $('#citationList');
  const badge = $('#citeCountBadge');
  if (badge) badge.textContent = citationsLibrary.length;
  if (!list) return;

  const q = filterQuery.toLowerCase().trim();
  const filtered = citationsLibrary.filter(c => {
    if (!q) return true;
    const authorStr = (c.authors || []).map(a => `${a.family} ${a.given}`).join(' ').toLowerCase();
    const titleStr = (c.title || '').toLowerCase();
    const keyStr = (c.cite_key || '').toLowerCase();
    const yearStr = String(c.year || '');
    return authorStr.includes(q) || titleStr.includes(q) || keyStr.includes(q) || yearStr.includes(q);
  });

  if (!filtered.length) {
    list.innerHTML = `<div class="empty-state"><p>${q ? 'No matching citations found.' : 'No citations in library. Click "+ Add Reference" to import BibTeX or RIS entries.'}</p></div>`;
    return;
  }

  const style = $('#citationStyleSelect')?.value || 'apa';
  let formattedData = null;
  try {
    const res = await fetch('/api/citations/format', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: filtered, style: style }),
    });
    if (res.ok) formattedData = await res.json();
  } catch (_) {}

  list.innerHTML = '';
  filtered.forEach((c, idx) => {
    const entry = formattedData?.entries?.[idx];
    const inText = entry?.in_text || (c.authors?.length ? `(${c.authors[0].family} et al., ${c.year})` : `(${c.cite_key})`);
    const fullBib = entry?.bibliography_entry || `${(c.authors || []).map(a => a.family).join(', ')} (${c.year}). ${c.title}. <em>${c.journal || ''}</em>.`;

    const el = document.createElement('div');
    el.className = 'citation-item';
    el.style.padding = '10px';
    el.style.border = '1px solid var(--line)';
    el.style.borderRadius = 'var(--radius-cards)';
    el.style.marginBottom = '8px';
    el.style.background = 'var(--surface-solid)';
    el.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
        <strong style="color:var(--accent);font-size:12.5px">${escapeHTML(c.cite_key || `ref_${idx+1}`)}</strong>
        <span style="font-size:10.5px;color:var(--muted);background:var(--bg2);padding:2px 6px;border-radius:4px">${escapeHTML(c.year || '2024')}</span>
      </div>
      <p style="font-size:12px;color:var(--text);margin:4px 0 8px 0;line-height:1.4">${fullBib}</p>
      <div style="display:flex;gap:6px">
        <button class="button primary compact" data-cite-intext="${escapeHTML(inText)}" data-cite-key="${escapeHTML(c.cite_key)}">Insert ${escapeHTML(inText)}</button>
        <button class="button secondary compact" data-del-cite="${c.id}">Delete</button>
      </div>
    `;
    list.appendChild(el);
  });

  $$('[data-cite-intext]', list).forEach(btn => {
    btn.onclick = () => {
      const inText = btn.dataset.citeIntext;
      const key = btn.dataset.citeKey;
      insertHTMLAtCursor(` <span class="citation-tag" data-sdt="mendeley" data-key="${key}">${escapeHTML(inText)}</span> `);
      showToast(`Inserted in-text citation: ${inText}`);
    };
  });

  $$('[data-del-cite]', list).forEach(btn => {
    btn.onclick = () => {
      const id = btn.dataset.delCite;
      citationsLibrary = citationsLibrary.filter(c => c.id !== id);
      saveCitations();
      showToast('Reference removed from library');
    };
  });
}

$('#citationSearchInput')?.addEventListener('input', e => {
  renderCitations(e.target.value);
});

$('#citationStyleSelect')?.addEventListener('change', () => {
  renderCitations($('#citationSearchInput')?.value || '');
});

$('#openAddCiteModalBtn')?.addEventListener('click', () => $('#modalAddCite')?.showModal());
$('#toolbarCiteBtn')?.addEventListener('click', () => {
  panel('citations');
  $('#modalAddCite')?.showModal();
});
$('#menuInsertCite')?.addEventListener('click', () => {
  panel('citations');
  $('#modalAddCite')?.showModal();
});

$('#parseAndSaveCiteBtn')?.addEventListener('click', async () => {
  const raw = $('#citeRawTextInput')?.value.trim();
  if (!raw) return alert('Please paste a BibTeX or RIS reference string.');
  const fmt = $('#citeImportFormatSelect')?.value || 'bibtex';
  showToast('Parsing scientific reference...');
  try {
    const res = await fetch('/api/citations/parse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ raw_text: raw, format: fmt }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    if (!data.items?.length) throw new Error('No valid citations parsed from text.');

    data.items.forEach(item => {
      // Remove duplicate if same key exists
      citationsLibrary = citationsLibrary.filter(c => c.cite_key !== item.cite_key);
      citationsLibrary.push(item);
    });
    saveCitations();
    $('#citeRawTextInput').value = '';
    $('#modalAddCite')?.close();
    showToast(`Successfully added ${data.items.length} reference(s) to library`);
  } catch (err) {
    alert('Failed to parse reference: ' + err.message);
  }
});

$('#exportBibtexBtn')?.addEventListener('click', async () => {
  if (!citationsLibrary.length) return showToast('No citations in library');
  try {
    const res = await fetch('/api/citations/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: citationsLibrary, format: 'bibtex' }),
    });
    const data = await res.json();
    const blob = new Blob([data.content], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${(state.filename || 'library')}.bib`;
    a.click();
    showToast(`Exported ${citationsLibrary.length} citations to .bib`);
  } catch (err) {
    alert('Export error: ' + err.message);
  }
});

$('#exportRisBtn')?.addEventListener('click', async () => {
  if (!citationsLibrary.length) return showToast('No citations in library');
  try {
    const res = await fetch('/api/citations/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: citationsLibrary, format: 'ris' }),
    });
    const data = await res.json();
    const blob = new Blob([data.content], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${(state.filename || 'library')}.ris`;
    a.click();
    showToast(`Exported ${citationsLibrary.length} citations to .ris`);
  } catch (err) {
    alert('Export error: ' + err.message);
  }
});

$('#insertBibliographyBtn')?.addEventListener('click', async () => {
  if (!citationsLibrary.length) return showToast('No citations in library');
  const style = $('#citationStyleSelect')?.value || 'apa';
  try {
    const res = await fetch('/api/citations/format', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: citationsLibrary, style: style }),
    });
    const data = await res.json();
    let bibHtml = '\n<h2>References</h2>\n<ol class="references-list">\n';
    data.entries.forEach(e => {
      bibHtml += `  <li>${e.bibliography_entry}</li>\n`;
    });
    bibHtml += '</ol>\n<p><br></p>';
    insertHTMLAtCursor(bibHtml);
    showToast(`References (${style.toUpperCase()}) inserted into manuscript`);
  } catch (err) {
    alert('Format error: ' + err.message);
  }
});

$('#syncDocCitationsBtn')?.addEventListener('click', async () => {
  const t = text();
  if (!t) return showToast('Editor is empty');
  // Match in-text citations
  const matched = t.match(/\(([A-Za-z]+(?:\s+et\s+al\.)?,?\s*\d{4})\)|\[\d+\]|data-key="([^"]+)"/g) || [];
  showToast(`Synced with document: found ${matched.length} citations in text`);
  renderCitations();
});


// 8. Document Analytics & Cognitive Readability
function updateAnalytics() {
  const t = text();
  if (!t) return;

  const words = (t.match(/[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]|[\p{L}][\p{L}'\u2019-]*/gu) || []).length;
  const chars = t.length;
  const sentences = (t.match(/[.!?]+(?:\s+|$)/g) || []).length || 1;
  const paragraphs = t.split(/\n\s*\n/).filter(p => p.trim().length > 0).length || 1;

  const readTimeMin = Math.ceil(words / 220);
  const speakTimeMin = Math.ceil(words / 130);

  $('#statWords').textContent = words.toLocaleString();
  $('#statChars').textContent = chars.toLocaleString();
  $('#statSentences').textContent = sentences.toLocaleString();
  $('#statParagraphs').textContent = paragraphs.toLocaleString();
  $('#statReadTime').textContent = `${readTimeMin} min`;
  $('#statSpeakTime').textContent = `${speakTimeMin} min`;

  // Syllable approximation
  const wordTokens = t.toLowerCase().match(/\b[a-z]{2,}\b/g) || [];
  let syllables = 0;
  let complexWords = 0;
  wordTokens.forEach(w => {
    const syl = (w.match(/[aeiouy]{1,2}/g) || []).length || 1;
    syllables += syl;
    if (syl >= 3) complexWords++;
  });

  const avgWordsPerSent = sentences ? (words / sentences) : 0;
  const avgSyllablesPerWord = words ? (syllables / words) : 1;

  // Flesch Reading Ease
  const fleschEase = Math.max(0, Math.min(100, 206.835 - 1.015 * avgWordsPerSent - 84.6 * avgSyllablesPerWord));
  // Flesch-Kincaid Grade Level
  const gradeLevel = Math.max(1, 0.39 * avgWordsPerSent + 11.8 * avgSyllablesPerWord - 15.59);

  // Lexical Diversity
  const uniqueWords = new Set(wordTokens).size;
  const lexicalDiversity = words ? Math.round((uniqueWords / words) * 100) : 0;

  $('#readabilityGrade').textContent = `Grade Level: ${gradeLevel.toFixed(1)} (US Schooling)`;
  $('#readabilitySummary').textContent = `Flesch Reading Ease: ${fleschEase.toFixed(1)} / 100 (${fleschEase > 60 ? 'Plain & Accessible' : fleschEase > 30 ? 'Academic / Technical' : 'Dense Scientific'})`;

  $('#statAvgSentenceLen').textContent = avgWordsPerSent.toFixed(1);
  $('#statLexicalDiv').textContent = `${lexicalDiversity}%`;
  $('#statLongWords').textContent = complexWords.toLocaleString();
}

// 9. Version Snapshots & Revisions
let snapshotRevisions = JSON.parse(localStorage.getItem('writeroute-snapshots') || '[]');

function saveSnapshots() {
  localStorage.setItem('writeroute-snapshots', JSON.stringify(snapshotRevisions));
  renderHistory();
}

function takeSnapshot(label = 'Manual Snapshot') {
  const content = text();
  if (!content) return;
  const snap = {
    id: 'snap_' + Date.now(),
    title: label,
    time: new Date().toLocaleString(),
    timestamp: Date.now(),
    words: (content.match(/\S+/g) || []).length,
    content: content,
  };
  snapshotRevisions.unshift(snap);
  if (snapshotRevisions.length > 25) snapshotRevisions.pop();
  saveSnapshots();
  showToast(`Revision snapshot saved: ${label}`);
}

function renderHistory() {
  const list = $('#historyList');
  const badge = $('#snapshotCountBadge');
  if (badge) badge.textContent = snapshotRevisions.length;
  if (!list) return;

  if (!snapshotRevisions.length) {
    list.innerHTML = '<div class="empty-state"><p>No revisions saved yet. Click "Save Revision Snapshot" to bookmark your progress.</p></div>';
    return;
  }
  list.innerHTML = '';
  snapshotRevisions.forEach((s, idx) => {
    const el = document.createElement('div');
    el.className = 'history-item';
    el.innerHTML = `
      <div class="history-item-top">
        <strong>${escapeHTML(s.title)}</strong>
        <span class="history-item-time">${escapeHTML(s.time)}</span>
      </div>
      <div class="history-item-meta">${s.words} words</div>
      <div style="display:flex;gap:6px">
        <button class="button secondary compact" data-compare-snap="${idx}">Compare</button>
        <button class="button primary compact" data-restore-snap="${idx}">Restore</button>
      </div>
    `;
    list.appendChild(el);
  });

  $$('[data-compare-snap]', list).forEach(btn => {
    btn.onclick = () => {
      const snap = snapshotRevisions[btn.dataset.compareSnap];
      $('#diffModalTitle').textContent = `Diff: ${snap.title} (${snap.time})`;
      $('#diffContent').textContent = snap.content;
      $('#restoreDiffConfirm').dataset.targetSnap = btn.dataset.compareSnap;
      $('#modalDiff')?.showModal();
    };
  });

  $$('[data-restore-snap]', list).forEach(btn => {
    btn.onclick = () => {
      const snap = snapshotRevisions[btn.dataset.restoreSnap];
      if (confirm(`Restore revision from ${snap.time}? Unsaved changes in the current editor will be overwritten.`)) {
        editor.textContent = snap.content;
        updateCounts();
        updateOutline();
        showToast(`Restored snapshot: ${snap.title}`);
      }
    };
  });
}

$('#restoreDiffConfirm')?.addEventListener('click', () => {
  const idx = $('#restoreDiffConfirm').dataset.targetSnap;
  if (idx !== undefined && snapshotRevisions[idx]) {
    editor.textContent = snapshotRevisions[idx].content;
    updateCounts();
    updateOutline();
    $('#modalDiff')?.close();
    showToast('Revision restored');
  }
});

$('#takeSnapshotBtn')?.addEventListener('click', () => takeSnapshot('Draft Milestone'));
$('#menuSaveSnapshot')?.addEventListener('click', () => takeSnapshot('Manual Save'));

// Global Hotkeys for Authoring & Zoom
addEventListener('keydown', e => {
  if (!(e.metaKey || e.ctrlKey)) return;
  const k = e.key.toLowerCase();
  if (k === 's') { e.preventDefault(); takeSnapshot('Quick Save (Ctrl/Cmd+S)'); }
  if (k === 'f') { e.preventDefault(); toggleFindBar(); }
  if (k === 'h') { e.preventDefault(); toggleFindBar(true); $('#replaceInput')?.focus(); }
  if (e.key === '=' || e.key === '+') { e.preventDefault(); setDocZoom(currentDocZoom + 0.1); }
  if (e.key === '-' || e.key === '_') { e.preventDefault(); setDocZoom(currentDocZoom - 0.1); }
  if (e.key === '0') { e.preventDefault(); setDocZoom(1.0); }
});


// Periodic auto-snapshot every 4 minutes if dirty
setInterval(() => {
  if (state.dirty && text().length > 50) {
    takeSnapshot('Autosave Milestone');
  }
}, 240000);

/* ------------------------------------------------------------------ Word Suite: Comments, Track Changes, Layout, Footnotes & Goals */

// 1. Comments
let documentComments = JSON.parse(localStorage.getItem('writeroute-comments') || '[]');

function saveComments() {
  localStorage.setItem('writeroute-comments', JSON.stringify(documentComments));
  renderComments();
}

let activeCommentSelection = '';

function openAddCommentModal() {
  const sel = window.getSelection();
  const selectedText = sel ? sel.toString().trim() : '';
  activeCommentSelection = selectedText || 'General Manuscript Comment';
  $('#commentSelectionPreview').textContent = `"${activeCommentSelection}"`;
  $('#commentTextInput').value = '';
  $('#modalComment')?.showModal();
}

$('#addCommentBtn')?.addEventListener('click', openAddCommentModal);
$('#openAddCommentModalBtn')?.addEventListener('click', openAddCommentModal);

$('#saveCommentConfirm')?.addEventListener('click', () => {
  const author = $('#commentAuthorInput').value.trim() || 'Reviewer';
  const commentText = $('#commentTextInput').value.trim();
  if (!commentText) return;

  const commentObj = {
    id: 'c_' + Date.now(),
    author: author,
    time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    quote: activeCommentSelection,
    text: commentText,
    resolved: false,
  };

  documentComments.unshift(commentObj);
  saveComments();
  $('#modalComment')?.close();
  panel('comments');
  showToast('Comment posted');
});

function renderComments() {
  const list = $('#commentList');
  const badge = $('#commentCountBadge');
  if (badge) badge.textContent = documentComments.filter(c => !c.resolved).length;
  if (!list) return;

  if (!documentComments.length) {
    list.innerHTML = '<div class="empty-state"><p>No comments yet. Select text in the editor and click Add Comment to leave an editorial note.</p></div>';
    return;
  }

  list.innerHTML = '';
  documentComments.forEach((c, idx) => {
    const card = document.createElement('div');
    card.className = `comment-card ${c.resolved ? 'resolved' : ''}`;
    card.innerHTML = `
      <div class="comment-card-header">
        <span class="comment-card-author">${escapeHTML(c.author)}</span>
        <span class="comment-card-time">${escapeHTML(c.time)}</span>
      </div>
      <div class="comment-card-quote">"${escapeHTML(c.quote)}"</div>
      <div class="comment-card-body">${escapeHTML(c.text)}</div>
      <div class="comment-card-actions">
        <button class="button secondary compact" data-resolve-comment="${idx}">${c.resolved ? 'Reopen' : 'Resolve'}</button>
        <button class="button secondary compact" data-delete-comment="${idx}">Delete</button>
      </div>
    `;
    list.appendChild(card);
  });


  $$('[data-resolve-comment]', list).forEach(btn => {
    btn.onclick = () => {
      const idx = btn.dataset.resolveComment;
      documentComments[idx].resolved = !documentComments[idx].resolved;
      saveComments();
    };
  });

  $$('[data-delete-comment]', list).forEach(btn => {
    btn.onclick = () => {
      const idx = btn.dataset.deleteComment;
      documentComments.splice(idx, 1);
      saveComments();
      showToast('Comment removed');
    };
  });
}

// 2. Track Changes Mode
let trackChangesOn = false;
$('#trackChangesBtn')?.addEventListener('click', () => {
  trackChangesOn = !trackChangesOn;
  const btn = $('#trackChangesBtn');
  if (trackChangesOn) {
    btn.textContent = 'Track: ON';
    btn.classList.add('primary');
    btn.classList.remove('secondary');
    showToast('Track Changes enabled');
  } else {
    btn.textContent = 'Track: OFF';
    btn.classList.remove('primary');
    btn.classList.add('secondary');
    showToast('Track Changes disabled');
  }
});

$('#acceptAllChangesBtn')?.addEventListener('click', () => {
  $$('.tracked-insert', editor).forEach(el => {
    const textNode = document.createTextNode(el.textContent);
    el.parentNode.replaceChild(textNode, el);
  });
  $$('.tracked-delete', editor).forEach(el => el.remove());
  updateCounts();
  showToast('All tracked changes accepted');
});

$('#rejectAllChangesBtn')?.addEventListener('click', () => {
  $$('.tracked-insert', editor).forEach(el => el.remove());
  $$('.tracked-delete', editor).forEach(el => {
    const textNode = document.createTextNode(el.textContent);
    el.parentNode.replaceChild(textNode, el);
  });
  updateCounts();
  showToast('All tracked changes rejected');
});

// 3. Footnotes
let footnoteCounter = 1;
$('#toolbarFootnoteBtn')?.addEventListener('click', () => {
  const num = footnoteCounter++;
  insertHTMLAtCursor(`<sup class="footnote-ref" data-fn="${num}">[${num}]</sup>`);
  const fnArea = $('#footnotesArea');
  const fnList = $('#footnotesList');
  if (fnArea && fnList) {
    fnArea.classList.remove('hidden');
    const li = document.createElement('li');
    li.id = `footnote-item-${num}`;
    li.innerHTML = `Footnote ${num} note text reference.`;
    li.contentEditable = 'true';
    fnList.appendChild(li);
  }
  showToast(`Footnote [${num}] inserted`);
});

// 4. Page Setup & Goals
$('#pageSizeSelect')?.addEventListener('change', e => {
  editor.classList.remove('page-a4', 'page-letter');
  if (e.target.value === 'letter') editor.classList.add('page-letter');
  else editor.classList.add('page-a4');
  showToast(`Page size: ${e.target.value.toUpperCase()}`);
});

$('#pageOrientationSelect')?.addEventListener('change', e => {
  if (e.target.value === 'landscape') editor.classList.add('orientation-landscape');
  else editor.classList.remove('orientation-landscape');
  showToast(`Orientation: ${e.target.value}`);
});

$('#pageMarginSelect')?.addEventListener('change', e => {
  editor.classList.remove('margins-narrow', 'margins-wide');
  if (e.target.value === 'narrow') editor.classList.add('margins-narrow');
  else if (e.target.value === 'wide') editor.classList.add('margins-wide');
  showToast(`Margins: ${e.target.value}`);
});

let targetGoal = parseInt(localStorage.getItem('writeroute-target-goal') || '3000');
$('#targetGoalInput').value = targetGoal;
$('#goalCount').textContent = targetGoal.toLocaleString();

$('#setGoalBtn')?.addEventListener('click', () => {
  const g = Math.max(50, parseInt($('#targetGoalInput').value) || 3000);
  targetGoal = g;
  localStorage.setItem('writeroute-target-goal', g.toString());
  $('#goalCount').textContent = g.toLocaleString();
  updateGoalProgress();
  showToast(`Goal set to ${g.toLocaleString()} words`);
});

function updateGoalProgress() {
  const currentWords = (text().match(/\S+/g) || []).length;
  const pct = Math.min(100, Math.round((currentWords / targetGoal) * 100));
  const goalPct = $('#goalPct');
  if (goalPct) goalPct.textContent = `${pct}%`;
}

/* ------------------------------------------------------------------ Document Zoom & Scale Controller */
let currentDocZoom = 1.0;

function setDocZoom(val, announce = true) {
  let scale = 1.0;
  if (val === 'fit-width') {
    const wrapper = $('#manuscriptEditorWrapper');
    const ed = $('#editor');
    if (wrapper && ed) {
      const availWidth = wrapper.clientWidth - 48;
      const edWidth = ed.offsetWidth || 820;
      scale = Math.min(2.0, Math.max(0.5, Math.round((availWidth / edWidth) * 100) / 100));
    }
  } else if (val === 'fit-page') {
    const wrapper = $('#manuscriptEditorWrapper');
    const ed = $('#editor');
    if (wrapper && ed) {
      const availHeight = wrapper.clientHeight - 60;
      const edHeight = ed.offsetHeight || 1100;
      scale = Math.min(1.5, Math.max(0.4, Math.round((availHeight / edHeight) * 100) / 100));
    }
  } else {
    scale = Math.max(0.4, Math.min(2.5, parseFloat(val) || 1.0));
  }

  currentDocZoom = Math.round(scale * 100) / 100;
  const pctStr = `${Math.round(currentDocZoom * 100)}%`;
  document.documentElement.style.setProperty('--doc-zoom', currentDocZoom.toString());

  const zoomSelect = $('#zoomSelect');
  if (zoomSelect) {
    let found = false;
    for (let opt of zoomSelect.options) {
      if (Math.abs(parseFloat(opt.value) - currentDocZoom) < 0.02) {
        zoomSelect.value = opt.value;
        found = true;
        break;
      }
    }
    if (!found && typeof val === 'string' && (val === 'fit-width' || val === 'fit-page')) {
      zoomSelect.value = val;
    }
  }

  const zoomResetBtn = $('#zoomResetBtn');
  if (zoomResetBtn) zoomResetBtn.textContent = pctStr;

  const panelZoomSlider = $('#panelZoomSlider');
  if (panelZoomSlider) panelZoomSlider.value = Math.round(currentDocZoom * 100);

  const panelZoomReadout = $('#panelZoomReadout');
  if (panelZoomReadout) panelZoomReadout.textContent = pctStr;

  if (announce) showToast(`Document Zoom: ${pctStr}`);
}

$('#zoomSelect')?.addEventListener('change', e => {
  setDocZoom(e.target.value);
});

$('#zoomInBtn')?.addEventListener('click', () => {
  setDocZoom(currentDocZoom + 0.1);
});

$('#zoomOutBtn')?.addEventListener('click', () => {
  setDocZoom(currentDocZoom - 0.1);
});

$('#zoomResetBtn')?.addEventListener('click', () => {
  setDocZoom(1.0);
});

$('#panelZoomSlider')?.addEventListener('input', e => {
  const v = parseInt(e.target.value) / 100;
  setDocZoom(v, false);
});

$$('[data-zoom-btn]').forEach(btn => {
  btn.addEventListener('click', () => {
    const v = parseFloat(btn.getAttribute('data-zoom-btn')) || 1.0;
    setDocZoom(v);
  });
});

$('#canvasWidthModeSelect')?.addEventListener('change', e => {
  editor.classList.remove('canvas-page', 'canvas-fluid', 'canvas-wide', 'canvas-compact');
  editor.classList.add(e.target.value);
  showToast(`Canvas layout: ${e.target.options[e.target.selectedIndex].text}`);
});

$('#menuZoomIn')?.addEventListener('click', () => setDocZoom(currentDocZoom + 0.1));
$('#menuZoomOut')?.addEventListener('click', () => setDocZoom(currentDocZoom - 0.1));
$('#menuZoomReset')?.addEventListener('click', () => setDocZoom(1.0));
$('#menuFitWidth')?.addEventListener('click', () => setDocZoom('fit-width'));

// Ctrl + Wheel / Trackpad pinch to Zoom
$('#manuscriptEditorWrapper')?.addEventListener('wheel', e => {
  if (e.ctrlKey || e.metaKey) {
    e.preventDefault();
    const delta = e.deltaY < 0 ? 0.05 : -0.05;
    setDocZoom(currentDocZoom + delta, false);
  }
}, { passive: false });


/* ------------------------------------------------------------------ Overleaf LaTeX Suite */
let isLatexSplitOpen = false;

async function syncLatexSource() {
  const docClass = $('#latexDocClassSelect')?.value || 'article';
  const title = $('#docTitle')?.value || 'Manuscript';
  try {
    const res = await fetch('/api/latex/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text(), title: title, doc_class: docClass, author: 'Author' }),
    });
    if (res.ok) {
      const data = await res.json();
      const codeArea = $('#latexSourceCode');
      if (codeArea) codeArea.value = data.latex;
    }
  } catch (err) {
    console.error('LaTeX preview sync error:', err);
  }
}

function toggleLatexSplit() {
  isLatexSplitOpen = !isLatexSplitOpen;
  const pane = $('#latexSplitPane');
  if (isLatexSplitOpen) {
    pane?.classList.remove('hidden');
    syncLatexSource();
    $('#latexSplitBtn')?.classList.add('primary');
    showToast('Overleaf LaTeX Split View opened');
  } else {
    pane?.classList.add('hidden');
    $('#latexSplitBtn')?.classList.remove('primary');
    showToast('Overleaf LaTeX Split View closed');
  }
}

$('#latexSplitBtn')?.addEventListener('click', toggleLatexSplit);
$('#toggleSplitLatexBtn')?.addEventListener('click', toggleLatexSplit);
$('#compileLatexBtn')?.addEventListener('click', () => {
  syncLatexSource();
  showToast('LaTeX source compiled');
});

$('#exportTexBtn')?.addEventListener('click', () => {
  const code = $('#latexSourceCode')?.value;
  if (!code) return;
  const blob = new Blob([code], { type: 'text/x-tex;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${$('#docTitle').value || 'manuscript'}.tex`;
  a.click();
  showToast('Downloaded .tex file');
});

/* ------------------------------------------------------------------ Adobe PDF Manipulation Suite */

// PDF Merge
$('#openPdfMergeModalBtn')?.addEventListener('click', () => $('#modalPdfMerge')?.showModal());
$('#pdfMergeInput')?.addEventListener('change', e => {
  const files = e.target.files;
  const fileList = $('#pdfMergeFileList');
  const confirmBtn = $('#pdfMergeConfirm');
  if (files.length < 2) {
    fileList.textContent = 'Please select at least 2 PDF files.';
    confirmBtn.disabled = true;
  } else {
    fileList.textContent = `Selected ${files.length} PDFs: ` + Array.from(files).map(f => f.name).join(', ');
    confirmBtn.disabled = false;
  }
});

$('#pdfMergeConfirm')?.addEventListener('click', async () => {
  const input = $('#pdfMergeInput');
  if (!input.files || input.files.length < 2) return;
  const formData = new FormData();
  for (let i = 0; i < input.files.length; i++) {
    formData.append('files', input.files[i]);
  }
  showToast('Merging PDF files...');
  try {
    const res = await fetch('/api/pdf/merge', { method: 'POST', body: formData });
    if (!res.ok) throw new Error(await res.text());
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'merged_document.pdf';
    a.click();
    $('#modalPdfMerge')?.close();
    showToast('Merged PDF downloaded successfully');
  } catch (err) {
    alert('PDF Merge failed: ' + err.message);
  }
});

// PDF Split
$('#openPdfSplitModalBtn')?.addEventListener('click', () => $('#modalPdfSplit')?.showModal());
$('#pdfSplitInput')?.addEventListener('change', e => {
  const file = e.target.files[0];
  if (file) {
    $('#pdfSplitFileName').textContent = `Loaded: ${file.name}`;
    $('#pdfSplitConfirm').disabled = false;
  }
});

$('#pdfSplitConfirm')?.addEventListener('click', async () => {
  const file = $('#pdfSplitInput')?.files[0];
  const pages = $('#pdfSplitRangeInput')?.value.trim() || '1';
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  formData.append('pages', pages);
  showToast('Extracting PDF pages...');
  try {
    const res = await fetch('/api/pdf/split', { method: 'POST', body: formData });
    if (!res.ok) throw new Error(await res.text());
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `extracted_${file.name}`;
    a.click();
    $('#modalPdfSplit')?.close();
    showToast('Extracted pages downloaded');
  } catch (err) {
    alert('PDF Split failed: ' + err.message);
  }
});

// PDF Rotate
$('#openPdfRotateModalBtn')?.addEventListener('click', () => $('#modalPdfRotate')?.showModal());
$('#pdfRotateInput')?.addEventListener('change', e => {
  const file = e.target.files[0];
  if (file) {
    $('#pdfRotateFileName').textContent = `Loaded: ${file.name}`;
    $('#pdfRotateConfirm').disabled = false;
  }
});

$('#pdfRotateConfirm')?.addEventListener('click', async () => {
  const file = $('#pdfRotateInput')?.files[0];
  const angle = $('#pdfRotateAngleSelect')?.value || '90';
  const pages = $('#pdfRotatePagesInput')?.value.trim() || '';
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  formData.append('angle', angle);
  if (pages) formData.append('pages', pages);
  showToast('Rotating PDF pages...');
  try {
    const res = await fetch('/api/pdf/rotate', { method: 'POST', body: formData });
    if (!res.ok) throw new Error(await res.text());
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `rotated_${file.name}`;
    a.click();
    $('#modalPdfRotate')?.close();
    showToast('Rotated PDF downloaded');
  } catch (err) {
    alert('PDF Rotate failed: ' + err.message);
  }
});

// PDF Watermark
$('#openPdfWatermarkModalBtn')?.addEventListener('click', () => $('#modalPdfWatermark')?.showModal());
$('#pdfWatermarkInput')?.addEventListener('change', e => {
  const file = e.target.files[0];
  if (file) {
    $('#pdfWatermarkFileName').textContent = `Loaded: ${file.name}`;
    $('#pdfWatermarkConfirm').disabled = false;
  }
});

$('#pdfWatermarkConfirm')?.addEventListener('click', async () => {
  const file = $('#pdfWatermarkInput')?.files[0];
  const text = $('#pdfWatermarkTextInput')?.value || 'CONFIDENTIAL';
  const opacity = $('#pdfWatermarkOpacityInput')?.value || '0.25';
  const angle = $('#pdfWatermarkAngleInput')?.value || '45';
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  formData.append('text', text);
  formData.append('opacity', opacity);
  formData.append('angle', angle);
  showToast('Applying watermark to PDF...');
  try {
    const res = await fetch('/api/pdf/watermark', { method: 'POST', body: formData });
    if (!res.ok) throw new Error(await res.text());
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `watermarked_${file.name}`;
    a.click();
    $('#modalPdfWatermark')?.close();
    showToast('Watermarked PDF downloaded');
  } catch (err) {
    alert('PDF Watermark failed: ' + err.message);
  }
});

// PDF Redact
$('#openPdfRedactModalBtn')?.addEventListener('click', () => $('#modalPdfRedact')?.showModal());
$('#pdfRedactInput')?.addEventListener('change', e => {
  const file = e.target.files[0];
  if (file) {
    $('#pdfRedactFileName').textContent = `Loaded: ${file.name}`;
    $('#pdfRedactConfirm').disabled = false;
  }
});

$('#pdfRedactConfirm')?.addEventListener('click', async () => {
  const file = $('#pdfRedactInput')?.files[0];
  const terms = $('#pdfRedactTermsInput')?.value || '';
  if (!file || !terms.trim()) return;
  const formData = new FormData();
  formData.append('file', file);
  formData.append('terms', terms);
  showToast('Applying permanent redactions...');
  try {
    const res = await fetch('/api/pdf/redact', { method: 'POST', body: formData });
    if (!res.ok) throw new Error(await res.text());
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `redacted_${file.name}`;
    a.click();
    $('#modalPdfRedact')?.close();
    showToast('Redacted PDF downloaded');
  } catch (err) {
    alert('PDF Redact failed: ' + err.message);
  }
});

// PDF Semantic OCR Extraction
$('#openPdfExtractModalBtn')?.addEventListener('click', () => {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.pdf';
  input.onchange = async () => {
    const file = input.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append('file', file);
    showToast('Extracting semantic PDF content...');
    try {
      const res = await fetch('/api/pdf/extract', { method: 'POST', body: formData });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      const box = $('#pdfExtractResultBox');
      if (box) {
        box.classList.remove('hidden');
        box.innerHTML = `<strong>${escapeHTML(file.name)} (${data.page_count} pages)</strong><br><br><pre style="white-space:pre-wrap;font-size:11.5px;color:var(--text);">${escapeHTML(data.full_text.slice(0, 1500))}${data.full_text.length > 1500 ? '...' : ''}</pre>`;
      }
      showToast(`Extracted ${data.page_count} pages`);
    } catch (err) {
      alert('Extraction failed: ' + err.message);
    }
  };
  input.click();
});

// Update word counts hook to also update goal progress & analytics
const originalUpdateCounts = updateCounts;
updateCounts = function() {
  originalUpdateCounts();
  updateGoalProgress();
  if (isLatexSplitOpen) syncLatexSource();
};

/* ------------------------------------------------------------------ Auctor Writing Doctrine & Fact Ledger */
async function runDoctrineAudit() {
  const content = text();
  if (!content) return;
  const authority = $('#doctrineAuthoritySelect')?.value || 'substantive';
  showToast('Auditing Fact Ledger & Channels...');
  try {
    const res = await fetch('/api/auctor/doctrine-audit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ original_text: content, authority: authority }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    const ledger = data.fact_ledger;
    const channels = data.three_channel_status;

    $('#ledgerNumbersCount').textContent = ledger.numbers_count;
    $('#ledgerCiCount').textContent = ledger.confidence_intervals.length;
    $('#ledgerDirectionsCount').textContent = ledger.directions.length;
    $('#ledgerNegationsCount').textContent = ledger.negations.length;

    const label = $('#doctrineStatusLabel');
    const summary = $('#doctrineStatusSummary');
    if (channels.is_clean) {
      label.textContent = 'Doctrine Verified (100% Clean)';
      summary.textContent = `All ${ledger.numbers_count} facts frozen in ledger. Zero QC leakage detected in substantive channel.`;
      showToast('Fact Ledger & Channels verified');
    } else {
      label.textContent = 'Channel Leakage Detected';
      summary.textContent = channels.leaks.join('; ');
      showToast('Warning: QC language detected in manuscript body');
    }
  } catch (err) {
    console.error('Doctrine audit error:', err);
  }
}

$('#runDoctrineAuditBtn')?.addEventListener('click', runDoctrineAudit);

// ------------------------------------------------------------------ Writing Master (AIWD) Controller
async function runAiwdScan() {
  const content = text();
  if (!content) return;
  showToast('Running Writing Master Scan...');
  try {
    const res = await fetch('/api/aiwd/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: content, genre: 'academic' }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    const det = data.detectionResult || {};
    const prob = det.globalAiProbability ?? 0.5;
    const humanScore = Math.round((1 - prob) * 100);
    const label = det.decisionLabel || 'UNCERTAIN';

    const scoreLabel = $('#aiwdScoreLabel');
    const badge = $('#aiwdDecisionBadge');
    const summary = $('#aiwdScoreSummary');

    if (scoreLabel) scoreLabel.textContent = `${humanScore} / 100 (${label.replace('_', ' ')})`;
    if (badge) {
      badge.style.display = 'inline-block';
      badge.textContent = label;
      if (label === 'HUMAN_GENERATED') badge.className = 'badge badge-green';
      else if (label === 'AI_GENERATED') badge.className = 'badge badge-red';
      else badge.className = 'badge badge-yellow';
    }
    if (summary) {
      summary.textContent = `Global AI Probability: ${(prob * 100).toFixed(1)}% | Confidence: ${((det.globalConfidence || 0) * 100).toFixed(0)}% across ${data.tokenCount || 0} tokens.`;
    }

    // Family breakdown
    const famMap = {};
    (det.familyScores || []).forEach(f => { famMap[f.family] = f.familyAiScore ?? f.score; });
    const getFamScore = (keys) => {
      for (const k of keys) {
        if (famMap[k] !== undefined) return `${Math.round((1 - famMap[k]) * 100)}`;
      }
      return '-';
    };

    if ($('#aiwdFamLex')) $('#aiwdFamLex').textContent = getFamScore(['LexicalPatterns', 'Lexical']);
    if ($('#aiwdFamSyn')) $('#aiwdFamSyn').textContent = getFamScore(['SyntacticPatterns', 'Syntactic']);
    if ($('#aiwdFamStruct')) $('#aiwdFamStruct').textContent = getFamScore(['DiscoursePatterns', 'Structural', 'ProbabilisticFeatures']);
    if ($('#aiwdFamEpist')) $('#aiwdFamEpist').textContent = getFamScore(['EpistemicStance', 'Epistemic']);
    if ($('#aiwdFamPrag')) $('#aiwdFamPrag').textContent = getFamScore(['PragmaticDepth', 'Pragmatic']);
    if ($('#aiwdFamFormat')) $('#aiwdFamFormat').textContent = getFamScore(['FormattingPatterns', 'Formatting']);


    // Allow-list exemptions
    const exList = $('#aiwdExemptList');
    const exBadge = $('#aiwdExemptBadge');
    if (data.allowListExemptions && data.allowListExemptions.length > 0) {
      if (exBadge) exBadge.textContent = data.allowListExemptions.length;
      if (exList) {
        exList.className = 'finding-list';
        exList.innerHTML = data.allowListExemptions.map(ex => `
          <div class="finding-item" style="border-left:2px solid var(--accent, #4f8ff7);padding:6px 8px;margin-bottom:6px">
            <div><strong>${escapeHtml(ex.allowListEntry || ex.featureType)}</strong> (Excused ${ex.count}×)</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:2px">${escapeHtml(ex.reason || '')}</div>
            <div style="font-size:11px;font-style:italic;margin-top:2px">"${escapeHtml((ex.examples || []).join('", "'))}"</div>
          </div>
        `).join('');
      }
    } else {
      if (exBadge) exBadge.textContent = '0';
      if (exList) {
        exList.className = 'empty-state';
        exList.innerHTML = '<p>No domain allow-list exemptions needed for this document.</p>';
      }
    }

    // Reported Voice
    const voiceInfo = $('#aiwdReportedVoiceInfo');
    const frac = (data.reportedVoiceFraction || 0) * 100;
    if (voiceInfo) {
      if (frac > 0 || (data.reportedVoiceDiscounts && data.reportedVoiceDiscounts.length > 0)) {
        voiceInfo.className = 'finding-list';
        voiceInfo.innerHTML = `
          <div style="padding:6px 8px;margin-bottom:6px">
            <strong>${frac.toFixed(1)}% of document</strong> isolated as reported speech or quotations.
            ${(data.reportedVoiceDiscounts || []).map(d => `<div style="font-size:11px;margin-top:2px">Discounted: <em>"${escapeHtml((d.examples || []).join('", "'))}"</em></div>`).join('')}
          </div>
        `;
      } else {
        voiceInfo.className = 'empty-state';
        voiceInfo.innerHTML = '<p>Zero external quotations detected. 100% evaluated as authorial voice.</p>';
      }
    }

    // Anti-slop suggestions
    const suggList = $('#aiwdSuggList');
    const suggBadge = $('#aiwdSuggBadge');
    if (data.suggestions && data.suggestions.length > 0) {
      if (suggBadge) suggBadge.textContent = data.suggestions.length;
      if (suggList) {
        suggList.className = 'finding-list';
        suggList.innerHTML = data.suggestions.map(s => `
          <div class="finding-item" style="padding:8px;margin-bottom:6px;border-left:2px solid ${s.safe ? 'var(--green, #22c55e)' : 'var(--yellow, #eab308)'}">
            <div style="display:flex;justify-content:space-between;align-items:center">
              <strong>${escapeHtml(s.original)}</strong>
              <span class="badge ${s.safe ? 'badge-green' : 'badge-yellow'}">${s.safe ? 'Safe' : 'Review'}</span>
            </div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:2px">${escapeHtml(s.rationale)}</div>
            ${s.options && s.options.length ? `<div style="font-size:11px;margin-top:4px">Options: <strong>${escapeHtml(s.options.join(', '))}</strong></div>` : ''}
          </div>
        `).join('');
      }
    } else {
      if (suggBadge) suggBadge.textContent = '0';
      if (suggList) {
        suggList.className = 'empty-state';
        suggList.innerHTML = '<p>No stylistic slop or uncalibrated hedges detected. Prose is clean!</p>';
      }
    }

    showToast('Writing Master Scan Complete');
  } catch (err) {
    console.error('Writing Master scan error:', err);
    showToast('Error during Writing Master scan');
  }
}

async function applyAiwdClean() {
  const content = text();
  if (!content) return;
  showToast('Applying Conservative De-Slop...');
  try {
    const res = await fetch('/api/aiwd/clean', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: content }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    if (data.passes_preservation_gate) {
      setDocument(data.cleaned_text, currentFilename || 'Manuscript.txt');
      showToast(`Applied ${data.applied_count} safe de-slop edits (Preservation Gate 100% Passed)`);
      runAiwdScan();
    } else {
      showToast(`Preservation Gate Rejected: ${data.gate_violations.join(', ')}`);
    }
  } catch (err) {
    console.error('De-slop error:', err);
    showToast('Error applying de-slop');
  }
}

$('#runAiwdAuditBtn')?.addEventListener('click', runAiwdScan);
$('#applyAiwdCleanBtn')?.addEventListener('click', applyAiwdClean);

// ===================================================================
// SCIENTIFIC TABLES SUITE
// ===================================================================

let currentTableResult = null;

async function buildScientificTable() {
  const raw = $('#tableDataInput')?.value?.trim();
  if (!raw) {
    showToast('Please paste CSV or TSV tabular data first');
    return;
  }
  const caption = $('#tableCaptionInput')?.value?.trim() || '';
  const label = $('#tableLabelInput')?.value?.trim() || '';
  const notes = $('#tableNotesInput')?.value?.trim() || '';

  showToast('Building Three-Line Publication Table...');
  try {
    const res = await fetch('/api/toolkit/tables/format', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ raw_data: raw, caption, label, notes }),
    });
    if (!res.ok) throw new Error(await res.text());
    currentTableResult = await res.json();

    const container = $('#tablePreviewContainer');
    if (container) {
      container.innerHTML = currentTableResult.html;
    }
    showToast(`Built ${currentTableResult.row_count}×${currentTableResult.col_count} Publication Table`);
  } catch (err) {
    console.error('Table build error:', err);
    showToast('Error formatting scientific table');
  }
}

function insertTableIntoManuscript() {
  if (!currentTableResult || !currentTableResult.html) {
    showToast('Build a table first before inserting');
    return;
  }
  document.execCommand('insertHTML', false, `\n${currentTableResult.html}\n`);
  editor.focus();
  updateCounts();
  showToast('Inserted Three-Line Table into manuscript');
}

function copyLatexBooktabs() {
  if (!currentTableResult || !currentTableResult.latex) {
    showToast('Build a table first before copying LaTeX');
    return;
  }
  navigator.clipboard.writeText(currentTableResult.latex);
  showToast('Copied LaTeX booktabs code to clipboard');
}

function copyMarkdownTable() {
  if (!currentTableResult || !currentTableResult.markdown) {
    showToast('Build a table first before copying Markdown');
    return;
  }
  navigator.clipboard.writeText(currentTableResult.markdown);
  showToast('Copied Markdown table to clipboard');
}

$('#generateTableBtn')?.addEventListener('click', buildScientificTable);
$('#insertTableToDocBtn')?.addEventListener('click', insertTableIntoManuscript);
$('#copyLatexBooktabsBtn')?.addEventListener('click', copyLatexBooktabs);
$('#copyMarkdownTableBtn')?.addEventListener('click', copyMarkdownTable);

// Initialize Components
renderComments();
renderCitations();
renderHistory();
setTimeout(updateOutline, 300);
setTimeout(updateAnalytics, 300);
setTimeout(updateGoalProgress, 300);







