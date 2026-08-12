# Sidereal Ink — working brief for Claude Code

Run this from the unzipped `sidereal-ink/` directory. Git is already initialised with
one commit on `main`; nothing has been pushed yet.

---

## Task 1 — push to GitHub

Remote: `https://github.com/Ngk325/sidereal.git`

```bash
git remote add origin https://github.com/Ngk325/sidereal.git
git push -u origin main
```

**Credentials:** do not ask the user to paste a token into the conversation, and do not
put one on a command line where it lands in shell history. If authentication is needed,
tell the user to run `gh auth login` themselves, or let git's own credential prompt
collect it. A token pasted into a chat transcript should be treated as compromised and
revoked, not used.

**If the push is rejected** as non-fast-forward, the remote was created with a README:

```bash
git pull --rebase origin main
git push -u origin main
```

Resolve any conflict in favour of the local files — they are complete and the remote
README is a stub.

**Then verify:** confirm the tree on GitHub matches `git ls-files` locally, and that
`docs/example.png` renders in the README.

---

## Project context

A generative artwork. Two implementations of the same mathematics:

- `site/index.html` — the whole browser piece, one file, p5.js from CDN, no build step
- `service/container/render.py` — server-side Python/PIL port, tested and working

**The invariant that matters:** these two must agree. The cipher is bit-exact across
both (FNV-1a key hash → mulberry32 keystream → base32). Sidereal time, digit reduction
and great-circle distance are identical. Ink dispersion differs because the PRNGs differ,
which is acceptable; the cipher and the arithmetic diverging is not.

**The rule of the piece:** every mark must be downstream of an input. No decoration that
isn't computed from the moments.

**Three registers, kept distinct in the UI and not to be blurred:**

1. Exact arithmetic — sidereal time, distances, digit sums. Verifiable.
2. A real cipher — genuinely reversible, genuinely keyed. A puzzle, not security (32-bit key).
3. An interpretive convention — the "transmission" indexes fixed word banks. Deterministic,
   but a scheme the piece defines, not a claim about reality. It must never borrow the
   authority of the first two.

---

## Task 2 — wire the front end to the render service

Currently the browser renders lapses locally. This blocks the UI thread because
JavaScript is single-threaded — moving work offscreen did not fix it and cannot.
The service in `service/` exists to move rendering server-side.

In `site/index.html`, replace the local queue with API submission:

- Add an **email** field beside the existing **name** field in the render card.
- `enqueue()` should `POST /api/render` with `{ email, config }`, where `config` matches
  the shape documented in `service/README.md` — note the server expects
  `seed`/`present` objects with `date`/`time`/`lat`/`lon`/`tz`, and `print_seed`,
  not the browser's internal `P.seed`. Write a mapping function; do not change the
  server's schema to match the client.
- Poll `GET /api/job/:id` every 3s while a job is `queued`/`rendering`/`uploading`.
  Drive the existing progress bar from `done`/`total`.
- On `done`, surface the `/f/:id` link in the job card and the render log.
- Keep the local PNG export and the local ZIP path working — they are the offline
  fallback when the service isn't deployed.
- **Degrade gracefully:** if `/api/render` 404s (static-only deployment), fall back to
  the existing in-browser queue rather than erroring. Detect once, cache the result.

Do not delete the local rendering code. It is the only path that works when the site is
served from `file://` or a static host.

---

## Task 3 — before the service is public

The service is unprotected. `POST /api/render` spends real CPU, so anyone who finds it
can run an open render farm on the owner's account.

- Add Cloudflare Turnstile to the submit endpoint, or a rate limit keyed by IP.
- `/f/:id` links are unlisted UUIDs, not private. If renders should be private, put
  Cloudflare Access in front of `/f/` or switch to signed URLs with an expiry.
- Confirm `RENDER_SECRET` and `RESEND_API_KEY` are set via `wrangler secret put` and
  appear nowhere in the repo.

---

## Deploying

Static site only:

```bash
cd site && npx wrangler deploy --assets . --name sidereal-ink-site
```

Full service — needs the Workers **paid** plan (Containers and Queues), Docker running
locally for the first image build, and a Resend account with a verified domain. Steps in
`service/README.md`. The Worker, queue wiring and email path are written against current
docs but **have not been run against live infrastructure** — expect to fix a binding name
or two on first deploy. The renderer itself is tested: about 107ms per frame at 1440px,
so a 60-second lapse (3600 frames at 60fps) is roughly 3–4 minutes on the four cores
`standard-4` provides. It needs those cores — see *Timing and cost* in
`service/README.md` before dropping the instance type.

**The lapse is specified as a duration, not a frame count**, and time runs continuously
through it. The digit registers still read the clock in whole minutes — they step,
because a clock reading is a discrete thing — but the sidereal angle reads the exact
instant. Passing only `h` and `m` to `moment()` quantises the whole plate to the minute,
which makes every frame inside a tick byte-identical and turns the lapse back into a
slideshow. Both implementations carry an optional `sec`; keep them in step.

---

## Notes on judgement

- `.gitignore` already excludes `.env`, `.wrangler/`, `__pycache__/` and rendered output.
  Keep it that way; do not commit generated `.mp4`, `.webm` or frame directories.
- The placeholder KV id and `MAIL_FROM` in `wrangler.jsonc` are meant to be committed.
- If asked to make the transmission sound more authoritative — more certain, more like a
  real reading — push back. The honest framing is what makes the piece defensible, and
  the decode table exists so a reader can audit which number chose which phrase.
