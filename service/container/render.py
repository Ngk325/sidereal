"""Sidereal Ink — server-side renderer.

Ports the browser piece exactly: sidereal angles, digit arithmetic, the
FNV-1a/mulberry32 cipher, the glyph ring, the anchor modes and the corner plate.
Renders frames with PIL/numpy, muxes with ffmpeg.
"""
import math, os, subprocess, tempfile
import numpy as np
from PIL import Image, ImageDraw, ImageFont

GROUND = (13, 20, 36)
GOLD   = (217, 164, 65)
BLUE   = (111, 177, 199)
EMBER  = (217, 119, 87)
SIZE   = 1200

CHARSET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ .,'?"
B32     = "abcdefghijklmnopqrstuvwxyz234567"

# ── time / sky ───────────────────────────────────────────────────
def julian_day(Y, M, D, hUT):
    if M <= 2:
        Y -= 1; M += 12
    A = Y // 100; B = 2 - A + A // 4
    return math.floor(365.25*(Y+4716)) + math.floor(30.6001*(M+1)) + D + B - 1524.5 + hUT/24

def lst_deg(Y, M, D, h, m, tz, lon):
    jd = julian_day(Y, M, D, h + m/60 - tz)
    T = (jd - 2451545.0) / 36525
    gmst = 280.46061837 + 360.98564736629*(jd-2451545.0) + 0.000387933*T*T - T**3/38710000
    return (gmst + lon) % 360

def digits(n): return [int(c) for c in str(abs(n))]
def reduce9(n):
    while n > 9: n = sum(digits(n))
    return n

def moment(Y, M, D, h, m, lat, lon, tz):
    total = sum(digits(Y)) + sum(digits(M)) + sum(digits(D))
    return dict(Y=Y, M=M, D=D, h=h, m=m, lat=lat, lon=lon, tz=tz,
                order=max(2, reduce9(sum(digits(Y)))), points=total,
                spokes=max(5, D), petals=max(3, sum(digits(h))+sum(digits(m))),
                rings=max(3, reduce9(D)), root=reduce9(total),
                angle=lst_deg(Y, M, D, h, m, tz, lon))

def great_circle(la1, lo1, la2, lo2):
    r = math.pi/180
    a, b, d = la1*r, la2*r, (lo2-lo1)*r
    return math.degrees(math.acos(max(-1, min(1,
        math.sin(a)*math.sin(b) + math.cos(a)*math.cos(b)*math.cos(d)))))

def relate(a, b):
    sep = (b['angle'] - a['angle']) % 360
    return dict(sep=sep,
                resonance=math.gcd(a['order'], b['order']),
                beat=a['order']*b['order']//math.gcd(a['order'], b['order']),
                arc=great_circle(a['lat'], a['lon'], b['lat'], b['lon']))

def self_rel(a):
    return dict(sep=0.0, resonance=a['order'], beat=a['order'], arc=0.0)

# ── cipher (bit-identical to the browser implementation) ─────────
def _u32(x): return x & 0xFFFFFFFF

def key_from(a, b):
    if b:
        parts = [round(a['angle']*1e4), round(b['angle']*1e4), round(a['lat']*1e4), round(a['lon']*1e4),
                 round(b['lat']*1e4), round(b['lon']*1e4), a['points'], b['points'],
                 a['order'], b['order'], a['root'], b['root']]
    else:
        parts = [round(a['angle']*1e4), round(a['lat']*1e4), round(a['lon']*1e4),
                 a['points'], a['order'], a['root']]
    h = 0x811c9dc5
    for p in parts:
        for ch in str(p):
            h = _u32(h ^ ord(ch))
            h = _u32(h * 0x01000193)
        h = _u32(h ^ 0x9e3779b9)
    return h

