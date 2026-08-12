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
```

`config` mirrors the browser's job snapshot:

```json
{
  "name": "Bridgeport, first light",
  "place_label": "Bridgeport, Connecticut",
  "seed":    { "date": "1989-03-25", "time": "03:17", "lat": 41.1865, "lon": -73.1952, "tz": -5 },
  "present": { "date": "2026-08-11", "time": "12:00", "lat": 41.1865, "lon": -73.1952, "tz": -4 },
  "anchor": "start",
  "frames": 144,
  "fps": 12,
  "print_seed": 19890325,
  "message": "WHAT WAS ALREADY TRUE",
  "mark": true
}
```

Omit `present` for a single-moment sweep.

The front end builds this shape in `toServiceConfig()` — the browser's internal job
snapshot is a different shape, and the mapping lives on the client so the server's
schema stays the one both sides are written against.

Every field is bounded before a job is created: `frames` 1–288, `fps` 1–30, latitude
±90, longitude ±180, UTC offset ±14, dates `YYYY-MM-DD`, times `HH:MM`, `anchor` one of
`calendar`/`start`/`centre`, `message` ≤ 240 characters. A bad field comes back as a 400
naming it. This is what stops a single request asking for a hundred thousand frames.

## Rate limiting

`POST /api/render` is limited per IP: **3 renders an hour and 10 a day** by default, set
by `RATE_PER_HOUR` and `RATE_PER_DAY` in `wrangler.jsonc`. Either can be set to `"0"` to
turn that window off. Over the limit returns 429 with a `Retry-After` header and a
message the front end shows verbatim; the browser then offers to render the job locally
instead, so being refused is never a dead end.

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

About 0.9s per frame on `standard-1`, so a 144-frame lapse is roughly 2–3 minutes of
CPU plus a few seconds of muxing. Four jobs run concurrently by default
(`max_concurrency` on the queue consumer); raise it and `max_instances` together.

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
