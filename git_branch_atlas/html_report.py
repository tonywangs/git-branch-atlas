"""Self-contained, bounded HTML view of the unchanged series-v1 report."""
from __future__ import annotations

import base64
import hashlib
import json

from .git import GitError

STYLE = r'''
:root {color-scheme:light; font:16px/1.5 system-ui,sans-serif; color:#172d38; background:#f3f5f3}
* {box-sizing:border-box} body {margin:0} header,main,footer {max-width:1200px;margin:auto;padding:24px}
header {border-bottom:3px solid #167361} h1 {font-size:2rem;margin:4px 0} h2 {font-size:1.2rem} h3 {font-size:1rem}
p {margin:8px 0} .eyebrow {letter-spacing:.15em;font-size:.8rem;font-weight:700;color:#176653}
.notice {background:#fff3ce;border-left:4px solid #846018;padding:12px;margin:12px 0;overflow-wrap:anywhere}
.endpoints {display:grid;grid-template-columns:1fr 1fr;gap:16px} .endpoint, article, #detail {background:white;border:1px solid #bacbc4;border-radius:8px;padding:16px;min-width:0;overflow-wrap:anywhere}
.toolbar {display:flex;flex-wrap:wrap;gap:12px;align-items:end;margin:20px 0} label {display:flex;flex-direction:column;gap:4px;font-size:.875rem;font-weight:600}
input,select,button {font:inherit;max-width:100%;padding:8px;border:1px solid #718a80;border-radius:4px;background:white;color:#172d38}
button {cursor:pointer} button:hover {background:#e5f2ec} button:disabled {opacity:.5;cursor:default}
:focus-visible {outline:3px solid #005cc5;outline-offset:3px} .layout {display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:24px;align-items:start}
#cards {display:grid;gap:8px} article {padding:12px} article[aria-current=true] {border:2px solid #167361;background:#edf7f1}
.card-open {text-align:left;width:100%;border:0;padding:0;background:transparent} .status {font-weight:700} code,pre,.mono {font-family:ui-monospace,monospace;font-size:.85rem;overflow-wrap:anywhere}
pre {white-space:pre-wrap} .candidate {border-top:1px solid #ccd7d1;padding:12px 0} .candidate button {margin:4px 4px 4px 0}
#page-info,#selection-info {font-variant-numeric:tabular-nums} summary {cursor:pointer;font-weight:600;padding:8px 0}
.evidence-line {display:block;white-space:pre-wrap;overflow-wrap:anywhere;font-family:ui-monospace,monospace;font-size:.85rem}
footer {font-size:.85rem;color:#455e55} .skip {position:absolute;left:-9999px} .skip:focus {left:8px;top:8px;background:white;padding:12px}
@media(max-width:700px) {.layout,.endpoints {grid-template-columns:1fr} header,main,footer {padding:16px} h1 {font-size:1.6rem}}
'''

