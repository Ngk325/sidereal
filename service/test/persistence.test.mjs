// Does a server render survive the window closing?
//
// The whole promise of the service is that you can queue work and walk away. The
// render does carry on — it runs on the server — but the queue lives in memory, so
// the page used to come back knowing nothing about a job it had submitted minutes
// earlier. Submit, close the page, reopen it, and check the job is picked back up,
// polled to completion, and still offers its link on a second reopen.
//
// Two of these are privacy checks, not features: the plaintext message and the
// email address must not end up in localStorage.
import { chromium } from 'playwright';
import { readFileSync } from 'fs';
import { makeServer } from './stub.mjs';
const p5 = readFileSync('./p5.min.js','utf8');
let pass=0,fail=0; const t=(n,c,x='')=>{ if(c){pass++;console.log('  ✓ '+n);} else {fail++;console.log('  ✗ '+n+(x?'  → '+x:''));} };
const srv = makeServer('live');
await new Promise(r=>srv.listen(0,'127.0.0.1',r));
const url = `http://127.0.0.1:${srv.address().port}/`;
const b = await chromium.launch({executablePath:'/opt/pw-browsers/chromium',args:['--no-sandbox']});
const ctx = await b.newContext();                     // one origin => one localStorage
const errs=[];

async function fresh(){ const p=await ctx.newPage(); p.on('pageerror',e=>errs.push(e.message));
  await p.route('**/p5.min.js', r=>r.fulfill({body:p5,contentType:'application/javascript'}));
  await p.goto(url); await p.waitForFunction(()=>typeof SERVICE!=='undefined'&&SERVICE.probed,{timeout:20000});
  return p; }

let page = await fresh();
await page.click('button:has-text("Draw figure")');
await page.waitForSelector('#exportCard',{state:'visible',timeout:30000});
await page.fill('#nameIn','Persisted job');
await page.fill('#emailIn','someone@example.com');
await page.click('#queueBtn');
await page.waitForFunction(()=>QUEUE.some(j=>j.remoteId),{timeout:15000});
const rid = await page.evaluate(()=>QUEUE.find(j=>j.remoteId).remoteId);
t('the job got a server id', !!rid, String(rid));
t('it was written to localStorage',
  (await page.evaluate(()=>JSON.parse(localStorage.getItem('sidereal.remote.v1')||'[]'))).length === 1);
const stored = await page.evaluate(()=>JSON.parse(localStorage.getItem('sidereal.remote.v1'))[0]);
t('the plaintext message is NOT stored', !('msg' in stored.cfg), JSON.stringify(Object.keys(stored.cfg)));
t('no email is stored', !JSON.stringify(stored).includes('someone@example.com'));

await page.close();                                   // ← close the window
page = await fresh();                                 // ← open it again
await page.waitForTimeout(1500);
const q = await page.evaluate(()=>QUEUE.map(j=>({remoteId:j.remoteId,status:j.status,name:j.cfg.name})));
t('the job came back after reopening', q.length===1 && q[0].remoteId===rid, JSON.stringify(q));
t('its name survived', q[0]?.name==='Persisted job');
t('the log mentions the earlier visit', (await page.locator('#log').innerText()).includes('earlier visit'));

await page.waitForFunction(()=>QUEUE.some(j=>j.status==='done'),{timeout:40000});
t('polling resumed and it finished', true);
t('the download link is there',
  (await page.locator('a:has-text("Download the video")').first().getAttribute('href')) === '/f/'+rid);

await page.close(); page = await fresh(); await page.waitForTimeout(1200);
const q2 = await page.evaluate(()=>QUEUE.map(j=>({status:j.status,link:j.link})));
t('a finished render still restores, with its link', q2.length===1 && q2[0].status==='done' && q2[0].link==='/f/'+rid,
  JSON.stringify(q2));
await page.evaluate(()=>clearDone());
await page.waitForTimeout(300);
t('clearing it forgets it for good',
  (await page.evaluate(()=>JSON.parse(localStorage.getItem('sidereal.remote.v1')||'[]'))).length===0);
t('no page errors', errs.length===0, errs[0]);
console.log(`\n${pass} passed, ${fail} failed`);
await b.close(); srv.close(); process.exit(fail?1:0);
