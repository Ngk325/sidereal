"""Sidereal Ink — server-side renderer.

Ports the browser piece exactly: sidereal angles, digit arithmetic, the
FNV-1a/mulberry32 cipher, the glyph ring, the anchor modes and the corner plate.
Renders frames with PIL/numpy, muxes with ffmpeg.

Two registers of time, and the distinction is the whole reason this reads as
motion rather than as a slideshow:

  * the digit registers — order, points, spokes, rings, petals — read the clock,
    and a clock reads in whole minutes. They step.
  * the sidereal angle reads the instant, and an instant is continuous. It flows.

Frames land wherever they land between minute ticks; passing only h and m to
`moment` would quantise the entire plate to the minute, so every frame inside a
tick came out byte-identical and the lapse played as a series of jumps. `sec`
carries the rest of the instant to `lst_deg` and nowhere else.
"""
import math, os, subprocess
from functools import lru_cache
import numpy as np
from PIL import Image, ImageDraw, ImageFont

GROUND = (13, 20, 36)
GOLD   = (217, 164, 65)
BLUE   = (111, 177, 199)
EMBER  = (217, 119, 87)

DRAWN_AT = 1200        # the size the composition was drawn at
SIZE     = 1440        # default plate; every length below scales from DRAWN_AT

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

def moment(Y, M, D, h, m, lat, lon, tz, sec=0.0):
    """`sec` is the seconds past the minute h:m. It reaches the sidereal angle and
    nothing else — the digit registers below are sums over a clock reading, and a
    clock reading has no fractional part to sum."""
    total = sum(digits(Y)) + sum(digits(M)) + sum(digits(D))
    return dict(Y=Y, M=M, D=D, h=h, m=m, lat=lat, lon=lon, tz=tz,
                order=max(2, reduce9(sum(digits(Y)))), points=total,
                spokes=max(5, D), petals=max(3, sum(digits(h))+sum(digits(m))),
                rings=max(3, reduce9(D)), root=reduce9(total),
                angle=lst_deg(Y, M, D, h, m + sec/60.0, tz, lon))

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

# ── the plate ────────────────────────────────────────────────────
class Plate:
    """Geometry for one square plate.

    The composition was drawn at 1200px. Every length is expressed as a multiple
    of `s`, so a larger plate is the same picture at a finer grain rather than the
    same picture with thinner lines.
    """
    __slots__ = ('size', 's', 'cx', 'cy', 'R')

    def __init__(self, size):
        self.size = int(size)
        self.s = self.size / DRAWN_AT
        self.cx = self.cy = self.size / 2.0
        self.R = self.size * 0.40

    def w(self, px):
        """A stroke width in drawn-at pixels, never thinner than one real pixel."""
        return max(1, int(round(px * self.s)))

# ── drawing ──────────────────────────────────────────────────────
@lru_cache(maxsize=64)
def _font(sz, bold=False):
    for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono%s.ttf" % ("-Bold" if bold else ""),
              "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf" % ("-Bold" if bold else "")]:
        if os.path.exists(p):
            return ImageFont.truetype(p, int(sz))
    return ImageFont.load_default()

def _dot(d, x, y, r, fill):
    d.ellipse([x-r, y-r, x+r, y+r], fill=fill)

