# Sidereal Ink — hosted render service

Renders lapses on Cloudflare's servers instead of in the browser, stores them in R2,
and emails a download link when each one finishes. Close the tab; the render carries on.

```
browser ──POST /api/render──▶ Worker ──▶ Queue ──▶ Container (python + ffmpeg)
                               │                        │
                               │◀── status callbacks ───┤
                               │◀── mp4 upload ─────────┘
                               ├──▶ R2 (the file)
                               └──▶ Resend (the email)
```

## What you need

- A Cloudflare account with Workers **paid** plan — Containers require it (~$5/mo plus usage). Containers bill active CPU only, so idle costs nothing. The dashboard is blunt about it: the Containers page shows nothing but *"Containers is included in the Workers Paid plan"* and a purchase button until you upgrade. Everything else in this stack — KV, R2, the queue — can be provisioned before you decide.
- **R2 enabled on the account.** It is off by default and cannot be turned on from the API — Dashboard → R2 → enable. Without it the renders have nowhere to land.
- Docker running locally for the first deploy (Wrangler builds and pushes the image).
- A Resend account with a verified sending domain.

## Deploy

```bash
cd worker
npm install

# storage — on the account this was set up for, all three already exist and the KV id
# is in wrangler.jsonc. On a different account, create them first:
#   npx wrangler kv namespace create JOBS       # then paste the id into wrangler.jsonc
#   npx wrangler r2 bucket create sidereal-renders
#   npx wrangler queues create sidereal-renders

# secrets
npx wrangler secret put RENDER_SECRET          # any long random string
npx wrangler secret put RESEND_API_KEY         # from resend.com

# set MAIL_FROM in wrangler.jsonc to an address on your verified domain
mkdir -p public && cp ../../site/index.html public/    # the front end
npx wrangler deploy
```

`public/` is a build artifact — the site has exactly one source of truth, and it is
`site/index.html`. It is gitignored so the two copies cannot drift.

**This Worker owns the name `sidereal-ink`.** It serves the artwork *and* the API from
one deployment. The static-only deploy described in the root README uses
`sidereal-ink-site`, deliberately: deploying a static-assets Worker over this one would
replace the API with nothing, and the front end would quietly fall back to rendering in
the browser rather than showing an error. Keep the two names apart.

The container needs to know where to call back. After the first deploy, set it:

```bash
npx wrangler secret put CALLBACK_BASE          # https://sidereal-ink.<subdomain>.workers.dev
```

then deploy once more so the container picks it up. The `Renderer` class passes
`CALLBACK_BASE` and `CALLBACK_SECRET` into the container itself, setting
`CALLBACK_SECRET` from `RENDER_SECRET` — one shared secret carrying a different name
on each side of the wire. There is nothing further to wire by hand.

Until `CALLBACK_BASE` is set the container renders and then has nowhere to send the
result, so jobs stall. It is the one step that cannot be done before the first deploy,
because the URL does not exist yet.

## API

```
POST /api/render     { email, config }        → { jobId }
GET  /api/job/:id                             → { status, done, total, bytes }
GET  /f/:id                                   → the mp4

internal, container→Worker, all behind x-render-secret:
POST /api/internal/status                     → progress and terminal state
PUT  /api/internal/upload?jobId               → the whole file, when it is small
POST /api/internal/upload/start?jobId         → { uploadId, partSize }
PUT  /api/internal/upload/part?jobId&uploadId&part
POST /api/internal/upload/finish?jobId&uploadId  { parts }
POST /api/internal/upload/abort?jobId&uploadId
```

`config` mirrors the browser's job snapshot:

```json
{
  "name": "Bridgeport, first light",
  "place_label": "Bridgeport, Connecticut",
  "seed":    { "date": "1989-03-25", "time": "03:17", "lat": 41.1865, "lon": -73.1952, "tz": -5 },
  "present": { "date": "2026-08-11", "time": "12:00", "lat": 41.1865, "lon": -73.1952, "tz": -4 },
  "anchor": "centre",
  "duration": 60,
  "fps": 60,
  "span_hours": 12,
  "size": 1440,
  "quality": "web",
  "print_seed": 19890325,
  "message": "WHAT WAS ALREADY TRUE",
  "mark": true
}
```

