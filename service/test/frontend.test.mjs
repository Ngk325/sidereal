// End-to-end tests of the front end against a stand-in Worker, across all four
// deployment realities the page has to survive.
//
//   npm install --no-save playwright && npx playwright install chromium
//   cd service/test && node frontend.test.mjs
//
// The page pulls p5 and JSZip from a CDN, so this needs network access. To run
// offline, drop p5.min.js and jszip.min.js beside this file and they will be
// served from disk instead.
import { makeServer } from './stub.mjs';
import fs from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const here = dirname(fileURLToPath(import.meta.url));
let chromium;
try { ({ chromium } = await import('playwright')); }
catch {
  console.log('playwright is not installed — skipping the front-end tests.');
  console.log('  npm install --no-save playwright && npx playwright install chromium');
  process.exit(0);
}

// Vendored copies are optional; without them the page uses the CDN as it ships.
const local = name => {
  const p = join(here, name);
  return fs.existsSync(p) ? fs.readFileSync(p, 'utf8') : null;
};
const P5 = local('p5.min.js');
let pass = 0, fail = 0;
const t = (name, cond, extra = '') => {
  if (cond) { pass++; console.log('  ✓ ' + name); }
  else { fail++; console.log('  ✗ ' + name + (extra ? '  → ' + extra : '')); }
};

async function withPage(mode, fn) {
  const srv = makeServer(mode);
  await new Promise(r => srv.listen(0, '127.0.0.1', r));
  const port = srv.address().port;
  const browser = await chromium.launch({
    ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}),
    args: ['--no-sandbox'],
  });
  const page = await browser.newPage();
  if (P5) await page.route('**/p5.min.js',
    r => r.fulfill({ status: 200, contentType: 'application/javascript', body: P5 }));
  const JSZIP = local('jszip.min.js');
  if (JSZIP) await page.route('**/jszip.min.js',
    r => r.fulfill({ status: 200, contentType: 'application/javascript', body: JSZIP }));
  const logs = [];
  page.on('console', m => logs.push(m.text()));
  page.on('pageerror', e => logs.push('PAGEERROR ' + e.message));
  await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'domcontentloaded' });
  try { await fn(page, srv, logs); }
  finally { await browser.close(); await new Promise(r => srv.close(r)); }
}

const logText = p => p.locator('#log').innerText();
const drawFigure = async page => {
  await page.click('#drawBtn');
  await page.waitForSelector('#exportCard', { state: 'visible', timeout: 30000 });
};

// ── 1. static host: no service, everything falls back ────────────────
console.log('\nSTATIC-ONLY DEPLOYMENT (no /api route)');
await withPage('static', async (page, srv, logs) => {
  await page.waitForTimeout(1200);
  await drawFigure(page);
  t('email field is hidden', !(await page.locator('#emailField').isVisible()));
  t('the log says it is rendering in this browser', (await logText(page)).includes('no render service'));
  t('the helper text describes the browser queue', (await page.locator('#queueNote').innerText()).includes('in this browser'));
  t('no uncaught page errors', !logs.some(l => l.startsWith('PAGEERROR')), logs.filter(l => l.startsWith('PAGEERROR'))[0]);

  await page.selectOption('#frames', '6');
  await page.fill('#nameIn', 'Fallback test');
  const download = page.waitForEvent('download', { timeout: 60000 }).catch(() => null);
  await page.click('#queueBtn');
  await page.waitForSelector('.job', { timeout: 10000 });
  t('a job card appears', await page.locator('.job').first().isVisible());
  const d = await download;
  t('the browser renderer still produces a file', !!d, 'no download fired');
  if (d) t('the file is named from the job', /fallback-test/.test(d.suggestedFilename()), d.suggestedFilename());
  t('no render was posted to the server', srv.submissions.length === 0);
});

