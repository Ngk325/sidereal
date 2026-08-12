"""Sidereal Ink — 24-hour lapse.
Seed moment fixed; present moment advances 10 minutes per frame across one full day.
Renders frames to PNG and muxes to MP4.

This is the small, readable illustration of the idea, and it stays that way: one file,
no dependencies past numpy and PIL, a whole day in 144 frames.

It is not what the service renders, and the difference is worth knowing before copying
anything out of here. Time below advances in whole minutes, which is fine at a
ten-minute step because every frame lands on a different minute. Shorten the step past
that and it stops working: the clock is the only thing driving the plate, so frames
inside the same minute come out byte-identical and the lapse plays as a series of jumps
rather than as motion. service/container/render.py carries the fix — the digit registers
still read the clock in whole minutes, but the sidereal angle reads the exact instant.
"""
import numpy as np, math, os
from PIL import Image, ImageDraw

# ── configuration ────────────────────────────────────────────────
SEED_M   = dict(Y=1989, M=3, D=25, h=3, m=17, lat=41.1865, lon=-73.1952, tz=-5)
PRES_DAY = dict(Y=2026, M=8, D=9,  lat=41.1865, lon=-73.1952, tz=-4)
SIZE, FRAMES, STEP_MIN = 1000, 144, 10
SEED = 19890325
OUT  = "/home/claude/frames"
GROUND, GOLD, BLUE, EMBER = (13,20,36), (217,164,65), (111,177,199), (217,119,87)

# ── time / sky ───────────────────────────────────────────────────
def julian_day(Y,M,D,hUT):
    if M <= 2: Y -= 1; M += 12
    A = Y//100; B = 2 - A + A//4
    return math.floor(365.25*(Y+4716)) + math.floor(30.6001*(M+1)) + D + B - 1524.5 + hUT/24

def lst_deg(Y,M,D,h,m,tz,lon):
    jd = julian_day(Y,M,D,h + m/60 - tz)
    T  = (jd - 2451545.0)/36525
    gmst = 280.46061837 + 360.98564736629*(jd-2451545.0) + 0.000387933*T*T - T**3/38710000
    return (gmst + lon) % 360

def digits(n): return [int(c) for c in str(abs(n))]
def reduce9(n):
    while n > 9: n = sum(digits(n))
    return n
def gcd(a,b): return math.gcd(a,b)
def lcm(a,b): return a*b//gcd(a,b)

def moment(Y,M,D,h,m,lat,lon,tz):
    total = sum(digits(Y))+sum(digits(M))+sum(digits(D))
    return dict(Y=Y,M=M,D=D,h=h,m=m,lat=lat,lon=lon,
        order=max(2,reduce9(sum(digits(Y)))), points=total, spokes=max(5,D),
        petals=max(3,sum(digits(h))+sum(digits(m))), rings=max(3,reduce9(D)),
        angle=lst_deg(Y,M,D,h,m,tz,lon))

def great_circle(la1,lo1,la2,lo2):
    r=math.pi/180; a,b,d=la1*r,la2*r,(lo2-lo1)*r
    return math.degrees(math.acos(max(-1,min(1,math.sin(a)*math.sin(b)+math.cos(a)*math.cos(b)*math.cos(d)))))

# ── smooth seeded field (stand-in for Perlin, vectorised) ────────
rng = np.random.default_rng(SEED)
PH  = rng.uniform(0, 2*np.pi, (6,3))
FR  = np.array([0.9,1.7,2.6,3.9,5.4,7.7])
def field(x, y):
    """x,y normalised to roughly [-1,1]; returns [0,1)."""
    v = np.zeros_like(x)
    for i in range(6):
        v += np.sin(FR[i]*x + PH[i,0]) * np.cos(FR[i]*y + PH[i,1]) / (i+1.4)
    return (v/2.1 + 0.5) % 1.0

# ── static structure, drawn with PIL ─────────────────────────────
def draw_structure(img, M, col, amt, extra, rng_local, cx, cy, R):
    d = ImageDraw.Draw(img, 'RGBA')
    off = math.radians(M['angle']) + extra
    # rings
    for k in range(M['rings']):
        t = (k+1)/M['rings']; rad = R*(0.20+0.80*t**0.85); rot = off*(k+1)/M['rings']
        for s in range(M['spokes']):
            if rng_local.random() > 0.60: continue
            a0 = rot + 2*math.pi*s/M['spokes']
            a1 = a0 + 2*math.pi/M['spokes']*rng_local.uniform(0.35,0.92)
            pts=[]
            a=a0
            while a <= a1:
                pts.append((cx+math.cos(a)*rad, cy+math.sin(a)*rad)); a += 0.02
            if len(pts)>1:
                d.line(pts, fill=col+(int(rng_local.uniform(30,110)*amt),), width=1)
    # chords
    N = M['points']
    pts = [(cx+math.cos(off+2*math.pi*i/N)*R, cy+math.sin(off+2*math.pi*i/N)*R) for i in range(N)]
    for si, st in enumerate([M['petals'], 2, M['order']]):
        alpha = int((62 if si==0 else 28)*amt)
        for i in range(N):
            d.line([pts[i], pts[(i*st)%N]], fill=col+(alpha,), width=1)
    for p in pts:
        d.ellipse([p[0]-1.4,p[1]-1.4,p[0]+1.4,p[1]+1.4], fill=col+(int(160*amt),))

