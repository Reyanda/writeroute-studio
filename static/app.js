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

function resetReports(){ state.lastAudit=null; state.lastFormatting=null; state.candidate=null; $('#scoreValue').textContent='—';$('#scoreLabel').textContent='Not analysed';$('#scoreSummary').textContent='Run an audit to inspect the current document.';$('#hardCount').textContent='0';$('#reviewCount').textContent='0';$('#softCount').textContent='0';$('#findingList').innerHTML='<p>No findings yet.</p>';$('#findingList').classList.add('empty-state');$('#formatMetrics').innerHTML='';$('#formatAdvice').innerHTML='<div class="advice"><span>01</span><p>Run an audit to generate formatting recommendations.</p></div>';$('#comparison').classList.add('hidden');$('#suggestionList').innerHTML=''; }

async function runAudit(){ const t=text();if(!t){showToast('Add some text first');return}
 if(!$('#genreSelect').value){showToast('Choose the document job first — it sets the severity thresholds');$('#genreSelect').focus();return}
 const btn=$('#auditButton');btn.classList.add('loading');btn.textContent='Auditing…'; try{const d=await postJSON('/api/audit',{text:t,genre:$('#genreSelect').value});renderAudit(d.audit,d.formatting);showToast('Audit complete')}catch(e){showToast(e.message)}finally{btn.classList.remove('loading');btn.textContent='Run audit'} }
$('#auditButton').onclick=runAudit; $('#genreSelect').onchange=()=>{if(text()&&$('#genreSelect').value)runAudit()};

function renderAudit(audit, formatting){ state.lastAudit=audit;state.lastFormatting=formatting; const burden=Number(audit.editorialBurden||0), health=Math.max(0,Math.round(100-burden)); $('#scoreValue').textContent=health; $('#scoreLabel').textContent=audit.status==='clean'?'Clean route':audit.status==='review'?'Review recommended':'Revision needed'; $('#scoreSummary').textContent=`${audit.counts.findings} finding${audit.counts.findings===1?'':'s'} across ${audit.counts.words.toLocaleString()} words. No authorship inference.`; $('#hardCount').textContent=audit.counts.hard;$('#reviewCount').textContent=audit.counts.review;$('#softCount').textContent=audit.counts.soft; const deg=health*3.6; const color=health>=85?'var(--green)':health>=65?'var(--amber)':'var(--red)';$('#scoreRing').style.background=`conic-gradient(${color} 0deg,${color} ${deg}deg,var(--line) ${deg}deg)`;
 const list=$('#findingList'); list.classList.toggle('empty-state',!audit.findings.length); list.innerHTML=audit.findings.length?'': '<p>No editorial defect crossed the configured threshold.</p>';
 audit.findings.forEach((f,i)=>{ const el=document.createElement('div');el.className='finding';el.innerHTML=`<div class="finding-top"><h4>${escapeHTML(f.title)}</h4><span class="badge">${escapeHTML(f.severity)}</span></div><q>${escapeHTML(shorten(f.original,130))}</q><p>${escapeHTML(f.rationale)}</p>`;el.onclick=()=>showToast(`${f.layer || 'Editorial'} · ${f.action}`);list.appendChild(el)}); renderFormatting(formatting); }

function renderFormatting(f){ if(!f)return; $('#formatIntro').textContent=`${f.label}. Advice is adapted to this document job.`; const m=f.metrics;$('#formatMetrics').innerHTML=`<div><strong>${m.paragraphs}</strong><span>Paragraphs</span></div><div><strong>${m.longSentences}</strong><span>Long sentences</span></div><div><strong>${m.headingsDetected}</strong><span>Headings</span></div>`; const advice=[...f.diagnostics,...f.recommendations];$('#formatAdvice').innerHTML=advice.map((x,i)=>`<div class="advice"><span>${String(i+1).padStart(2,'0')}</span><p>${escapeHTML(x)}</p></div>`).join(''); }

