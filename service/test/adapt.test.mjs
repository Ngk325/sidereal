// The adaptive default: it must step down when there is no service, leave a
// deliberate choice alone, and never touch the value when the service is up.
import { chromium } from 'playwright';
import { readFileSync } from 'fs';
import { createServer } from 'http';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, '../../site/index.html'), 'utf8');
const p5 = readFileSync(join(here, 'p5.min.js'), 'utf8');
let pass = 0, fail = 0;
const t = (n, c, extra='') => { if (c) { pass++; console.log('  ✓ ' + n); } else { fail++; console.log('  ✗ ' + n + (extra?'  → '+extra:'')); } };

function srv(mode) {
  return createServer((req, res) => {
    if (req.url.includes('p5')) { res.writeHead(200, {'content-type':'application/javascript'}); return res.end(p5); }
    if (req.url === '/api/render') {
      if (mode === 'live') { res.writeHead(400, {'content-type':'application/json'}); return res.end('{"error":"config.seed is required"}'); }
      res.writeHead(404); return res.end('nope');            // static-only host
    }
    res.writeHead(200, {'content-type':'text/html'}); res.end(html);
  });
}
async function open(mode, fn) {
  const s = srv(mode); await new Promise(r => s.listen(0,'127.0.0.1',r));
  const b = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH, args:['--no-sandbox'] });
  const page = await b.newPage();
  const errs = []; page.on('pageerror', e => errs.push(e.message));
  await page.route('**/p5.min.js', r => r.fulfill({ body: p5, contentType: 'application/javascript' }));
  await page.goto(`http://127.0.0.1:${s.address().port}/`);
  await page.waitForFunction(() => typeof SERVICE !== 'undefined' && SERVICE.probed, { timeout: 15000 });
  await page.waitForTimeout(300);
  try { await fn(page, errs); } finally { await b.close(); s.close(); }
}

console.log('no service — the default steps down');
await open('static', async (page, errs) => {
  t('length dropped to ten seconds', await page.locator('#lapse').inputValue() === '10,60',
    await page.locator('#lapse').inputValue());
  const c = await page.evaluate(() => snapshot());
  t('the snapshot agrees: 600 frames', c.frames === 600, String(c.frames));
  t('still 60fps — smoothness is not what got traded', c.fps === 60, String(c.fps));
  t('the log explains why', (await page.locator('#log').innerText()).includes('long '));
  t('no page errors', errs.length === 0, errs[0]);
});

console.log('\nno service — but a deliberate choice is left alone');
await open('static', async (page) => {
  await page.evaluate(() => {                       // the card is hidden until the figure is drawn;
    const el = document.getElementById('lapse');    // a real change event is what the handler listens for
    el.value = '60,60'; el.dispatchEvent(new Event('change'));
  });
  await page.evaluate(() => { SERVICE.probed = false; SERVICE.promise = null; probeService(); });
  await page.waitForTimeout(800);
  t('a hand-picked full minute survives a re-probe',
    await page.locator('#lapse').inputValue() === '60,60', await page.locator('#lapse').inputValue());
});

console.log('\nservice up — the default is untouched');
await open('live', async (page) => {
  t('length stays at the full minute', await page.locator('#lapse').inputValue() === '60,60',
    await page.locator('#lapse').inputValue());
  const c = await page.evaluate(() => snapshot());
  t('3600 frames', c.frames === 3600, String(c.frames));
});

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
