"""Compare the Python renderer's arithmetic and cipher against the browser's."""
import json, sys, math, importlib.util

spec = importlib.util.spec_from_file_location("render", sys.argv[1])
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)

cases = json.load(open(sys.argv[2]))

def as_moment(spec_):
    d, t, lat, lon, tz = spec_
    Y, M, D = map(int, d.split('-'))
    h, m = map(int, t.split(':'))
    return R.moment(Y, M, D, h, m, lat, lon, tz)

fails, checks = [], 0
def eq(label, name, js, py, tol=0.0):
    global checks
    checks += 1
    ok = (abs(js - py) <= tol) if isinstance(js, (int, float)) and not isinstance(js, bool) else (js == py)
    if not ok:
        fails.append(f"{label} :: {name}\n     browser {js!r}\n     python  {py!r}")

for c in cases:
    L = c['label']
    A = as_moment(c['a'])
    B = as_moment(c['b']) if c['b'] else None

    for k in ('angle', 'order', 'points', 'root', 'spokes', 'petals', 'rings'):
        if k in c['A']:
            eq(L, f'seed.{k}', c['A'][k], A[k], 1e-9 if k == 'angle' else 0)
    if B:
        for k in ('angle', 'order', 'points', 'root'):
            eq(L, f'present.{k}', c['B'][k], B[k], 1e-9 if k == 'angle' else 0)
        rel = R.relate(A, B)
        eq(L, 'rel.sep', c['REL']['sep'], rel['sep'], 1e-9)
        eq(L, 'rel.arc', c['REL']['arc'], rel['arc'], 1e-9)
        eq(L, 'rel.resonance', c['REL']['resonance'], rel['resonance'])
        eq(L, 'rel.beat', c['REL']['beat'], rel['beat'])

    eq(L, 'cipher.key', c['key'], R.key_from(A, B))
    eq(L, 'cipher.keystream[0:8]', c['stream8'], R.keystream(A, B, 8))
    eq(L, 'cipher.text', c['cipher'], R.encrypt(c['msg'], A, B))

print(f"{checks} comparisons across {len(cases)} cases")
if fails:
    print(f"\n{len(fails)} MISMATCH(ES):\n")
    print("\n\n".join(fails))
    sys.exit(1)
print("browser and server agree on every one")