def draw_layer(img, M, col, amt, extra, rng, pl):
    d = ImageDraw.Draw(img, 'RGBA')
    cx, cy, R = pl.cx, pl.cy, pl.R
    off = math.radians(M['angle']) + extra
    for k in range(M['rings']):
        t = (k+1)/M['rings']; rad = R*(0.20+0.80*t**0.85); rot = off*(k+1)/M['rings']
        # step the arc so each segment is about a pixel — at 0.02rad flat, an outer
        # ring lands 11px between vertices and reads as a polygon, not a circle
        da = min(0.02, 1.2/max(rad, 1.0))
        for s in range(M['spokes']):
            if rng.random() > 0.60: continue
            a0 = rot + 2*math.pi*s/M['spokes']
            a1 = a0 + 2*math.pi/M['spokes']*rng.uniform(0.35, 0.92)
            n = max(2, int((a1-a0)/da) + 1)
            aa = np.linspace(a0, a1, n)
            pts = list(zip(cx+np.cos(aa)*rad, cy+np.sin(aa)*rad))
            if len(pts) > 1:
                d.line(pts, fill=col+(int(rng.uniform(30, 110)*amt),), width=pl.w(1))
    N = M['points']
    pts = [(cx+math.cos(off+2*math.pi*i/N)*R, cy+math.sin(off+2*math.pi*i/N)*R) for i in range(N)]
    for si, st in enumerate([M['petals'], 2, M['order']]):
        alpha = int((62 if si == 0 else 28)*amt)
        for i in range(N):
            d.line([pts[i], pts[(i*st) % N]], fill=col+(alpha,), width=pl.w(1))
    for p in pts:
        _dot(d, p[0], p[1], 1.4*pl.s, col+(int(160*amt),))

def draw_nodes(img, rl, orders, mid, pl, glow=0.7):
    d = ImageDraw.Draw(img, 'RGBA')
    cx, cy, R = pl.cx, pl.cy, pl.R
    n = min(rl['beat'], 144)
    rr = R*(0.30 + 0.55*(rl['resonance']/max(orders)))
    for i in range(n):
        a = mid + 2*math.pi*i/n
        x, y = cx+math.cos(a)*rr, cy+math.sin(a)*rr
        for g in range(6, 0, -1):
            _dot(d, x, y, g*1.3*pl.s, EMBER+(int(11*glow),))
        _dot(d, x, y, 1.3*pl.s, EMBER+(int(200*glow),))

def draw_glyph_ring(img, cipher, seed_angle, pl):
    if not cipher: return
    d = ImageDraw.Draw(img, 'RGBA')
    cx, cy, R = pl.cx, pl.cy, pl.R
    n, base = len(cipher), R*1.005
    o = math.radians(seed_angle)
    for i, ch in enumerate(cipher):
        v = B32.index(ch); a = o + 2*math.pi*i/n
        r1 = base + R*0.012 + (v/31)*R*0.085
        d.line([(cx+math.cos(a)*base, cy+math.sin(a)*base),
                (cx+math.cos(a)*r1,   cy+math.sin(a)*r1)], fill=GOLD+(160,), width=pl.w(2))
        _dot(d, cx+math.cos(a)*r1, cy+math.sin(a)*r1, 1.3*pl.s, EMBER+(190,))
    d.ellipse([cx-base, cy-base, cx+base, cy+base], outline=GOLD+(40,), width=pl.w(1))

def ink(buf, M, colour, count, extra, dirn, weight, pl, seed, field):
    """Disperse `count` particles through the field and stamp the plate's rotational
    symmetry on every step.

    The hits are counted into one flat histogram and multiplied by the colour once,
    rather than added colour-by-colour into the image — same deposit, and it does not
    pay `np.ufunc.at`'s unbuffered scatter for every step of every particle.
    """
    r = np.random.default_rng(seed)
    cx, cy, R = pl.cx, pl.cy, pl.R
    order = M['order']
    v = r.integers(0, order, count)
    a0 = math.radians(M['angle']) + extra + 2*np.pi*v/order + r.normal(0, 0.10, count)
    rr = R*r.uniform(0.08, 1.0, count)
    x = cx + np.cos(a0)*rr; y = cy + np.sin(a0)*rr
    alive = np.ones(count, bool)
    tilt = math.radians(M['lat'])
    H, W = buf.shape[0], buf.shape[1]
    th = 2*np.pi*np.arange(order)/order
    ck, sk = np.cos(th)[:, None], np.sin(th)[:, None]
    step = 1.35*pl.s
    hit_idx = []
    for _ in range(150):
        n = field((x-cx)/R*1.6, (y-cy)/R*1.6)
        rad = np.arctan2(y-cy, x-cx)
        ang = n*2*np.pi*2 + dirn*(rad+np.pi/2) + np.sin(rad*order)*0.35 + tilt*0.25
        x = np.where(alive, x + np.cos(ang)*step, x)
        y = np.where(alive, y + np.sin(ang)*step, y)
        alive &= (np.hypot(x-cx, y-cy) < R*1.06)
        if not alive.any(): break
        dx, dy = (x-cx)[alive], (y-cy)[alive]
        px = (cx + dx*ck - dy*sk).astype(np.int64)
        py = (cy + dx*sk + dy*ck).astype(np.int64)
        ok = (px >= 0) & (px < W) & (py >= 0) & (py < H)
        hit_idx.append(py[ok]*W + px[ok])
    if not hit_idx: return
    hits = np.bincount(np.concatenate(hit_idx), minlength=H*W).reshape(H, W)
    nz = hits > 0
    buf[nz] += hits[nz][:, None].astype(np.float32) * (np.asarray(colour, np.float32)*weight)