def keystream(a, b, n):
    s = key_from(a, b)
    out = []
    for _ in range(n):
        s = _u32(s + 0x6D2B79F5)
        t = s
        t = _u32(t ^ (t >> 15)) * _u32(1 | t) & 0xFFFFFFFF
        t = _u32(t + (_u32(t ^ (t >> 7)) * _u32(61 | t))) ^ t
        out.append(int((((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296) * 32))
    return out

def encrypt(text, a, b):
    chars = [c for c in text.upper() if c in CHARSET]
    ks = keystream(a, b, len(chars))
    return ''.join(B32[(CHARSET.index(c) + k) % 32] for c, k in zip(chars, ks))

# ── seeded field ─────────────────────────────────────────────────
def make_field(seed):
    rng = np.random.default_rng(seed)
    ph = rng.uniform(0, 2*np.pi, (6, 2))
    fr = np.array([0.9, 1.7, 2.6, 3.9, 5.4, 7.7])
    def field(x, y):
        v = np.zeros_like(x)
        for i in range(6):
            v += np.sin(fr[i]*x + ph[i, 0]) * np.cos(fr[i]*y + ph[i, 1]) / (i + 1.4)
        return (v/2.1 + 0.5) % 1.0
    return field

# ── drawing ──────────────────────────────────────────────────────
def _font(sz, bold=False):
    for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono%s.ttf" % ("-Bold" if bold else ""),
              "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf" % ("-Bold" if bold else "")]:
        if os.path.exists(p):
            return ImageFont.truetype(p, sz)
    return ImageFont.load_default()

def draw_layer(img, M, col, amt, extra, rng, cx, cy, R):
    d = ImageDraw.Draw(img, 'RGBA')
    off = math.radians(M['angle']) + extra
    for k in range(M['rings']):
        t = (k+1)/M['rings']; rad = R*(0.20+0.80*t**0.85); rot = off*(k+1)/M['rings']
        for s in range(M['spokes']):
            if rng.random() > 0.60: continue
            a0 = rot + 2*math.pi*s/M['spokes']
            a1 = a0 + 2*math.pi/M['spokes']*rng.uniform(0.35, 0.92)
            pts, a = [], a0
            while a <= a1:
                pts.append((cx+math.cos(a)*rad, cy+math.sin(a)*rad)); a += 0.02
            if len(pts) > 1:
                d.line(pts, fill=col+(int(rng.uniform(30, 110)*amt),), width=1)
    N = M['points']
    pts = [(cx+math.cos(off+2*math.pi*i/N)*R, cy+math.sin(off+2*math.pi*i/N)*R) for i in range(N)]
    for si, st in enumerate([M['petals'], 2, M['order']]):
        alpha = int((62 if si == 0 else 28)*amt)
        for i in range(N):
            d.line([pts[i], pts[(i*st) % N]], fill=col+(alpha,), width=1)
    for p in pts:
        d.ellipse([p[0]-1.4, p[1]-1.4, p[0]+1.4, p[1]+1.4], fill=col+(int(160*amt),))

def draw_nodes(img, rl, orders, mid, cx, cy, R, glow=0.7):
    d = ImageDraw.Draw(img, 'RGBA')
    n = min(rl['beat'], 144)
    rr = R*(0.30 + 0.55*(rl['resonance']/max(orders)))
    for i in range(n):
        a = mid + 2*math.pi*i/n
        x, y = cx+math.cos(a)*rr, cy+math.sin(a)*rr
        for g in range(6, 0, -1):
            d.ellipse([x-g*1.3, y-g*1.3, x+g*1.3, y+g*1.3], fill=EMBER+(int(11*glow),))
        d.ellipse([x-1.3, y-1.3, x+1.3, y+1.3], fill=EMBER+(int(200*glow),))

def draw_glyph_ring(img, cipher, seed_angle, cx, cy, R):
    if not cipher: return
    d = ImageDraw.Draw(img, 'RGBA')
    n, base = len(cipher), R*1.005
    o = math.radians(seed_angle)
    for i, ch in enumerate(cipher):
        v = B32.index(ch); a = o + 2*math.pi*i/n
        r1 = base + R*0.012 + (v/31)*R*0.085
        d.line([(cx+math.cos(a)*base, cy+math.sin(a)*base),
                (cx+math.cos(a)*r1,   cy+math.sin(a)*r1)], fill=GOLD+(160,), width=2)
        d.ellipse([cx+math.cos(a)*r1-1.3, cy+math.sin(a)*r1-1.3,
                   cx+math.cos(a)*r1+1.3, cy+math.sin(a)*r1+1.3], fill=EMBER+(190,))
    d.ellipse([cx-base, cy-base, cx+base, cy+base], outline=GOLD+(40,), width=1)

def ink(buf, M, colour, count, extra, dirn, weight, cx, cy, R, seed, field):
    r = np.random.default_rng(seed)
    order = M['order']
    v = r.integers(0, order, count)
    a0 = math.radians(M['angle']) + extra + 2*np.pi*v/order + r.normal(0, 0.10, count)
    rr = R*r.uniform(0.08, 1.0, count)
    x = cx + np.cos(a0)*rr; y = cy + np.sin(a0)*rr
    alive = np.ones(count, bool)
    tilt = math.radians(M['lat'])
    H, W = buf.shape[0], buf.shape[1]
    for _ in range(150):
        n = field((x-cx)/R*1.6, (y-cy)/R*1.6)
        rad = np.arctan2(y-cy, x-cx)
        ang = n*2*np.pi*2 + dirn*(rad+np.pi/2) + np.sin(rad*order)*0.35 + tilt*0.25
        x = np.where(alive, x + np.cos(ang)*1.35, x)
        y = np.where(alive, y + np.sin(ang)*1.35, y)
        alive &= (np.hypot(x-cx, y-cy) < R*1.06)
        if not alive.any(): break
        dx, dy = (x-cx)[alive], (y-cy)[alive]
        for k in range(order):
            th = 2*np.pi*k/order; c, s = math.cos(th), math.sin(th)
            px = (cx + dx*c - dy*s).astype(np.int32)
            py = (cy + dx*s + dy*c).astype(np.int32)
            ok = (px >= 0) & (px < W) & (py >= 0) & (py < H)
            np.add.at(buf, (py[ok], px[ok]), np.array(colour, float)*weight)

def corner_plate(img, title, subtitle, lines, right):
    d = ImageDraw.Draw(img, 'RGBA')
    if title:
        d.text((30, 26), title, font=_font(22, True), fill=(232, 230, 220, 235))
    if subtitle:
        d.text((30, 58), subtitle, font=_font(13), fill=(176, 174, 165, 190))
    for i, ln in enumerate(lines):
        y = SIZE - 34 - (len(lines)-1-i)*20
        d.text((30, y), ln, font=_font(14), fill=(200, 196, 185, 225 if i == 0 else 170))
    if right:
        f = _font(14)
        w = d.textlength(right, font=f)
        d.text((SIZE-30-w, SIZE-34), right, font=f, fill=(176, 174, 165, 170))

# ── one frame ────────────────────────────────────────────────────
def render_frame(cfg, f, step_min, start_ms, field):
    from datetime import datetime, timedelta, timezone
    t = datetime.fromtimestamp(start_ms/1000, timezone.utc) + timedelta(minutes=f*step_min)
    seed_cfg, pres_cfg = cfg['seed'], cfg.get('present')
    dual = pres_cfg is not None

    sY, sM, sD = map(int, seed_cfg['date'].split('-'))
    sh, sm = map(int, seed_cfg['time'].split(':'))

    if dual:
        A = moment(sY, sM, sD, sh, sm, seed_cfg['lat'], seed_cfg['lon'], seed_cfg['tz'])
        B = moment(t.year, t.month, t.day, t.hour, t.minute,
                   pres_cfg['lat'], pres_cfg['lon'], pres_cfg['tz'])
        RL = relate(A, B); extra = math.radians(RL['arc'])
    else:
        A = moment(t.year, t.month, t.day, t.hour, t.minute,
                   seed_cfg['lat'], seed_cfg['lon'], seed_cfg['tz'])
        B = None; RL = self_rel(A); extra = 0.0

    cipher = encrypt(cfg.get('message', ''), A, B)
    cx = cy = SIZE/2; R = SIZE*0.40
    img = Image.new('RGB', (SIZE, SIZE), GROUND)
    d = ImageDraw.Draw(img, 'RGBA')
    for rad in range(int(R*1.5), 0, -8):
        d.ellipse([cx-rad, cy-rad, cx+rad, cy+rad],
                  outline=(255, 238, 205, max(0, int(7*(1-rad/(R*1.5))))), width=6)

    seed = cfg.get('print_seed', 19890325)
    draw_layer(img, A, GOLD, 1.0, 0.0, np.random.default_rng(seed), cx, cy, R)
    if B:
        draw_layer(img, B, BLUE, 0.85, extra, np.random.default_rng(seed+7), cx, cy, R)
        draw_nodes(img, RL, (A['order'], B['order']),
                   math.radians((A['angle']+B['angle'])/2)+extra/2, cx, cy, R)
    draw_glyph_ring(img, cipher, A['angle'], cx, cy, R)

    buf = np.zeros((SIZE, SIZE, 3), np.float32)
    field_fn = field
    ink(buf, A, GOLD, 420, 0.0, 1, 0.055, cx, cy, R, seed+11, field_fn)
    if B:
        ink(buf, B, BLUE, 420, extra, -1, 0.050, cx, cy, R, seed+23, field_fn)
    img = Image.fromarray(np.clip(np.asarray(img, np.float32)+buf, 0, 255).astype(np.uint8))

    d = ImageDraw.Draw(img, 'RGBA')
    for i in range(1, 4):
        rr = R*0.05*i
        d.ellipse([cx-rr, cy-rr, cx+rr, cy+rr], outline=GOLD+(150-i*30,), width=1)
    d.ellipse([cx-3, cy-3, cx+3, cy+3], fill=EMBER)

    hit = cfg.get('mark', True) and abs(
        (t - datetime(*[int(x) for x in (pres_cfg or seed_cfg)['date'].split('-')],
                      *[int(x) for x in (pres_cfg or seed_cfg)['time'].split(':')],
                      tzinfo=timezone.utc)).total_seconds()) < step_min*30
    if hit:
        for i in range(9):
            rr = R*(0.12+i*0.21)
            d.ellipse([cx-rr, cy-rr, cx+rr, cy+rr], outline=EMBER+(46-i*5,), width=3)

    stamp = t.strftime('%Y-%m-%d %H:%M')
    lines = [f"seed     {seed_cfg['date']} {seed_cfg['time']}   "
             f"{seed_cfg['lat']:.3f}, {seed_cfg['lon']:.3f}"]
    if dual:
        lines.append(f"present  {stamp}   {pres_cfg['lat']:.3f}, {pres_cfg['lon']:.3f}   "
                     f"LST {B['angle']:.2f} deg   sep {RL['sep']:.2f} deg")
    else:
        lines.append(f"sweeping {stamp}   LST {A['angle']:.2f} deg   single moment")
    if hit:
        lines.append("* THE MOMENT")
    corner_plate(img, cfg.get('name', 'Sidereal Ink'), cfg.get('place_label', ''),
                 lines, '#'+str(seed))
    return img

def render_job(cfg, outdir, progress=None):
    """Render every frame and mux to mp4. Returns the output path."""
    from datetime import datetime, timezone, timedelta
    frames = int(cfg.get('frames', 144))
    step_min = 1440/frames
    src = cfg.get('present') or cfg['seed']
    Y, M, D = map(int, src['date'].split('-'))
    h, m = map(int, src['time'].split(':'))
    at = datetime(Y, M, D, h, m, tzinfo=timezone.utc)
    anchor = cfg.get('anchor', 'start')
    if anchor == 'calendar':
        start = datetime(Y, M, D, 0, 0, tzinfo=timezone.utc)
    elif anchor == 'centre':
        start = at - timedelta(hours=12)
    else:
        start = at
    start_ms = start.timestamp()*1000

    field = make_field(cfg.get('print_seed', 19890325))
    frames_dir = os.path.join(outdir, 'frames')
    os.makedirs(frames_dir, exist_ok=True)
    for f in range(frames):
        render_frame(cfg, f, step_min, start_ms, field).save(f"{frames_dir}/f{f:04d}.png")
        if progress and f % 4 == 0:
            progress(f+1, frames)
    if progress:
        progress(frames, frames)

    out = os.path.join(outdir, 'lapse.mp4')
    subprocess.run(['ffmpeg', '-y', '-framerate', str(cfg.get('fps', 12)),
                    '-i', f'{frames_dir}/f%04d.png', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', '-crf', '18', '-movflags', '+faststart', out],
                   check=True, capture_output=True)
    return out
