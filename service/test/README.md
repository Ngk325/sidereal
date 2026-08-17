# Tests

Four suites, no framework, no build step beyond one `tsc` call the worker test
makes for itself.

## The worker's guards

```bash
cd service/worker && npm install
cd ../test && node worker.test.mjs
```

Covers `src/limits.ts`: the config bounds that stop one request asking for hours
of container CPU, and the IP rate limit. Compiles the shipped source rather than
testing a copy of it.

## The front end, across every deployment it has to survive

```bash
npm install --no-save playwright && npx playwright install chromium
cd service/test && node frontend.test.mjs
```

Serves `site/index.html` against `stub.mjs` — a stand-in Worker faithful to the
contract in `../worker/src/index.ts` — and drives a real browser through four
realities:

- **static host, no API.** The email field stays hidden, nothing is submitted,
  and the in-browser renderer produces a file exactly as before.
- **service available.** The submitted body is checked field by field against the
  server's schema: `date`/`time`/`lat`/`lon`/`tz` objects, a flat `print_seed`,
  `present` omitted when there is no present moment, and none of the browser's
  internal keys leaking through. Then the progress bar is checked against the
  server's `done`/`total`, and the `/f/:id` link against the card and the log.
- **service refuses the job.** The server's own message reaches the visitor, and
  the browser renderer is one click away.
- **ZIP format while the service is up.** Stays local, because the service emits
  mp4.

It skips itself with a note if playwright isn't installed, so it is safe to run
in a bare checkout.

The page loads p5 and JSZip from a CDN, so this needs network access. To run
offline, drop `p5.min.js` and `jszip.min.js` beside these files — they are
gitignored — and they will be served from disk instead.

## The length that adapts to the deployment

```bash
cd service/test && node adapt.test.mjs
```

The default length is a full minute at 60fps — 3600 frames, which is right when a
server draws them and a long wait when a browser tab does. So a page that probes
and finds no service steps its default down to ten seconds.

Three things have to hold, and only the first is the feature: it steps down when
there is no API; it leaves a length the visitor picked by hand alone, even across
a re-probe; and it does not touch the value at all when the service is up. The
second is the one worth keeping a test for — an adaptive default that overrides a
deliberate choice is a bug, not a convenience.

Same playwright and `CHROMIUM_PATH` arrangement as above.

## A server render surviving the window closing

```bash
cd service/test && node persistence.test.mjs
```

The promise of the service is that you queue work and walk away. The render always
did carry on — it runs on the server — but the queue lived in memory, so the page
came back knowing nothing about a job it had submitted minutes earlier. The job
ids are in `localStorage` now.

This one actually closes the page and opens a new one against the same origin, so
it exercises the real thing rather than a simulated reload: submit, close, reopen,
confirm the job is picked back up and polled through to done, close and reopen
again, confirm a *finished* render still restores with its download link, and that
clearing it forgets it for good.

Two of its checks are privacy, not features — the plaintext message and the email
address must not be in `localStorage`. The message is the one thing on that page
someone might not want left in browser storage.

## After a deploy

`../smoke.py` walks the live service end to end — submits a 6-frame render,
follows it through the queue and the container, downloads the result — and names
the component that broke rather than just failing.

```bash
python3 service/smoke.py https://sidereal-ink.<subdomain>.workers.dev you@example.com
```

## The invariant

`../parity/` is the one that matters most: the cipher and the arithmetic agreeing
bit for bit between `site/index.html` and `service/container/render.py`.
