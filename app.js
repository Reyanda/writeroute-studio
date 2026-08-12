import WriteRoute from './engine.js';
import { extract, exportDocument, download } from './files.js';

const $ = (q, root=document) => root.querySelector(q);
const $$ = (q, root=document) => [...root.querySelectorAll(q)];
const state = { sourceText:'', lastAudit:null, lastFormatting:null, candidate:null, filename:'Untitled document', dirty:false };
const editor=$('#editor'), workspace=$('#workspace'), hero=$('#hero'), toast=$('#toast');

function showToast(message){ toast.textContent=message; toast.classList.add('show'); clearTimeout(showToast.t); showToast.t=setTimeout(()=>toast.classList.remove('show'),2600); }
function text(){ return editor.innerText.replace(/\u00a0/g,' ').trim(); }
function updateCounts(){ const n=(text().match(/\b[\w’'-]+\b/g)||[]).length; $('#wordCount').textContent=`${n.toLocaleString()} words`; state.dirty=true; $('#saveState').textContent='Edited locally'; }
function openWorkspace(){ hero.classList.add('hidden'); workspace.classList.remove('hidden'); setTimeout(()=>editor.focus(),50); WriteRoute.ensure().catch(e=>showToast(`Engine failed to start: ${e.message}`)); }
function setDocument(content, name='Untitled document'){ openWorkspace(); state.sourceText=content; state.filename=name.replace(/\.[^.]+$/,'') || 'Untitled document'; $('#docTitle').value=state.filename; editor.textContent=content; state.dirty=false; $('#saveState').textContent='Loaded'; updateCounts(); state.dirty=false; $('#saveState').textContent='Ready'; }

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

$('#fileInput').onchange=async e=>{
 const file=e.target.files[0]; if(!file)return;
 showToast('Reading document…');
 try{
  const {text:content, sourceFormat}=await extract(file);
  setDocument(content, file.name);
  const d=await postJSON('audit',{text:content,genre:$('#genreSelect').value});
  renderAudit(d.audit,d.formatting);
  showToast(`${sourceFormat.toUpperCase()} loaded and audited`);
 }
 catch(err){showToast(err.message)} finally{e.target.value=''}
};

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
