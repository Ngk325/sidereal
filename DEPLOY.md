# Deploying the render service

Everything in `service/` is written and tested. None of it has ever run against live
infrastructure. This is the list of what remains, in order, written so either you or
a fresh Claude Code session can pick it up cold.

The one thing that has blocked it all along is credentials: `wrangler` needs a
Cloudflare API token, and there is no way to get one into a cloud session without
the setup in [Step 1](#step-1--let-a-cloud-session-reach-cloudflare). If you are
deploying from your own machine, skip straight to [Step 3](#step-3--deploy).

---

## Where things stand

**Live now.** `https://ngk325.github.io/sidereal/` — the whole artwork, published
from `site/` by `.github/workflows/pages.yml` on every push to main. Figure, cipher,
decode table, PNG export, and lapses rendered in the browser.

**Not live.** The render service. Which means, today, every lapse renders in the
visitor's tab: minutes of work on the one thread the page is also drawing with, and
it stops when the tab is backgrounded, because browsers clamp timers in hidden tabs
and `MediaRecorder` needs the tab visible. That is not a bug to be fixed in the
front end — it is why `service/` exists.

**Already provisioned on the Cloudflare account**, checked and confirmed:

- R2 bucket `sidereal-renders` — so R2 is switched on, which is the one setup step
  that cannot be done from the API
- KV namespace `sidereal-ink-JOBS`, id `499c4007141b4be0b2446aa37fe88cb3`, which is
  already the id in `wrangler.jsonc`

**Not confirmed, and both will stop the deploy dead:**

- Whether the queue `sidereal-renders` exists
- **Whether the account is on the Workers paid plan.** Containers and Queues both
  require it. No token can work around this one.

---

## Step 1 — let a cloud session reach Cloudflare

Skip this if you are deploying from your own machine.

At **claude.ai/code**, open the environment settings and edit the environment the
session will run in. Two fields matter, and missing either produces a failure that
looks like something else.

### Environment variables

```
CLOUDFLARE_API_TOKEN=...
CLOUDFLARE_ACCOUNT_ID=...
```

**There is no secrets store.** Cloud environment variables live in the environment
configuration, readable by anyone who uses that environment, and the documentation
says plainly not to put credentials there. For a personal environment that is
effectively just you — but it is stored config, not a vault, and every future
session in that environment can read it.

So scope the token rather than skip it. At
**dash.cloudflare.com/profile/api-tokens** → Create Custom Token:

| Scope   | Permission              | Level |
| ------- | ----------------------- | ----- |
| Account | Workers Scripts         | Edit  |
| Account | Workers KV Storage      | Edit  |
| Account | Workers R2 Storage      | Edit  |
| Account | Queues                  | Edit  |
| Account | Cloudflare Containers   | Edit  |
| Account | Account Settings        | Read  |
| User    | User Details            | Read  |

Give it a **TTL of a few days**. It has to survive one deploy, not a year. A
short-lived scoped token sitting in readable config is a much smaller problem than
a long-lived global one, and it expires itself if you get distracted.

Sessions copy these in **once, at startup**. An already-running session will never
see them; start a new one.

### Network access

The default **Trusted** level is an allowlist, and `api.cloudflare.com` is not on
it. The only Cloudflare entry in the default list is `production.cloudflare.docker.com`,
a Docker mirror. Leave this alone and `wrangler deploy` fails at its first API call,
which reads like a bad token rather than a blocked domain.

Set **Network access → Custom**, tick *"Also include default list of common package
managers"*, and add:

```
api.cloudflare.com
*.cloudflare.com
*.workers.dev
```

`*.cloudflare.com` covers the container registry and dashboard endpoints.
`*.workers.dev` is what the smoke test needs afterwards. **Full** works too if you
would rather not enumerate.

---

## Step 2 — the things Cloudflare needs that code cannot supply

- **Workers paid plan.** Containers and Queues both require it. The Containers page
  in the dashboard shows nothing but a purchase button until you upgrade.
- **R2 enabled.** Already done on this account.
- **A queue named `sidereal-renders`**, if it does not exist:
  `npx wrangler queues create sidereal-renders`
- **Docker running**, for the first image build only. Wrangler builds and pushes the
  container image itself.
- **A Resend account with a verified sending domain.** `MAIL_FROM` in
  `wrangler.jsonc` is currently `renders@insuranceprosct.com`, because that is the
  only domain verified for sending. If the piece should have a sender of its own,
  verify that domain in Resend and change both together — an address on an
  unverified domain fails every send.

---

## Step 3 — deploy

```bash
cd service/worker
npm install

# public/ is a build artifact: the site has one source of truth, site/index.html.
# It is gitignored so the two copies cannot drift.
mkdir -p public && cp ../../site/index.html public/

npx wrangler secret put RENDER_SECRET      # any long random string
npx wrangler secret put RESEND_API_KEY     # from resend.com

npx wrangler deploy
```

`wrangler secret put` prompts for the value. It never lands in shell history or in
a transcript, which is the point — do not pass these on the command line.

Then the one step that cannot be done in advance, because the URL does not exist
until the first deploy has happened:

```bash
npx wrangler secret put CALLBACK_BASE      # https://sidereal-ink.<subdomain>.workers.dev
npx wrangler deploy                        # again, so the container picks it up
```

Until `CALLBACK_BASE` is set, the container renders correctly and then has nowhere
to send the result, so jobs sit at `queued` forever. If that is the symptom, this is
the cause.

---

## Step 4 — prove it works

```bash
python3 service/smoke.py https://sidereal-ink.<subdomain>.workers.dev you@example.com
```

It submits a deliberately tiny render — six frames — follows it through the queue
and the container, downloads the result, and names the component that broke rather
than just failing. The email is the one link in the chain it cannot check; if that
does not arrive, the problem is Resend, not this code.

Then load the site itself. The render card should grow an email field, and the log
should say `render service found`. That is the front end's own probe agreeing.

---

## Traps

**The Worker name.** This deployment owns `sidereal-ink` and serves the artwork
*and* the API from one Worker. The static-only deploy in `PUSH.md` uses
`sidereal-ink-site`, deliberately. Deploying a static-assets Worker over this one
replaces the API with nothing, and the front end falls back to browser rendering
without showing an error — so it looks like it works, and quietly doesn't.

**`instance_type` is `standard-4`, and it needs to be.** `standard-1` is *half* a
vCPU. The renderer splits 3600 frames across every core it is given, so half a core
turns a four-minute render into something over half an hour. Containers bill active
CPU, so the wide instance costs little more for the same work — it finishes sooner.

**The upload is chunked, and has to be.** Cloudflare caps a request body at 100 MB
on Free and Pro accounts, and the container's upload arrives over the public edge
like any other request. A 60-second lapse is around 110 MB, and a single-moment
sweep — where the whole plate rotates, so the encoder has nothing to predict — runs
to 280 MB. Anything over 64 MB goes up as an R2 multipart upload in 24 MB parts.
This is already implemented; it is here so nobody "simplifies" it back.

**The rate limits were set against a much smaller job.** `RATE_PER_HOUR` is 3 and
`RATE_PER_DAY` is 10, chosen when a lapse was 144 frames and about one vCPU-minute.
The 60-second default is roughly ten. That is on the order of $0.10–0.15 of
container CPU per IP per day at current settings, and more if you raise them. Look
at these before pointing anyone at the service.

**`/f/:id` links are unlisted, not private.** Anyone with the UUID can fetch the
file for 30 days. If renders should be private, put Cloudflare Access in front of
`/f/` or move to signed URLs with an expiry.

---

## Expect to fix something

The renderer is tested and the front end is tested against a stub faithful to the
Worker's contract. The Worker, the queue wiring and the email path have never run
against live infrastructure. The two most likely first failures are `CALLBACK_BASE`
not being set and redeployed, and the queue consumer binding.

Run the checks before deploying — they are fast and they have caught real problems:

```bash
cd service/worker && npm test && npm run typecheck
cd ../test && node worker.test.mjs
CHROMIUM_PATH=... node frontend.test.mjs      # and adapt, persistence
cd ../parity && node browser-side.js ../../site/index.html > /tmp/b.json \
             && python3 server-side.py ../container/render.py /tmp/b.json
```

The parity check is the one that matters most if anything in `render.py` or the
sky-math in `site/index.html` has been touched: it holds the browser and the server
to the same arithmetic and the same cipher, which is the invariant the whole piece
rests on.