SCRIPT = r'''
'use strict';
const report = JSON.parse(document.getElementById('report-data').textContent);
const $ = id => document.getElementById(id);
// Controls and bidi formatting are displayed literally, never used as markup.
function visible(value) {return String(value).replace(/[\u0000-\u001f\u007f-\u009f\u2028-\u202e\u2066-\u2069]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));}
function el(tag,text,parent,cls) {const n=document.createElement(tag); if(text!==undefined)n.textContent=visible(text); if(cls)n.className=cls; if(parent)parent.append(n); return n;}
function button(text,parent,action) {const b=el('button',text,parent);b.type='button';b.addEventListener('click',action);return b;}
const entries=[];
for(const side of ['left','right']) report[side].commits.forEach((commit,i)=>entries.push({side,commit,order:i+1,key:side+':'+commit.oid}));
const byKey=new Map(entries.map(e=>[e.key,e]));
const groups=new Map();
report.matches.forEach((g,i)=>{for(const side of ['left','right']) for(const oid of g[side])groups.set(side+':'+oid,i);});
let page=0, selected=null, filtered=[];
const PAGE_SIZE=100;
$('completeness').textContent=report.complete?'Complete within supported scope':'INCOMPLETE — rankings provisional; groups may have unobserved members';
$('notice').textContent=visible(report.notice);
for(const warning of report.warnings)el('p','Warning: '+warning,$('warnings'));
const omitted=entries.reduce((n,e)=>n+(e.commit.candidates_omitted||0),0);
let omittedItems=0, truncated=0;
for(const e of entries)for(const c of e.commit.candidates||[])for(const k of ['source_only','counterpart_only','paths_source_only','paths_counterpart_only']) {
 const s=c.evidence[k];omittedItems+=s.omitted_items;truncated+=s.items.filter(i=>i.truncated).length;
}
$('bounds').textContent=`Evidence is a bounded normalized feature difference, not an applicable patch. ${omitted} candidate entries omitted; ${omittedItems} evidence keys omitted; ${truncated} evidence items truncated (counts include both directions). One candidate's evidence is mounted at a time. Filters never remove these notices.`;
$('metrics').textContent=`${entries.length} observed commits · ${report.matches.length} exact groups · ${report.comparison_count} comparisons · threshold ${report.threshold}/${report.score_scale}`;
for(const side of ['left','right']) {
 const box=el('section',undefined,$('endpoints'),'endpoint'); el('h2',side==='left'?'Left · original series':'Right · revised series',box);
 for(const endpoint of ['base','tip']) {el('p',endpoint+': '+report[side][endpoint].revision,box);el('code',report[side][endpoint].oid,box);}
 el('p','Enumeration '+(report[side].enumeration_complete?'complete':'INCOMPLETE')+' · '+report[side].commits.length+' commits',box);
}
el('pre',JSON.stringify({algorithm:report.algorithm,limits:report.limits,inspection_bytes_read:report.inspection_bytes_read},null,2),$('metadata'));
for(const status of [...new Set(entries.map(e=>e.commit.status))].sort()) {const o=el('option',status+' ('+entries.filter(e=>e.commit.status===status).length+')',$('status'));o.value=status;}
report.matches.forEach((g,i)=>{const o=el('option',`Group ${i+1}: ${g.left.length} left / ${g.right.length} right`,$('group'));o.value=String(i);});
const searchText=new Map(entries.map(e=>[e.key,visible(JSON.stringify(e)).toLowerCase()]));
function apply() {
 const query=$('search').value.toLowerCase();
 filtered=entries.filter(e=>(!$('status').value||e.commit.status===$('status').value)&&(!$('side').value||e.side===$('side').value)&&(!$('group').value||groups.get(e.key)===Number($('group').value))&&searchText.get(e.key).includes(query));
 page=0;renderCards();
}
function renderCards() {
 $('cards').replaceChildren();
 const start=page*PAGE_SIZE;
 for(const e of filtered.slice(start,start+PAGE_SIZE)) {
  const card=el('article',undefined,$('cards'));card.dataset.key=e.key;card.setAttribute('aria-current',String(e.key===selected));
  const b=button(`${e.side} #${e.order} · ${e.commit.oid.slice(0,12)}` ,card,()=>select(e.key,true));b.className='card-open';
  el('p',e.commit.status,card,'status');
  if(e.commit.search_complete===false)el('p','INCOMPLETE search — provisional',card);
  if(e.commit.ambiguous)el('p','Ambiguous: '+e.commit.candidate_count+' observed candidates',card);
  if(e.commit.reason)el('p',e.commit.reason,card);
 }
 $('page-info').textContent=filtered.length?`${start+1}–${Math.min(start+PAGE_SIZE,filtered.length)} of ${filtered.length} commits`:'No commits match these filters';
 $('prev-page').disabled=page===0;$('next-page').disabled=start+PAGE_SIZE>=filtered.length;
}
function jump(key) {
 $('search').value='';$('status').value='';$('side').value='';$('group').value='';apply();
 const index=filtered.findIndex(e=>e.key===key);page=Math.floor(index/PAGE_SIZE);select(key,true);
}
function select(key,focus=false) {
 selected=key;renderCards();const e=byKey.get(key),c=e.commit, box=$('detail');box.replaceChildren();box.dataset.key=key;
 el('h2',`${e.side} #${e.order} · ${c.status}`,box);el('p',c.oid,box,'mono');
 el('p','Parents: '+(c.parents.join(', ')||'(root)'),box,'mono');
 if(c.patch_id)el('p','Patch ID: '+c.patch_id,box,'mono');
 if(c.reason)el('p','Reason: '+c.reason,box);
 if(c.search_complete!==undefined)el('p',c.search_complete?'Candidate search complete':'INCOMPLETE candidate search — all ranks provisional',box,'notice');
 if(c.candidates)el('p',`${c.ambiguous?'Ambiguous':'Heuristic only'} · ${c.candidate_count} observed candidates · ${c.top_tie_count} tied at best score · ${c.candidates_omitted} omitted`,box);
 const gi=groups.get(key);
 if(gi!==undefined) {
  const g=report.matches[gi];el('p',`Exact group ${gi+1}: ${g.left.length} left / ${g.right.length} right. All members retained; no unique pairing implied.`,box,'notice');
  button('Browse all group members',box,()=>{ $('search').value='';$('status').value='';$('side').value='';$('group').value=String(gi);apply();$('list-heading').focus();});
 }
 for(const candidate of c.candidates||[]) {
  const row=el('section',undefined,box,'candidate');row.dataset.oid=candidate.oid;row.dataset.score=String(candidate.score);row.dataset.rank=String(candidate.rank);
  el('h3',`Rank ${candidate.rank} · score ${candidate.score}/${report.score_scale} (not probability)`,row);
  el('p',candidate.oid,row,'mono');
  button('Go to counterpart',row,()=>jump((e.side==='left'?'right':'left')+':'+candidate.oid));
  const details=el('details',undefined,row);el('summary','Inspect bounded evidence',details);
  details.addEventListener('toggle',()=>{
   if(!details.open){const old=details.querySelector('.evidence');if(old)old.remove();return;}
   for(const other of box.querySelectorAll('details'))if(other!==details){other.open=false;const old=other.querySelector('.evidence');if(old)old.remove();}
   if(details.querySelector('.evidence'))return;
   const body=el('div',undefined,details,'evidence');
   el('p',candidate.evidence.kind+' — source is selected commit',body);
   for(const category of ['source_only','counterpart_only','paths_source_only','paths_counterpart_only']) {
    const sample=candidate.evidence[category];el('h3',category,body);
    for(const item of sample.items)el('span',`${item.count} × ${item.text} [${item.bytes} bytes${item.truncated?'; TRUNCATED':''}]`,body,'evidence-line');
    el('p',`${sample.items.length} shown; ${sample.omitted_items} distinct keys omitted`,body);
   }
  });
 }
 $('selection-info').textContent=`Selected ${e.side} commit ${e.order}. Navigation follows full series order and clears filters.`;
 if(focus){box.focus();box.scrollIntoView({block:'nearest'});}
}
for(const id of ['status','side','group'])$(id).addEventListener('change',apply);
$('search').addEventListener('input',apply);
$('prev-page').addEventListener('click',()=>{page--;renderCards();$('list-heading').focus();});
$('next-page').addEventListener('click',()=>{page++;renderCards();$('list-heading').focus();});
function step(delta) {if(!entries.length)return;let i=entries.findIndex(e=>e.key===selected);jump(entries[Math.max(0,Math.min(entries.length-1,i+delta))].key);}
$('prev-commit').addEventListener('click',()=>step(-1));$('next-commit').addEventListener('click',()=>step(1));
apply();if(entries.length)select(entries[0].key);
'''