def corner_plate(img, title, subtitle, lines, right, pl):
    d = ImageDraw.Draw(img, 'RGBA')
    s, S = pl.s, pl.size
    if title:
        d.text((30*s, 26*s), title, font=_font(22*s, True), fill=(232, 230, 220, 235))
    if subtitle:
        d.text((30*s, 58*s), subtitle, font=_font(13*s), fill=(176, 174, 165, 190))
    for i, ln in enumerate(lines):
        y = S - 34*s - (len(lines)-1-i)*20*s
        d.text((30*s, y), ln, font=_font(14*s), fill=(200, 196, 185, 225 if i == 0 else 170))
    if right:
        f = _font(14*s)
        w = d.textlength(right, font=f)
        d.text((S-30*s-w, S-34*s), right, font=f, fill=(176, 174, 165, 170))

# ── the parts that do not move ───────────────────────────────────
def _vignette(pl):
    img = Image.new('RGB', (pl.size, pl.size), GROUND)
    d = ImageDraw.Draw(img, 'RGBA')
    cx, cy, R = pl.cx, pl.cy, pl.R
    outer = int(R*1.5)
    for rad in range(outer, 0, -max(1, int(round(8*pl.s)))):
        d.ellipse([cx-rad, cy-rad, cx+rad, cy+rad],
                  outline=(255, 238, 205, max(0, int(7*(1-rad/(R*1.5))))), width=pl.w(6))
    return img

class Backdrop:
    """Everything that is identical on every frame of a job, rendered once.

    When a fixed seed is held against a sweeping present, the seed's own layer and
    its ink are the same marks 3600 times over — and the seed usually carries the
    higher order, so its ink is the expensive one. Hoisting it out is not a
    shortcut: the deposit is the same, it is simply not recomputed.

    Draw order is preserved exactly. Ink is additive and composited last, so
    holding the seed's ink in a buffer alongside the sweeping layer's is the same
    sum in the same order.
    """
    def __init__(self, cfg, A, dual, pl, seed, field):
        self.plate = _vignette(pl)
        self.ink = np.zeros((pl.size, pl.size, 3), np.float32)
        self.static_seed_layer = dual
        n_ink = max(1, int(round(cfg.get('ink', 420) * pl.s * pl.s)))
        self.n_ink = n_ink
        if dual:
            # A is the seed moment: fixed for the whole sweep.
            draw_layer(self.plate, A, GOLD, 1.0, 0.0, np.random.default_rng(seed), pl)
            ink(self.ink, A, GOLD, n_ink, 0.0, 1, 0.055, pl, seed+11, field)

# ── one frame ────────────────────────────────────────────────────
def frame_time(start_ms, f, step_min):
    from datetime import datetime, timedelta, timezone
    return datetime.fromtimestamp(start_ms/1000, timezone.utc) + timedelta(minutes=f*step_min)

