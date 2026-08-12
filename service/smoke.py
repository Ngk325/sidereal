#!/usr/bin/env python3
"""Post-deploy smoke test — walks the whole chain and names the link that broke.

    python3 service/smoke.py https://sidereal-ink.<subdomain>.workers.dev you@example.com

Submits a deliberately tiny render (6 frames, a few seconds of CPU), watches it
through the queue and the container, and downloads the result. Every step prints
what it expected and what it got, so a failure points at one component rather
than at "it didn't work".

Nothing here needs the repo — stdlib only, runs anywhere with Python 3.
"""
import json, sys, time, urllib.error, urllib.request

TIMEOUT = 20
POLL_SECONDS = 3
GIVE_UP_AFTER = 600          # 6 frames should take well under a minute once running


class Failed(Exception):
    def __init__(self, step, detail, likely):
        super().__init__(detail)
        self.step, self.detail, self.likely = step, detail, likely


def call(method, url, body=None, expect_json=True):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
            return r.status, dict(r.headers), (json.loads(raw) if expect_json and raw else raw)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, dict(e.headers), json.loads(raw)
        except Exception:
            return e.code, dict(e.headers), raw[:400]
    except Exception as e:
        raise Failed("network", f"{type(e).__name__}: {e}",
                     "the URL is wrong, or the Worker is not deployed at all")


def step(n, what):
    print(f"\n[{n}] {what}")


def main(base, email):
    base = base.rstrip("/")
    print(f"Smoke test against {base}")

    step(1, "the site itself is being served")
    code, hdr, body = call("GET", base + "/", expect_json=False)
    if code != 200:
        raise Failed("assets", f"GET / returned {code}",
                     "the ASSETS binding, or public/ was empty at deploy time")
    if b"Sidereal Ink" not in body:
        raise Failed("assets", "GET / did not contain 'Sidereal Ink'",
                     "public/ holds something other than site/index.html")
    print("    200, and it is the artwork")

    step(2, "the API is mounted (an empty body must be rejected, not 404'd)")
    code, hdr, body = call("POST", base + "/api/render", {})
    if code == 404:
        raise Failed("routing", "POST /api/render returned 404",
                     "a static-assets Worker is deployed over the service — check the "
                     "Worker name, this is exactly the collision the READMEs warn about")
    if code != 400:
        raise Failed("routing", f"expected 400, got {code}: {body}",
                     "the fetch handler is not the one in src/index.ts")
    print(f"    400 as expected — {body.get('error')}")

    step(3, "submitting a 6-frame render")
    config = {
        "name": "Smoke test",
        "place_label": "Bridgeport, Connecticut",
        "seed": {"date": "1989-03-25", "time": "03:17",
                 "lat": 41.1865, "lon": -73.1952, "tz": -5},
        "present": {"date": "2026-08-11", "time": "12:00",
                    "lat": 41.1865, "lon": -73.1952, "tz": -4},
        "anchor": "start", "frames": 6, "fps": 12,
        "print_seed": 19890325, "message": "SMOKE TEST", "mark": True,
    }
    code, hdr, body = call("POST", base + "/api/render", {"email": email, "config": config})
    if code == 429:
        raise Failed("rate limit", body.get("error", "429"),
                     "not a bug — you are over your own limit. Wait, or raise "
                     "RATE_PER_HOUR in wrangler.jsonc and redeploy")
    if code != 200:
        raise Failed("submit", f"{code}: {body}",
                     "KV or the queue producer binding — check JOBS has a real id in "
                     "wrangler.jsonc and that the queue exists")
    job_id = body.get("jobId")
    if not job_id:
        raise Failed("submit", f"no jobId in {body}", "the response shape changed")
    print(f"    accepted as {job_id}")

    step(4, "watching it through the queue and the container")
    started = time.time()
    last, stuck_at_queued = None, 0
    while True:
        if time.time() - started > GIVE_UP_AFTER:
            raise Failed("timeout", f"still {last} after {GIVE_UP_AFTER}s", {
                "queued": "the queue consumer never picked it up, or the container "
                          "refused the dispatch — CALLBACK_SECRET inside the container "
                          "must equal RENDER_SECRET",
                "rendering": "the container is rendering but slowly, or its status "
                             "callbacks are not arriving — check CALLBACK_BASE",
                "uploading": "the container cannot PUT to /api/internal/upload — check "
                             "CALLBACK_BASE and the R2 binding",
            }.get(last, "unknown"))
        code, hdr, body = call("GET", f"{base}/api/job/{job_id}")
        if code != 200:
            raise Failed("poll", f"{code}: {body}", "the job vanished from KV")
        st = body.get("status")
        if st != last:
            counts = body.get("total") and body.get("done") is not None
            print(f"    {st}" + (f"  {body['done']}/{body['total']}" if counts else ""))
            last = st
        if "email" in body:
            raise Failed("privacy", "GET /api/job/:id echoed the email address back",
                         "the destructuring that strips it was removed")
        if st == "done":
            break
        if st == "failed":
            raise Failed("render", body.get("error", "no error given"),
                         "the container itself — read `wrangler tail` for the traceback")
        if st == "queued":
            stuck_at_queued += 1
            if stuck_at_queued == 20:
                print("    (still queued after a minute — likely the container dispatch)")
        time.sleep(POLL_SECONDS)

    step(5, "downloading the file")
    code, hdr, body = call("GET", f"{base}/f/{job_id}", expect_json=False)
    if code != 200:
        raise Failed("download", f"GET /f/{job_id} returned {code}",
                     "the R2 binding, or the upload callback never landed")
    ctype = hdr.get("Content-Type", hdr.get("content-type", ""))
    if "video/mp4" not in ctype:
        raise Failed("download", f"content-type was {ctype!r}", "R2 stored the wrong thing")
    if len(body) < 10000:
        raise Failed("download", f"only {len(body)} bytes", "a truncated upload")
    print(f"    {len(body)/1048576:.1f} MB of video/mp4")
    print(f"    disposition: {hdr.get('Content-Disposition', hdr.get('content-disposition'))}")

    print("\nEvery link in the chain works.")
    print(f"The email to {email} is the one thing this cannot check — if it did not")
    print("arrive, the problem is Resend: the API key, or SPF/DKIM on the sending domain.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    try:
        sys.exit(main(sys.argv[1], sys.argv[2]))
    except Failed as f:
        print(f"\n  FAILED at: {f.step}")
        print(f"  {f.detail}")
        print(f"  most likely: {f.likely}")
        sys.exit(1)