def draw_nodes(img, beat, res, orders, mid_angle, cx, cy, R, glow=0.7):
    d = ImageDraw.Draw(img,'RGBA')
    n = min(beat,144); rr = R*(0.30+0.55*(res/max(orders)))
    for i in range(n):
        a = mid_angle + 2*math.pi*i/n
        x,y = cx+math.cos(a)*rr, cy+math.sin(a)*rr
        for g in range(6,0,-1):
            d.ellipse([x-g*1.3,y-g*1.3,x+g*1.3,y+g*1.3], fill=EMBER+(int(11*glow),))
        d.ellipse([x-1.3,y-1.3,x+1.3,y+1.3], fill=EMBER+(int(200*glow),))

# ── ink: vectorised walkers accumulated into a float buffer ──────
def ink(buf, M, colour, count, extra, dirn, weight, cx, cy, R, seed_off):
    r = np.random.default_rng(SEED + seed_off)
    order = M['order']
    v = r.integers(0, order, count)
    a0 = math.radians(M['angle']) + extra + 2*np.pi*v/order + r.normal(0,0.10,count)
    rr = R*r.uniform(0.08,1.0,count)
    x = cx + np.cos(a0)*rr; y = cy + np.sin(a0)*rr
    alive = np.ones(count, bool)
    tilt = math.radians(M['lat'])
    rot = np.array([[math.cos(2*np.pi*k/order), -math.sin(2*np.pi*k/order),
                     math.sin(2*np.pi*k/order),  math.cos(2*np.pi*k/order)] for k in range(order)])
    H, W = buf.shape[0], buf.shape[1]
    for step in range(150):
        n = field((x-cx)/R*1.6, (y-cy)/R*1.6)
        rad = np.arctan2(y-cy, x-cx)
        ang = n*2*np.pi*2 + dirn*(rad+np.pi/2) + np.sin(rad*order)*0.35 + tilt*0.25
        x = np.where(alive, x + np.cos(ang)*1.35, x)
        y = np.where(alive, y + np.sin(ang)*1.35, y)
        alive &= (np.hypot(x-cx, y-cy) < R*1.06)
        if not alive.any(): break
        dx, dy = (x-cx)[alive], (y-cy)[alive]
        for k in range(order):
            c,s = rot[k][0], rot[k][2]
            px = (cx + dx*c - dy*s).astype(np.int32)
            py = (cy + dx*s + dy*c).astype(np.int32)
            ok = (px>=0)&(px<W)&(py>=0)&(py<H)
            np.add.at(buf, (py[ok], px[ok]), np.array(colour, float)*weight)

# ── frame render ─────────────────────────────────────────────────
def render(frame_i):
    minutes = frame_i*STEP_MIN
    h, m = divmod(minutes, 60)
    A = moment(SEED_M['Y'],SEED_M['M'],SEED_M['D'],SEED_M['h'],SEED_M['m'],
               SEED_M['lat'],SEED_M['lon'],SEED_M['tz'])
    B = moment(PRES_DAY['Y'],PRES_DAY['M'],PRES_DAY['D'], h, m,
               PRES_DAY['lat'],PRES_DAY['lon'],PRES_DAY['tz'])
    arc = great_circle(A['lat'],A['lon'],B['lat'],B['lon'])
    extra = math.radians(arc)
    res = gcd(A['order'],B['order']); beat = lcm(A['order'],B['order'])

    cx = cy = SIZE/2; R = SIZE*0.40
    img = Image.new('RGB',(SIZE,SIZE), GROUND)
    # ground tooth + halo
    d = ImageDraw.Draw(img,'RGBA')
    for rad in range(int(R*1.5), 0, -8):
        d.ellipse([cx-rad,cy-rad,cx+rad,cy+rad], outline=(255,238,205, max(0,int(7*(1-rad/(R*1.5))))), width=6)
    rl = np.random.default_rng(SEED)
    draw_structure(img, A, GOLD, 1.0, 0.0, rl, cx, cy, R)
    draw_structure(img, B, BLUE, 0.85, extra, np.random.default_rng(SEED+7), cx, cy, R)
    draw_nodes(img, beat, res, (A['order'],B['order']),
               math.radians((A['angle']+B['angle'])/2)+extra/2, cx, cy, R)

    buf = np.zeros((SIZE,SIZE,3), np.float32)
    ink(buf, A, GOLD, 420, 0.0,   1, 0.055, cx, cy, R, 11)
    ink(buf, B, BLUE, 420, extra,-1, 0.050, cx, cy, R, 23)
    base = np.asarray(img, np.float32)
    out = np.clip(base + buf, 0, 255).astype(np.uint8)
    img = Image.fromarray(out)

    d = ImageDraw.Draw(img,'RGBA')
    for i in range(1,4):
        rr = R*0.05*i
        d.ellipse([cx-rr,cy-rr,cx+rr,cy+rr], outline=GOLD+(150-i*30,), width=1)
    d.ellipse([cx-3,cy-3,cx+3,cy+3], fill=EMBER)
    # timestamp
    sep = (B['angle']-A['angle']) % 360
    d.text((30, SIZE-46), f"{PRES_DAY['Y']}-{PRES_DAY['M']:02d}-{PRES_DAY['D']:02d}  {h:02d}:{m:02d}   "
                          f"LST {B['angle']:7.3f} deg   sep {sep:7.3f} deg",
           fill=(200,196,185,220))
    return img

if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    for i in range(FRAMES):
        render(i).save(f"{OUT}/f{i:04d}.png")
        if i % 24 == 0: print("frame", i, flush=True)
    print("done")