def render_frame(cfg, f, step_min, start_ms, field, pl=None, back=None, hold_min=None):
    from datetime import datetime, timezone
    pl = pl or Plate(cfg.get('size', SIZE))
    if hold_min is None:
        hold_min = step_min/2          # the original half-a-step window
    t = frame_time(start_ms, f, step_min)
    sec = t.second + t.microsecond/1e6          # the part of the instant the clock does not show
    seed_cfg, pres_cfg = cfg['seed'], cfg.get('present')
    dual = pres_cfg is not None

    sY, sM, sD = map(int, seed_cfg['date'].split('-'))
    sh, sm = map(int, seed_cfg['time'].split(':'))

    if dual:
        A = moment(sY, sM, sD, sh, sm, seed_cfg['lat'], seed_cfg['lon'], seed_cfg['tz'])
        B = moment(t.year, t.month, t.day, t.hour, t.minute,
                   pres_cfg['lat'], pres_cfg['lon'], pres_cfg['tz'], sec)
        RL = relate(A, B); extra = math.radians(RL['arc'])
    else:
        A = moment(t.year, t.month, t.day, t.hour, t.minute,
                   seed_cfg['lat'], seed_cfg['lon'], seed_cfg['tz'], sec)
        B = None; RL = self_rel(A); extra = 0.0

    cipher = encrypt(cfg.get('message', ''), A, B)
    seed = cfg.get('print_seed', 19890325)
    if back is None:
        back = Backdrop(cfg, A, dual, pl, seed, field)

    img = back.plate.copy()
    if not back.static_seed_layer:
        draw_layer(img, A, GOLD, 1.0, 0.0, np.random.default_rng(seed), pl)
    if B:
        draw_layer(img, B, BLUE, 0.85, extra, np.random.default_rng(seed+7), pl)
        draw_nodes(img, RL, (A['order'], B['order']),
                   math.radians((A['angle']+B['angle'])/2)+extra/2, pl)
    draw_glyph_ring(img, cipher, A['angle'], pl)

    buf = back.ink.copy()
    if back.static_seed_layer:
        ink(buf, B, BLUE, back.n_ink, extra, -1, 0.050, pl, seed+23, field)
    else:
        ink(buf, A, GOLD, back.n_ink, 0.0, 1, 0.055, pl, seed+11, field)
    np.add(np.asarray(img, np.float32), buf, out=buf)
    img = Image.fromarray(np.clip(buf, 0, 255, out=buf).astype(np.uint8))

    d = ImageDraw.Draw(img, 'RGBA')
    cx, cy, R = pl.cx, pl.cy, pl.R
    for i in range(1, 4):
        rr = R*0.05*i
        d.ellipse([cx-rr, cy-rr, cx+rr, cy+rr], outline=GOLD+(150-i*30,), width=pl.w(1))
    _dot(d, cx, cy, 3*pl.s, EMBER)

    # THE MOMENT. At 12fps a single marked frame held for 83ms; at 60fps it would be
    # a 16ms subliminal blip, so the mark blooms over `mark_hold` seconds of screen
    # time and decays. What it marks is unchanged — the exact instant, to the frame.
    hit = 0.0
    if cfg.get('mark', True):
        mark_at = datetime(*[int(x) for x in (pres_cfg or seed_cfg)['date'].split('-')],
                           *[int(x) for x in (pres_cfg or seed_cfg)['time'].split(':')],
                           tzinfo=timezone.utc)
        away = abs((t - mark_at).total_seconds()) / 60.0          # in swept minutes
        if hold_min > 0 and away <= hold_min:
            hit = math.cos(math.pi/2 * away/hold_min)**2
    if hit > 0:
        for i in range(9):
            rr = R*(0.12+i*0.21)
            d.ellipse([cx-rr, cy-rr, cx+rr, cy+rr],
                      outline=EMBER+(max(1, int((46-i*5)*hit)),), width=pl.w(3))

    stamp = t.strftime('%Y-%m-%d %H:%M')
    lines = [f"seed     {seed_cfg['date']} {seed_cfg['time']}   "
             f"{seed_cfg['lat']:.3f}, {seed_cfg['lon']:.3f}"]
    if dual:
        lines.append(f"present  {stamp}   {pres_cfg['lat']:.3f}, {pres_cfg['lon']:.3f}   "
                     f"LST {B['angle']:.2f} deg   sep {RL['sep']:.2f} deg")
    else:
        lines.append(f"sweeping {stamp}   LST {A['angle']:.2f} deg   single moment")
    if hit > 0:
        lines.append("* THE MOMENT")
    corner_plate(img, cfg.get('name', 'Sidereal Ink'), cfg.get('place_label', ''),
                 lines, '#'+str(seed), pl)
    return img