Omit `present` for a single-moment sweep.

A lapse is specified the way it is watched — a `duration` in seconds and an `fps` —
and the container multiplies them into a frame count. `frames` still works and still
wins when given (the smoke test asks for six), but sending both is a 400: they are two
ways of saying the same thing and silently ignoring one hides a mistake.

`span_hours` is how much sky the video sweeps, not how long it plays. The default 12
puts the moment at the middle of ±6 hours, which is the calmest motion; 24 covers the
whole day and moves through it twice as fast.

`quality` is `web` (yuv420p, plays anywhere) or `master` (yuv444p, full chroma, which
Safari and most hardware decoders refuse). See **Timing and cost** for the measurements
behind that choice. `crf`, `preset` and `pix_fmt` override it directly if you want
something else.

The front end builds this shape in `toServiceConfig()` — the browser's internal job
snapshot is a different shape, and the mapping lives on the client so the server's
schema stays the one both sides are written against.

Every field is bounded before a job is created: `duration` 0–60s, `fps` 1–60, `frames`
1–3600, `size` 240–2160 and even, `span_hours` 0–48, latitude ±90, longitude ±180, UTC
offset ±14, dates `YYYY-MM-DD`, times `HH:MM`, `anchor` one of `calendar`/`start`/`centre`,
`quality` one of `web`/`master`, `message` ≤ 240 characters. A bad field comes back as a
400 naming it.

Those single-field caps are not the real ceiling. 3600 frames at 1440px is the intended
job; 3600 frames at 2160px is twice the work and no more suspicious-looking, so what is
actually bounded is **frames × megapixels** (`MAX_WORK` in `limits.ts`). This is what
stops one request asking for hours of CPU.

## Rate limiting

`POST /api/render` is limited per IP: **3 renders an hour and 10 a day** by default, set
by `RATE_PER_HOUR` and `RATE_PER_DAY` in `wrangler.jsonc`. Either can be set to `"0"` to
turn that window off. Over the limit returns 429 with a `Retry-After` header and a
message the front end shows verbatim; the browser then offers to render the job locally
instead, so being refused is never a dead end.

**Those numbers were set against a much smaller job.** A 144-frame lapse was about one
vCPU-minute; the 60-second default is roughly ten. The per-IP ceiling did not change, so
the CPU one address can spend in a day went up with it — on the order of $0.10–0.15 of
container CPU per IP per day at the default limits, and more if you raise them. That is
a decision for whoever owns the account, so nothing here was lowered on your behalf:
look at `RATE_PER_HOUR` and `RATE_PER_DAY` before pointing anyone at this.

Counters live in the `JOBS` KV namespace under an `rl:` prefix, so there is no extra
binding to create. **KV is eventually consistent**, which makes this a soft ceiling: a
burst of simultaneous requests can slip a few past the limit. It closes the open render
farm; it is not a hard quota. If you need one, move the counter to a Durable Object.

Counting happens only when a job is actually created — validation failures are free, so
the front end can probe for the service without spending anyone's quota.

## Checking it

Before deploying:

```bash
cd worker && npm test && npm run typecheck
cd ../test && node frontend.test.mjs        # needs playwright
cd ../parity && node browser-side.js ../../site/index.html > /tmp/b.json \
             && python3 server-side.py ../container/render.py /tmp/b.json
```

After deploying:

```bash
python3 ../smoke.py https://sidereal-ink.<subdomain>.workers.dev you@example.com
```

`smoke.py` submits a 6-frame render, follows it through the queue and the container,
downloads the result, and names the component that broke rather than just failing.
See [`test/README.md`](test/README.md).

## Timing and cost

A 60-second lapse is 3600 frames. Measured on a 4-core box: **107 ms per frame**, and
**3.6 minutes wall clock** for the whole thing including the encode, which runs
concurrently with the rendering and takes roughly half the CPU.

Three things carry that, and they are worth knowing before changing any of them:

- **The seed layer is drawn once, not 3600 times.** When a fixed seed is held against
  a sweeping present, the seed's own layer and its ink are identical on every frame —
  and the seed usually carries the higher rotational order, so its ink is the expensive
  one. Hoisting it into a per-job backdrop removed about two thirds of the frame cost.
