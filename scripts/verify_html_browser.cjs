/* Offline browser parity and keyboard playthrough. NODE_PATH may locate Playwright. */
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const assert = require('node:assert/strict');
const visible = s => String(s).replace(/[\u0000-\u001f\u007f-\u009f\u2028-\u202e\u2066-\u2069]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--disable-background-networking']});
 const context=await browser.newContext({offline:true,viewport:{width:1200,height:900}});
 const requests=[],errors=[];
 await context.route('**/*',route=>{if(!route.request().url().startsWith('file:')){requests.push(route.request().url());return route.abort();}return route.continue();});
 await context.addInitScript(()=>{
  globalThis.atlasPeaks={cards:0,groupRows:0,evidence:0,nodes:0};
  new MutationObserver(()=>{
   atlasPeaks.groupRows=Math.max(atlasPeaks.groupRows,document.querySelectorAll('#group-rows .group-row').length);
   atlasPeaks.cards=Math.max(atlasPeaks.cards,document.querySelectorAll('#cards article').length);
   atlasPeaks.evidence=Math.max(atlasPeaks.evidence,document.querySelectorAll('.evidence-line').length);
   atlasPeaks.nodes=Math.max(atlasPeaks.nodes,document.querySelectorAll('*').length);
  }).observe(document,{childList:true,subtree:true});
 });
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 const measurements=[];
 for(const name of process.argv.slice(3)) {
  const file=path.join(process.argv[2],name+'.html');
  const report=JSON.parse(fs.readFileSync(path.join(process.argv[2],name+'.json'),'utf8'));
  const start=performance.now();await page.goto(pathToFileURL(file).href);await page.locator('#page-info').waitFor();
  await page.waitForFunction(()=>document.querySelector('#page-info').textContent.length>0);
  const load=performance.now()-start;
  assert.deepEqual(await page.locator('#report-data').evaluate(n=>JSON.parse(n.textContent)),report);
  assert.match(await page.locator('#completeness').innerText(),report.complete?/Complete within/:/INCOMPLETE/);
  for(const side of ['left','right'])for(const endpoint of ['base','tip'])assert.ok((await page.locator('#endpoints').innerText()).includes(visible(report[side][endpoint].oid)));
  const seen=[];
  do {
   const cards=await page.locator('#cards article').evaluateAll(nodes=>nodes.map(n=>({key:n.dataset.key,text:n.textContent})));
   assert.ok(cards.length<=100);seen.push(...cards);
   if(await page.locator('#next-page').isDisabled())break;
   await page.locator('#next-page').click();
  }while(true);
  const expected=['left','right'].flatMap(side=>report[side].commits.map(c=>({side,c})));
  assert.deepEqual(seen.map(x=>x.key),expected.map(({side,c})=>side+':'+c.oid));
  for(let i=0;i<seen.length;i++)assert.ok(seen[i].text.includes(expected[i].c.status));
  // Large workloads are measured and paginated; small cases inspect every commit.
  for(const {side,c} of expected.length>40?expected.filter(({c})=>c.candidates?.length).slice(0,2):expected) {
   await page.locator('#search').fill(c.oid);await page.locator('#side').selectOption(side);
   await page.locator('.card-open').first().click();
   const text=await page.locator('#detail').innerText();assert.ok(text.includes(c.status));assert.ok(text.includes(c.oid));
   if(c.reason)assert.ok(text.includes(visible(c.reason)));
   if(c.ambiguous)assert.ok(text.includes('Ambiguous'));
   if(c.candidates){assert.ok(text.includes(`${c.candidate_count} observed candidates`));assert.ok(text.includes(`${c.candidates_omitted} omitted`));assert.ok(text.includes(`${c.top_tie_count} tied at best score`));}
   if(c.search_complete===false)assert.ok(text.includes('INCOMPLETE'));
   const group=report.matches.find(g=>g[side].includes(c.oid));
   if(group){
    assert.ok(text.includes(`${group.left.length} left / ${group.right.length} right`));
    await page.getByRole('button',{name:'Browse all group members'}).click();
    const keys=await page.locator('#cards article').evaluateAll(ns=>ns.map(n=>n.dataset.key));
    assert.deepEqual(keys,['left','right'].flatMap(s=>group[s].map(o=>s+':'+o)).slice(0,100));
    await page.locator('#group').selectOption('');
   }
   const rows=page.locator('.candidate');assert.equal(await rows.count(),(c.candidates||[]).length);
   for(let i=0;i<(c.candidates||[]).length;i++) {
    const candidate=c.candidates[i],row=rows.nth(i);
    assert.equal(await row.getAttribute('data-oid'),candidate.oid);
    assert.equal(await row.getAttribute('data-score'),String(candidate.score));
    assert.equal(await row.getAttribute('data-rank'),String(candidate.rank));
    await row.locator('summary').focus();await page.keyboard.press('Enter');
    await row.locator('.evidence').waitFor();
    const lines=await row.locator('.evidence-line').allTextContents();
    const wanted=[];
    for(const category of ['source_only','counterpart_only','paths_source_only','paths_counterpart_only']) {
     const sample=candidate.evidence[category];
     for(const item of sample.items)wanted.push(visible(`${item.count} × ${item.text} [${item.bytes} bytes${item.truncated?'; TRUNCATED':''}]`));
     assert.ok((await row.innerText()).includes(`${sample.items.length} shown; ${sample.omitted_items} distinct keys omitted`));
    }
    assert.deepEqual(lines,wanted);assert.ok(await page.locator('.evidence-line').count()<=32);
   }
  }
  if(report.group_comparison) {
   const gc=report.group_comparison;
   assert.ok(await page.locator('#group-review').isVisible());
   const bounds=await page.locator('#group-bounds').innerText();
   for(const value of [`${gc.windows_omitted} windows omitted`,`${gc.comparison_count}/${gc.comparisons_possible} eligible comparisons`,`${gc.candidates_omitted} omitted`])assert.ok(bounds.includes(value));
   if(!gc.complete)assert.ok(bounds.includes('INCOMPLETE'));
   const observed=[];
   do {
    observed.push(...await page.locator('#group-rows .group-row').evaluateAll(ns=>ns.map(n=>Number(n.dataset.candidateIndex))));
    assert.ok(await page.locator('#group-rows .group-row').count()<=20);
    if(await page.locator('#group-next').isDisabled())break;
    await page.locator('#group-next').click();
   }while(true);
   assert.deepEqual(observed,gc.candidates.map((c,i)=>i));
   const inspect=expected.length>40?gc.candidates.slice(0,2):gc.candidates;
   for(const c of inspect) {
    // Searching the singleton can leave several candidates; identify by index.
    const i=gc.candidates.indexOf(c),g=gc.groups.find(g=>g.id===c.group_id);
    await page.locator('#group-search').fill(c.single_oid);
    while(!await page.locator(`#group-rows [data-candidate-index="${i}"]`).count())await page.locator('#group-next').click();
    const b=page.locator(`#group-rows [data-candidate-index="${i}"] button`);
    await b.focus();await page.keyboard.press('Enter');
    const box=page.locator('#group-detail');
    assert.equal(await box.evaluate(n=>document.activeElement===n),true);
    assert.equal(await box.getAttribute('data-group-id'),g.id);
    const t=await box.innerText();
    assert.ok(t.includes(`score ${c.score}/${report.score_scale}`));
    assert.ok(t.includes(`normalized patch agreement: ${c.normalized_patch_agreement}`));
    if(c.ambiguous)assert.ok(t.includes('Ambiguous'));
    const single=gc.singles.find(s=>s.side===c.single_side&&s.oid===c.single_oid);
    for(const value of [single.base_oid,single.tip_oid,g.base_oid,g.tip_oid])assert.ok(t.includes(value));
    assert.deepEqual(await box.locator('ol').nth(1).locator('li').allTextContents(),g.members.map(o=>g.side+' '+o));
    await box.locator('summary').focus();await page.keyboard.press('Enter');await box.locator('.evidence').waitFor();
    const wanted=[];
    for(const category of ['source_only','counterpart_only','paths_source_only','paths_counterpart_only']) {
     const sample=c.evidence[category];
     for(const item of sample.items)wanted.push(visible(`${item.count} × ${item.text} [${item.bytes} bytes${item.truncated?'; TRUNCATED':''}]`));
     assert.ok((await box.innerText()).includes(`${sample.items.length} shown; ${sample.omitted_items} distinct keys omitted`));
    }
    assert.deepEqual(await box.locator('.evidence-line').allTextContents(),wanted);
    assert.ok(await page.locator('.evidence-line').count()<=32);
    await box.locator('ol').nth(1).locator('button').first().focus();await page.keyboard.press('Enter');
    assert.equal(await page.locator('#detail').getAttribute('data-key'),g.side+':'+g.members[0]);
   }
   await page.locator('#group-search').fill('');await page.locator('#group-view').selectOption('groups');
   const ids=[];
   do {
    ids.push(...await page.locator('#group-rows .group-row').evaluateAll(ns=>ns.map(n=>n.dataset.groupId)));
    if(await page.locator('#group-next').isDisabled())break;
    await page.locator('#group-next').click();
   }while(true);
   assert.deepEqual(ids,gc.groups.map(g=>g.id));
   if(expected.length<=40)for(const g of gc.groups) {
    await page.locator('#group-search').fill(g.id);
    await page.locator('#group-rows button').first().click();
    const text=await page.locator('#group-detail').innerText();
    assert.ok(text.includes(g.status));if(g.reason)assert.ok(text.includes(g.reason));
   }
   await page.locator('#group-search').fill('nothing-matches-00000');
   assert.equal(await page.locator('#group-rows .group-row').count(),0);
   assert.ok(await page.locator('#group-bounds').isVisible());
   await page.locator('#group-search').fill('');
  } else assert.equal(await page.locator('#group-review').isVisible(),false);
  if(name==='review' && process.env.ATLAS_SCREENSHOT)await page.screenshot({path:process.env.ATLAS_SCREENSHOT,fullPage:true});
  await page.locator('#search').fill('no-such-commit-000000');
  assert.equal(await page.locator('#cards article').count(),0);
  assert.ok(await page.locator('#bounds').isVisible());assert.ok(await page.locator('#completeness').isVisible());
  await page.locator('#search').fill('');await page.locator('#side').selectOption('');await page.locator('#group').selectOption('');
  const statuses=[...new Set(expected.map(({c})=>c.status))];
  for(const status of statuses){await page.locator('#status').selectOption(status);const count=expected.filter(({c})=>c.status===status).length;assert.equal(await page.locator('#cards article').count(),Math.min(100,count));}
  await page.locator('#status').selectOption('');
  const candidateSource=expected.find(({c})=>c.candidates?.length);
  if(candidateSource){
   await page.locator('#search').fill(candidateSource.c.oid);await page.locator('#side').selectOption(candidateSource.side);
   await page.locator('.card-open').first().click();
   await page.getByRole('button',{name:'Go to counterpart'}).first().focus();await page.keyboard.press('Enter');
   const other=candidateSource.side==='left'?'right':'left';
   assert.equal(await page.locator('#detail').getAttribute('data-key'),other+':'+candidateSource.c.candidates[0].oid);
  }
  if(expected.length){
   await page.locator('#side').selectOption(''); // Reset pagination after counterpart navigation.
   await page.locator('.card-open').first().focus();await page.keyboard.press('Enter');
   assert.equal(await page.locator('#detail').evaluate(n=>document.activeElement===n),true);
   await page.locator('#next-commit').focus();await page.keyboard.press('Enter');
   assert.equal(await page.locator('#detail').getAttribute('data-key'),expected[Math.min(1,expected.length-1)].side+':'+expected[Math.min(1,expected.length-1)].c.oid);
  }
  await page.setViewportSize({width:360,height:780});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),name+' '+JSON.stringify(await page.evaluate(()=>[...document.querySelectorAll('*')].filter(n=>n.getBoundingClientRect().right>innerWidth).map(n=>({tag:n.tagName,id:n.id,cls:n.className,width:n.getBoundingClientRect().width})))));
  await page.locator('#search').focus();await page.keyboard.press('Tab');assert.equal(await page.locator('#status').evaluate(n=>document.activeElement===n),true);
  assert.equal(await page.evaluate(()=>globalThis.PWNED),undefined);
  const peaks=await page.evaluate(()=>atlasPeaks);assert.ok(peaks.cards<=100);assert.ok(peaks.groupRows<=20);assert.ok(peaks.evidence<=32);
  assert.equal(await page.locator('img,iframe,object').count(),0);
  measurements.push({name,observed_peak_cards:peaks.cards,observed_peak_group_rows:peaks.groupRows,observed_peak_evidence_lines:peaks.evidence,observed_peak_dom_nodes:peaks.nodes,html_bytes:fs.statSync(file).size,load_ms:load,commits:expected.length,dom_nodes:await page.locator('*').count(),mounted_cards:await page.locator('#cards article').count(),mounted_evidence_lines:await page.locator('.evidence-line').count()});
  await page.setViewportSize({width:1200,height:900});
 }
 assert.deepEqual(requests,[]);assert.deepEqual(errors,[]);
 console.log(JSON.stringify({result:'passed',browser:await browser.version(),network:'offline context plus non-file route blocking; zero external requests',checks:['CLI JSON embedding parity','visible ordered statuses','exact duplicate group membership','candidate score and rank parity','evidence text and omission parity','filters retain warnings','keyboard selection, evidence and navigation','360px no horizontal overflow','hostile text inert','group candidates, endpoints, ordered members, ambiguity and evidence match CLI JSON','all group exclusions inspectable; group filters retain notices','group evidence shares global 32-line bound'],measurements,limitations:['Chromium only','no screen-reader audit','browser load includes Playwright navigation overhead; warm local cache']},null,2));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