TEMPLATE = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'sha256-@HASH@'; style-src 'sha256-@STYLEHASH@'; base-uri 'none'; form-action 'none'">
<title>Git Branch Atlas · Series review</title><style>@STYLE@</style></head><body>
<a class="skip" href="#controls">Skip to review controls</a>
<header><p class="eyebrow">GIT BRANCH ATLAS / LOCAL REVIEW</p><h1>Two series. Every possibility.</h1><p id="metrics"></p>
<div class="notice"><strong id="completeness"></strong><p id="notice"></p><div id="warnings"></div><p id="bounds"></p></div>
<div id="endpoints" class="endpoints"></div><details id="metadata"><summary>Algorithm and inspection limits</summary></details></header>
<main><noscript>This report requires JavaScript. Use the CLI --json or text output when scripts are disabled.</noscript>
<div id="controls" class="toolbar"><label>Search commit data<input id="search" type="search" placeholder="OID, status, path or evidence"></label>
<label>Status<select id="status"><option value="">All statuses</option></select></label>
<label>Series<select id="side"><option value="">Both sides</option><option value="left">Left</option><option value="right">Right</option></select></label>
<label>Exact group<select id="group"><option value="">All groups and ungrouped</option></select></label></div>
<div class="layout"><section aria-labelledby="list-heading"><h2 id="list-heading" tabindex="-1">Ordered commits</h2>
<div class="toolbar"><button id="prev-page" type="button">Previous page</button><span id="page-info" role="status"></span><button id="next-page" type="button">Next page</button></div><div id="cards"></div></section>
<section aria-label="Commit inspection"><div class="toolbar"><button id="prev-commit" type="button">Previous commit</button><button id="next-commit" type="button">Next commit</button></div><p id="selection-info" role="status"></p><div id="detail" tabindex="-1"><p>No commits to inspect.</p></div></section></div></main>
<footer>Private by content: this file embeds repository identifiers, paths and normalized patch evidence. Review before sharing. No server or network required. Messages are not collected by series-v1. Search covers embedded commit fields; each page contains at most 100 commit cards.</footer>
<script id="report-data" type="application/json">@DATA@</script><script>@SCRIPT@</script></body></html>'''


def format_series_html(report: dict, max_output: int = 2 * 1024 * 1024) -> str:
    """Serialize once; never drop data to fit the artifact budget."""
    if not 1024 <= max_output <= 16 * 1024 * 1024:
        raise GitError('output byte limit must be 1024..16777216')
    data = json.dumps(report, ensure_ascii=True, separators=(',', ':')).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    digest = lambda value: base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()
    # Substitute data last so untrusted values cannot become template directives.
    output = TEMPLATE.replace('@HASH@', digest(SCRIPT)).replace('@STYLEHASH@', digest(STYLE)).replace('@STYLE@', STYLE).replace('@SCRIPT@', SCRIPT).replace('@DATA@', data)
    if len((output + '\n').encode('utf-8')) > max_output:
        raise GitError('output byte limit; no HTML emitted (increase --max-output-bytes)')
    return output
