// Runs the SHIPPED browser code (extracted from site/index.html, not retyped)
// against a set of moments, and prints the numbers Python must agree with.
const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync(process.argv[2] || new URL("../../site/index.html", `file://${__filename}`).pathname, "utf8");
// the piece is one <script> block at the end of the file
const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const src = blocks.sort((a, b) => b.length - a.length)[0];

const noop = () => {};
const el = new Proxy({}, {
  get: (t, k) => (k === 'value' ? '' : k === 'style' ? {} :
                  k === 'classList' ? { toggle: noop, add: noop, remove: noop } :
                  k === 'textContent' ? '' : typeof k === 'string' ? noop : undefined),
  set: () => true,
});
const sandbox = {
  console,
  window: { addEventListener: noop },
  document: { getElementById: () => el, addEventListener: noop, createElement: () => el,
              head: { appendChild: noop }, body: { appendChild: noop } },
  location: { protocol: 'https:', origin: 'https://example.test' },
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  fetch: () => Promise.reject(new Error('no network in the parity harness')),
  setTimeout: noop, requestAnimationFrame: noop, Math, Date, JSON,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: 'index.html:script' });

const CASES = [
  { label: 'dual / the README example',
    a: ['1989-03-25', '03:17', 41.1865, -73.1952, -5],
    b: ['2026-08-11', '12:00', 41.1865, -73.1952, -4],
    msg: 'WHAT WAS ALREADY TRUE' },
  { label: 'dual / far apart, southern + eastern',
    a: ['1969-07-20', '20:17', -33.8688, 151.2093, 10],
    b: ['2026-01-01', '00:00', 64.1466, -21.9426, 0],
    msg: "IT'S LATE, AND THE LINE IS STILL OPEN." },
  { label: 'dual / antimeridian and a quarter-hour zone',
    a: ['2000-02-29', '23:59', 27.7172, 85.324, 5.75],
    b: ['1999-12-31', '00:01', -17.7134, 178.065, 12] },
  { label: 'dual / leading-zero month, negative lon',
    a: ['2001-01-01', '00:00', 0, 0, 0],
    b: ['1904-02-29', '13:45', 51.4779, -0.0015, 0], msg: 'ZERO, ZERO.' },
  { label: 'single / no present moment',
    a: ['1989-03-25', '03:17', 41.1865, -73.1952, -5], b: null, msg: 'ALONE' },
  { label: 'single / high latitude',
    a: ['2026-06-21', '12:00', 78.2232, 15.6267, 2], b: null },
];

const out = CASES.map(c => {
  const A = sandbox.mom(...c.a);
  const B = c.b ? sandbox.mom(...c.b) : null;
  const msg = c.msg ?? 'WHAT WAS ALREADY TRUE';
  const r = {
    label: c.label, a: c.a, b: c.b, msg,
    A: { angle: A.angle, order: A.order, points: A.points, root: A.root,
         spokes: A.spokes, petals: A.petals, rings: A.rings },
    key: sandbox.keyFrom(A, B),
    cipher: sandbox.encrypt(msg, A, B),
    stream8: sandbox.stream(A, B, 8),
  };
  if (B) {
    r.B = { angle: B.angle, order: B.order, points: B.points, root: B.root };
    const REL = sandbox.rel(A, B);
    r.REL = { sep: REL.sep, arc: REL.arc, km: REL.km, resonance: REL.resonance,
              beat: REL.beat, days: REL.days, dayMod: REL.dayMod, rootPair: REL.rootPair };
  }
  return r;
});
console.log(JSON.stringify(out, null, 1));