- **The ink counts hits into one histogram** and multiplies by the colour once, instead
  of scattering colour into the image with `np.ufunc.at` for every step of every
  particle.
- **Frames never touch the disk.** They are piped raw into ffmpeg. 3600 PNGs at 1440px
  would have been several gigabytes and a PNG encode per frame, to hand ffmpeg pixels
  it already had.

A **single-moment sweep** — one with no `present` — gets none of the first of those,
because the moment being drawn is the one that moves. Nothing is frame-invariant, so it
runs around three times slower: budget closer to 10 minutes for a full 60 seconds. It is
also the more active picture, since the whole plate turns rather than just the blue
layer.

`instance_type` is `standard-4` (4 vCPU). This matters more than it looks: `standard-1`
is **half** a vCPU, and the renderer splits frames across every core it is given, so
half a core turns a ~4 minute render into something over half an hour. Containers bill
active CPU, so the wide instance costs little more for the same work — it just finishes
sooner. Four jobs run concurrently by default (`max_concurrency` on the queue consumer);
raise it and `max_instances` together.

### Why `web` is 4:2:0 and what `master` buys

PSNR of the encoded file against the raw frames, measured on this content:

| pix_fmt | crf | size (60s) | PSNR |
| ------- | --- | ---------- | ---- |
| yuv420p | 14  | 204 MB     | 35.83 dB |
| yuv420p | 18  | 118 MB     | 35.76 dB |
| yuv420p | 23  | 52 MB      | 35.45 dB |
| yuv444p | 16  | 157 MB     | 38.00 dB |

4:2:0 sits on a hard ceiling around 35.8 dB and no amount of bitrate moves it — the
loss is the chroma downsample, not the quantiser, and the plate is one-pixel gold lines
on near-black, which is the content that survives it worst. So `web` spends nothing on
crf 14: crf 18 is within 0.07 dB of the ceiling at half the size. `master` spends the
same bytes on full chroma instead and gains a real 2.2 dB, at the cost of High 4:4:4
Predictive — which Safari, iOS and most hardware decoders will not play. That is the
whole trade, and it is why the default is the one that opens on a phone.

### The upload is chunked, and has to be

Cloudflare caps a **request body** by account plan — 100 MB on Free and Pro — and the
container's upload arrives over the public edge like any other request. A 60-second
1440p60 lapse is about 110 MB, which is past it. So anything over 64 MB goes up as an
R2 multipart upload: several requests of 24 MB each, assembled into one object by
`/api/internal/upload/finish`. No new credentials — the same shared secret
authenticates every leg — and a failure aborts the upload so half-stored parts stop
billing. Small renders still take the one-shot `PUT`.

## Things worth knowing before you rely on it

- **The download link is unlisted, not private.** Anyone with the UUID can fetch the
  file for 30 days. If these should be private, put Cloudflare Access in front of `/f/`
  or swap to signed URLs with an expiry.
- **Email deliverability depends on your domain**, not on this code. Verify SPF/DKIM in
  Resend or the mail lands in spam.
- **The IP rate limit is a soft ceiling, not a quota.** See above. It also does nothing
  about anyone willing to change IP. If the piece gets attention, add Cloudflare
  Turnstile to the submit endpoint on top of it.
- **The email address is stored with the job for 30 days** and is not echoed back by
  `GET /api/job/:id`. Nothing else is collected.
- **The Python renderer is a faithful port, not the identical binary.** The cipher,
  sidereal math and digit arithmetic match the browser exactly (verified against the same
  inputs); the ink dispersion uses a different PRNG, so the same seed gives the same
  structure with slightly different ink. If you need pixel-identical output to the
  browser, render both from this service.
- **I have not deployed this stack.** The renderer is tested and produces correct frames,
  the config the browser sends has been rendered end-to-end, and the validation and rate
  limiting have unit tests — but the Worker, queue wiring and email path have still never
  run against live infrastructure. Expect to fix something on first deploy. The two most
  likely candidates are the container env vars (`CALLBACK_BASE` must be set and then
  redeployed) and the queue consumer binding.