# ── the job ──────────────────────────────────────────────────────
DEFAULT_DURATION = 60.0     # seconds of finished video
DEFAULT_FPS      = 60
DEFAULT_SPAN     = 12.0     # hours of sky the video sweeps through

# Measured on a 1440px lapse, PSNR against the raw frames:
#
#   yuv420p  crf 14  204 MB  35.83 dB      yuv444p  crf 16  157 MB  38.00 dB
#   yuv420p  crf 18  118 MB  35.76 dB      yuv444p  crf 18  117 MB  37.82 dB
#   yuv420p  crf 23   52 MB  35.45 dB
#
# 4:2:0 sits on a hard ceiling around 35.8 dB and no amount of bitrate moves it:
# the loss is the chroma downsample, not the quantiser, and the plate is one-pixel
# gold lines on near-black — the exact content that survives worst. So there is no
# point paying for crf 14 in 'web': crf 18 is within 0.07 dB of the ceiling at
# half the size. 'master' spends the same bytes on full chroma instead and gains a
# real 2.2 dB — at the cost of High 4:4:4 Predictive, which Safari, iOS and most
# hardware decoders will not play.
QUALITY = {
    'web':    dict(pix_fmt='yuv420p', crf=18, preset='medium'),
    'master': dict(pix_fmt='yuv444p', crf=16, preset='medium'),
}

def plan(cfg):
    """Resolve a config into the numbers the render actually runs on.

    `frames` is honoured when given — the smoke test asks for six of them — but the
    piece is now specified the way it is watched: a duration and a frame rate.
    """
    fps = int(cfg.get('fps') or DEFAULT_FPS)
    span_h = float(cfg.get('span_hours') or DEFAULT_SPAN)
    if cfg.get('frames'):
        frames = int(cfg['frames'])
    else:
        frames = max(1, int(round(float(cfg.get('duration') or DEFAULT_DURATION) * fps)))
    step_min = span_h*60.0/frames
    # The mark blooms for `mark_hold` seconds either side of the moment, but never
    # for more than a twentieth of the lapse — otherwise a six-frame test render
    # would be one continuous flash.
    hold_frames = max(0.5, min(float(cfg.get('mark_hold', 0.6))*fps, frames*0.05))
    q = dict(QUALITY.get(cfg.get('quality', 'web'), QUALITY['web']))
    for k in ('pix_fmt', 'crf', 'preset'):        # explicit settings still win
        if cfg.get(k) is not None:
            q[k] = cfg[k]
    return dict(fps=fps, frames=frames, duration=frames/fps,
                span_hours=span_h, step_min=step_min, hold_min=hold_frames*step_min,
                size=int(cfg.get('size') or SIZE),
                pix_fmt=q['pix_fmt'], crf=str(q['crf']), preset=q['preset'])

def job_start(cfg, span_h):
    from datetime import datetime, timezone, timedelta
    src = cfg.get('present') or cfg['seed']
    Y, M, D = map(int, src['date'].split('-'))
    h, m = map(int, src['time'].split(':'))
    at = datetime(Y, M, D, h, m, tzinfo=timezone.utc)
    anchor = cfg.get('anchor', 'start')
    if anchor == 'calendar':
        return datetime(Y, M, D, 0, 0, tzinfo=timezone.utc)
    if anchor == 'centre':
        return at - timedelta(hours=span_h/2)
    return at

# Set once per worker process by _init, so the backdrop and the field are built
# once per core rather than once per frame.
_W = {}

