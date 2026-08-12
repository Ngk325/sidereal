// Unit tests for the two things standing between /api/render and an open render
// farm: the config bounds and the IP rate limit.
//
//   cd service/test && node worker.test.mjs
//
// Compiles ../worker/src/limits.ts on the way in, so it tests the shipped source
// rather than a copy. Needs `npm install` to have been run in ../worker.
import { execFileSync } from 'child_process';
import { mkdtempSync } from 'fs';
import { tmpdir } from 'os';
import { join, dirname } from 'path';
import { fileURLToPath, pathToFileURL } from 'url';

const here = dirname(fileURLToPath(import.meta.url));
const out = mkdtempSync(join(tmpdir(), 'sidereal-limits-'));
execFileSync('npx', ['tsc', join(here, '../worker/src/limits.ts'),
                     '--outDir', out, '--target', 'es2022', '--module', 'esnext',
                     '--moduleResolution', 'bundler', '--skipLibCheck'],
             { cwd: join(here, '../worker'), stdio: 'inherit' });
const { validateConfig, overLimit, countAgainstLimit, humanise } =
  await import(pathToFileURL(join(out, 'limits.js')).href);

let pass = 0, fail = 0;
const t = (name, cond, extra = '') => {
  if (cond) pass++;
  else { fail++; console.log('  ✗ ' + name + (extra ? '  → ' + extra : '')); }
};

const GOOD = {
  name: 'Bridgeport, first light',
  place_label: 'Bridgeport, Connecticut',
  seed: { date: '1989-03-25', time: '03:17', lat: 41.1865, lon: -73.1952, tz: -5 },
  present: { date: '2026-08-11', time: '12:00', lat: 41.1865, lon: -73.1952, tz: -4 },
  anchor: 'start', frames: 144, fps: 12,
  print_seed: 19890325, message: 'WHAT WAS ALREADY TRUE', mark: true,
};
const base = () => JSON.parse(JSON.stringify(GOOD));

console.log('config bounds — must be rejected');
const rej = (name, mut) => { const c = base(); mut(c); t(name, validateConfig(c) !== null, 'accepted'); };
rej('frames: 100000', c => c.frames = 100000);
rej('frames: -1', c => c.frames = -1);
rej('frames: 0', c => c.frames = 0);
rej('frames: "1000000000"', c => c.frames = '1000000000');
rej('frames: 12.5', c => c.frames = 12.5);
rej('frames: NaN', c => c.frames = NaN);
rej('frames: null', c => c.frames = null);
rej('message: null', c => c.message = null);
rej('name: null', c => c.name = null);
rej('anchor: null', c => c.anchor = null);
rej('mark: null', c => c.mark = null);
rej('config is null', c => { for (const k of Object.keys(c)) delete c[k]; });
rej('fps: 10000', c => c.fps = 10000);
rej('lat: 999', c => c.seed.lat = 999);
rej('lat: null', c => c.seed.lat = null);
rej('lat: "41.18"', c => c.seed.lat = '41.18');
rej('lon: "abc"', c => c.seed.lon = 'abc');
rej('lon: Infinity', c => c.seed.lon = Infinity);
rej('tz: 99', c => c.seed.tz = 99);
rej('date: 25-03-1989', c => c.seed.date = '25-03-1989');
rej('date carrying a shell fragment', c => c.seed.date = '1989-03-25; rm -rf /');
rej('date: ../../etc/passwd', c => c.seed.date = '../../etc/passwd');
rej('time: 3:17', c => c.seed.time = '3:17');
rej('seed missing', c => delete c.seed);
rej('seed is a string', c => c.seed = '1989-03-25');
rej('seed is null', c => c.seed = null);
rej('present half-formed', c => c.present = { date: '2026-08-11' });
rej('present lat out of range', c => c.present.lat = -91);
rej('anchor: ../../etc', c => c.anchor = '../../etc');
rej('message of 10k characters', c => c.message = 'A'.repeat(10000));
rej('name of 5k characters', c => c.name = 'A'.repeat(5000));
rej('place_label of 5k characters', c => c.place_label = 'A'.repeat(5000));
rej('print_seed: 1.5', c => c.print_seed = 1.5);