$('#repairSafe').onclick=async()=>{const t=text();if(!t)return;try{const d=await postJSON('/api/repair',{text:t,genre:$('#genreSelect').value}); if(!d.changed){showToast('No safe deterministic repair was available');return} state.candidate=d.finalText;renderCandidate(d.finalText,d.auditBefore?.editorialBurden,d.auditAfter?.editorialBurden);panel('suggestions');showToast('Safe repair prepared for review')}catch(e){showToast(e.message)}};

$('#suggestButton').onclick=async()=>{const t=text();if(!t){showToast('Add some text first');return}const b=$('#suggestButton');b.classList.add('loading');b.textContent='Analysing…';try{const d=await postJSON('/api/suggest',{text:t,genre:$('#genreSelect').value,max_candidates:3});renderSuggestions(d);panel('suggestions')}catch(e){showToast(e.message)}finally{b.classList.remove('loading');b.textContent='Generate local suggestions'}};
function renderSuggestions(d){const box=$('#suggestionList');box.innerHTML=''; if(!d.findings?.length){box.innerHTML='<div class="empty-state"><p>No local suggestion is needed.</p></div>';return} d.findings.forEach(f=>{const c=(f.candidates||[])[0];if(!c)return;const el=document.createElement('div');el.className='suggestion';el.innerHTML=`<h4>${escapeHTML(f.title)}</h4><div class="before">${escapeHTML(shorten(f.original,160))}</div><div class="arrow">↓</div><div class="after">${escapeHTML(c.preview||c.replacement||'Needs author input')}</div>${c.safeToApply?'<button class="button secondary compact">Apply this edit</button>':''}`;const btn=$('button',el);if(btn)btn.onclick=()=>applySpan(f.span,c.replacement||c.preview);box.appendChild(el)})}
function applySpan(span,replacement){const t=text();if(!span||replacement==null)return; const next=t.slice(0,span.start)+replacement+t.slice(span.end);editor.textContent=next;updateCounts();showToast('Suggestion applied');runAudit()}

$('#providerSelect').onchange=()=>{$('#baseUrlLabel').classList.toggle('hidden',$('#providerSelect').value!=='openai-compatible'); $('#modelName').placeholder='Enter provider model ID'};
$('#keyVisibility').onclick=()=>{const i=$('#apiKey');i.type=i.type==='password'?'text':'password';$('#keyVisibility').textContent=i.type==='password'?'Show':'Hide'};
$('#testProvider').onclick=()=>{if(!$('#apiKey').value)return showToast('Enter your API key');if(!$('#modelName').value)return showToast('Enter a model ID');$('#providerStatus').textContent=`${$('#providerSelect option:checked').textContent} configured for this browser tab. Key is not persisted.`;showToast('Provider configuration is ready')};

$('#rewriteButton').onclick=async()=>{
 const t=text(); if(!t)return showToast('Add some text first');
 if(!$('#apiKey').value){panel('provider');showToast('Add your API key to use generative rewrite');return}
 if(!$('#modelName').value){panel('provider');showToast('Enter the provider model ID');return}
 const b=$('#rewriteButton');b.classList.add('loading');b.textContent='Routing candidates…';
 try{
  const d=await WriteRoute.rewrite({
   text:t, genre:$('#genreSelect').value,
   provider:$('#providerSelect').value, apiKey:$('#apiKey').value,
   model:$('#modelName').value, baseUrl:$('#baseUrl').value,
   candidates:Number($('#candidateCount').value), temperature:Number($('#temperature').value),
  });
  if(d.providerErrors?.length) showToast(`Provider: ${d.providerErrors[0]}`);
  if(!d.changed){
   showToast(d.reason||'No candidate cleared the safety gates');
   if(d.auditBefore)renderAudit(d.auditBefore,state.lastFormatting);
   return;
  }
  state.candidate=d.finalText;
  renderCandidate(d.finalText,d.auditBefore?.editorialBurden,d.auditAfter?.editorialBurden);
  showToast('A rewrite cleared the preservation gates');
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