// ── 2. service present: submit, poll, link ───────────────────────────
console.log('\nSERVICE AVAILABLE');
await withPage('live', async (page, srv, logs) => {
  await page.waitForTimeout(1200);
  await drawFigure(page);
  t('email field is shown', await page.locator('#emailField').isVisible());
  t('the log says the service was found', (await logText(page)).includes('render service found'));
  t('the helper text describes server rendering', (await page.locator('#queueNote').innerText()).includes('server'));

  await page.fill('#nameIn', 'Bridgeport, first light');
  await page.click('#queueBtn');                       // no email yet
  await page.waitForTimeout(300);
  t('an empty email blocks submission', (await page.locator('#status').innerText()).includes('email'));
  t('nothing was submitted without an email', srv.submissions.length === 0);

  await page.fill('#emailIn', 'not-an-email');
  await page.click('#queueBtn');
  await page.waitForTimeout(300);
  t('a malformed email blocks submission', srv.submissions.length === 0);

  await page.fill('#emailIn', 'someone@example.com');
  await page.click('#queueBtn');
  await page.waitForFunction(() => document.querySelectorAll('.job').length > 0, { timeout: 10000 });
  await page.waitForTimeout(500);
  t('exactly one render was submitted', srv.submissions.length === 1, String(srv.submissions.length));

  const s = srv.submissions[0] || {};
  const c = s.config || {};
  t('the email is sent at the top level', s.email === 'someone@example.com');
  t('config.seed uses date/time/lat/lon/tz', ['date','time','lat','lon','tz'].every(k => k in (c.seed || {})),
    JSON.stringify(c.seed));
  t('config.seed.date is YYYY-MM-DD', c.seed?.date === '1989-03-25');
  t('config.seed.time is HH:MM', c.seed?.time === '03:17');
  t('lat/lon/tz are numbers', [c.seed?.lat, c.seed?.lon, c.seed?.tz].every(v => typeof v === 'number'));
  t('print_seed is flat, not P.seed', c.print_seed === 19890325 && !('P' in c));
  t('present is included when the present moment is on', !!c.present && 'date' in c.present);
  t('name carries through', c.name === 'Bridgeport, first light');
  t('place_label is a string', typeof c.place_label === 'string' && c.place_label.length > 0, c.place_label);
  t('message carries through', typeof c.message === 'string');
  t('mark and anchor carry through', typeof c.mark === 'boolean' && !!c.anchor);
  t('fps is set', c.fps === 12);
  t('none of the browser-only keys leak', !['dual','pres','format','msg','P'].some(k => k in c),
    Object.keys(c).join(','));

  // progress bar driven by done/total from the server
  await page.waitForFunction(() => /frame \d+ of 144 on the server/.test(document.body.innerText), { timeout: 20000 });
  const pct = await page.locator('.bar i').first().evaluate(e => e.style.width);
  t('the progress bar is driven by the server counts', /^\d+%$/.test(pct) && parseInt(pct) > 0 && parseInt(pct) < 100, pct);

  await page.waitForFunction(() => document.body.innerText.includes('Download the video'), { timeout: 30000 });
  const href = await page.locator('a:has-text("Download the video")').first().getAttribute('href');
  t('the job card links to /f/:id', href === '/f/job-1-uuid', href);
  const lg = await logText(page);
  t('the render log carries the link', lg.includes('/f/job-1-uuid'), lg.split('\n').slice(-2).join(' | '));
  t('the log reports completion', lg.includes('complete'));
  t('no uncaught page errors', !logs.some(l => l.startsWith('PAGEERROR')), logs.filter(l => l.startsWith('PAGEERROR'))[0]);
});

// ── 3. rate limited: the message survives, the browser path stays open ─
console.log('\nSERVICE REFUSES THE JOB (429)');
await withPage('ratelimited', async (page, srv) => {
  await page.waitForTimeout(1200);
  await drawFigure(page);
  await page.selectOption('#frames', '6');
  await page.fill('#nameIn', 'Refused');
  await page.fill('#emailIn', 'someone@example.com');
  await page.click('#queueBtn');
  await page.waitForFunction(() => document.body.innerText.includes('render limit reached'), { timeout: 15000 });
  t("the server's own message reaches the user", true);
  t('the failed card offers the browser renderer',
    await page.locator('button:has-text("Render in this browser instead")').isVisible());

  const download = page.waitForEvent('download', { timeout: 60000 }).catch(() => null);
  await page.click('button:has-text("Render in this browser instead")');
  const d = await download;
  t('clicking it renders locally and produces a file', !!d, 'no download fired');
});

// ── 4. the ZIP path is untouched by any of this ───────────────────────
console.log('\nZIP FORMAT WITH THE SERVICE UP (must stay local)');
await withPage('live', async (page, srv) => {
  await page.waitForTimeout(1200);
  await drawFigure(page);
  await page.selectOption('#format', 'zip');
  await page.selectOption('#frames', '6');
  await page.fill('#nameIn', 'Zip stays local');
  await page.fill('#emailIn', 'someone@example.com');
  const download = page.waitForEvent('download', { timeout: 60000 }).catch(() => null);
  await page.click('#queueBtn');
  await page.waitForTimeout(2500);
  t('a ZIP job is not sent to the server', srv.submissions.length === 0, String(srv.submissions.length));
  t('the log marks it as a browser render', (await logText(page)).includes('in this browser'));
  const d = await download;
  t('the ZIP is produced locally', !!d && /\.zip$/.test(d.suggestedFilename() || ''), d?.suggestedFilename());
});

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