def _init(cfg, P, start_ms):
    from datetime import datetime, timezone
    pl = Plate(P['size'])
    field = make_field(cfg.get('print_seed', 19890325))
    seed_cfg, pres_cfg = cfg['seed'], cfg.get('present')
    dual = pres_cfg is not None
    sY, sM, sD = map(int, seed_cfg['date'].split('-'))
    sh, sm = map(int, seed_cfg['time'].split(':'))
    A = moment(sY, sM, sD, sh, sm, seed_cfg['lat'], seed_cfg['lon'], seed_cfg['tz'])
    _W.update(cfg=cfg, P=P, start_ms=start_ms, pl=pl, field=field,
              back=Backdrop(cfg, A, dual, pl, cfg.get('print_seed', 19890325), field))

def _frame_bytes(f):
    img = render_frame(_W['cfg'], f, _W['P']['step_min'], _W['start_ms'],
                       _W['field'], _W['pl'], _W['back'], _W['P']['hold_min'])
    return img.tobytes()

def _ffmpeg(P, out):
    """Encode from raw frames on stdin.

    No PNGs on the way: 3600 frames at 1440px would be gigabytes of disk and a PNG
    encode per frame, to hand ffmpeg pixels it already had.

    yuv420p by default because the result has to play everywhere, including the
    phone the download link arrives on. The plate is one-pixel gold lines on
    near-black, which is the content 4:2:0 handles worst, so the answer is to spend
    bitrate rather than profile: CRF 16 keeps the chroma error under a quantiser
    step. `pix_fmt: "yuv444p"` is there for an archival master — full chroma, and
    High 4:4:4 which Safari and most hardware decoders will refuse.
    """
    size = P['size']
    cmd = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
           '-f', 'rawvideo', '-pix_fmt', 'rgb24',
           '-s', f'{size}x{size}', '-framerate', str(P['fps']), '-i', 'pipe:0',
           '-an', '-c:v', 'libx264', '-preset', P['preset'], '-crf', P['crf'],
           '-pix_fmt', P['pix_fmt']]
    if P['pix_fmt'] == 'yuv444p':
        cmd += ['-profile:v', 'high444']
    return cmd + ['-x264-params', 'keyint=%d:min-keyint=%d' % (P['fps']*2, P['fps']),
                  '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709',
                  '-movflags', '+faststart', '-r', str(P['fps']), out]

def render_job(cfg, outdir, progress=None):
    """Render every frame and mux to mp4. Returns the output path."""
    P = plan(cfg)
    start_ms = job_start(cfg, P['span_hours']).timestamp()*1000
    frames = P['frames']
    out = os.path.join(outdir, 'lapse.mp4')

    want = cfg.get('workers')
    workers = int(want) if want else min(8, max(1, (os.cpu_count() or 1)))
    workers = max(1, min(workers, frames))

    proc = subprocess.Popen(_ffmpeg(P, out), stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    every = max(1, frames // 100)
    try:
        if workers == 1:
            _init(cfg, P, start_ms)
            for f in range(frames):
                proc.stdin.write(_frame_bytes(f))
                if progress and f % every == 0:
                    progress(f+1, frames)
        else:
            import multiprocessing as mp
            ctx = mp.get_context('fork' if 'fork' in mp.get_all_start_methods() else 'spawn')
            with ctx.Pool(workers, initializer=_init, initargs=(cfg, P, start_ms)) as pool:
                # imap keeps frame order; chunksize 1 keeps at most a few frames of
                # raw pixels in flight per worker rather than buffering the lapse.
                for f, raw in enumerate(pool.imap(_frame_bytes, range(frames), chunksize=1)):
                    proc.stdin.write(raw)
                    if progress and f % every == 0:
                        progress(f+1, frames)
        proc.stdin.close()
    except BrokenPipeError:
        raise RuntimeError('ffmpeg exited early: ' + proc.stderr.read().decode()[-400:])
    except Exception:
        proc.kill(); raise
    err = proc.stderr.read().decode()
    if proc.wait() != 0:
        raise RuntimeError('ffmpeg failed: ' + err[-400:])
    if progress:
        progress(frames, frames)
    return out