console.log('config bounds — must be accepted');
const acc = (name, mut) => { const c = base(); mut(c); const e = validateConfig(c); t(name, e === null, e); };
acc('the config the front end sends', () => {});
acc('frames omitted', c => delete c.frames);
acc('frames at the maximum', c => c.frames = 288);
acc('frames at the minimum', c => c.frames = 1);
acc('fps omitted', c => delete c.fps);
acc('a quarter-hour offset', c => c.seed.tz = 5.75);
acc('latitude exactly -90', c => c.seed.lat = -90);
acc('longitude exactly 180', c => c.seed.lon = 180);
acc('null island', c => { c.seed.lat = 0; c.seed.lon = 0; });
acc('a single moment', c => delete c.present);
acc('present explicitly null — the single-moment case', c => c.present = null);
acc('an empty message', c => c.message = '');
acc('no message', c => delete c.message);
acc('punctuation in the message', c => c.message = "IT'S LATE, AND THE LINE IS STILL OPEN.");
acc('a leap day', c => c.seed.date = '2000-02-29');
acc('midnight', c => c.seed.time = '00:00');

console.log('rate limit');
const store = () => {
  const kv = new Map();
  return { get: async k => kv.get(k) ?? null, put: async (k, v) => void kv.set(k, v) };
};
const spend = async (env, ip, n) => {
  let allowed = 0;
  for (let i = 0; i < n; i++) {
    if (await overLimit(env, ip)) break;
    await countAgainstLimit(env, ip);
    allowed++;
  }
  return allowed;
};

const env = { JOBS: store(), RATE_PER_HOUR: '3', RATE_PER_DAY: '10' };
t('the hourly window allows exactly 3', await spend(env, '203.0.113.7', 8) === 3);
t('a different address is unaffected', (await overLimit(env, '198.51.100.9')) === null);
const hit = await overLimit(env, '203.0.113.7');
t('the refusal names the window and the number', hit?.label === 'hour' && hit?.max === 3);
t('the refusal carries a retry-after inside the hour',
  hit?.retryAfter > 0 && hit?.retryAfter <= 3600, String(hit?.retryAfter));

t('the daily window allows exactly 10',
  await spend({ JOBS: store(), RATE_PER_HOUR: '0', RATE_PER_DAY: '10' }, '203.0.113.7', 20) === 10);
t('setting both to 0 disables limiting',
  await spend({ JOBS: store(), RATE_PER_HOUR: '0', RATE_PER_DAY: '0' }, '203.0.113.7', 25) === 25);
t('a missing config falls back to the defaults',
  await spend({ JOBS: store() }, '203.0.113.7', 8) === 3);
t('an unparseable limit disables that window rather than blocking everyone',
  await spend({ JOBS: store(), RATE_PER_HOUR: 'three', RATE_PER_DAY: '0' }, '203.0.113.7', 12) === 12);

// The counter must expire, or a limit becomes a ban.
const kv = new Map();
const ttls = [];
const expiring = {
  JOBS: {
    get: async k => kv.get(k) ?? null,
    put: async (k, v, o) => { kv.set(k, v); ttls.push(o?.expirationTtl); },
  }, RATE_PER_HOUR: '3', RATE_PER_DAY: '10',
};
await countAgainstLimit(expiring, '203.0.113.7');
t('counters are written with an expiry', ttls.every(x => typeof x === 'number' && x >= 60), String(ttls));
t('the hourly counter expires within the hour', ttls[0] <= 3600, String(ttls[0]));

console.log('the message shown to a refused visitor');
t('seconds', humanise(45) === '45 seconds');
t('minutes', humanise(1800) === '30 minutes');
t('hours', humanise(7200) === '2 hours');

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
