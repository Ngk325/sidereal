"""Render service. Receives a job, renders it, uploads the mp4 back to the Worker.

The Worker owns R2 and email; this container only knows how to draw.
"""
import os, tempfile, threading, traceback, requests
from flask import Flask, request, jsonify
import render

app = Flask(__name__)
CALLBACK_SECRET = os.environ.get("CALLBACK_SECRET", "")
CALLBACK_BASE = os.environ.get("CALLBACK_BASE", "")   # e.g. https://sidereal-ink.example.workers.dev

def post_status(job_id, payload):
    if not CALLBACK_BASE:
        return
    try:
        requests.post(f"{CALLBACK_BASE}/api/internal/status",
                      json={"jobId": job_id, **payload},
                      headers={"x-render-secret": CALLBACK_SECRET}, timeout=15)
    except Exception:
        traceback.print_exc()

# Cloudflare caps a request body at 100 MB on Free and Pro accounts, and the PUT
# below arrives over the public edge like any other request. A minute of 1440p60 is
# well past that, so anything big goes up in parts and R2 reassembles it. Small
# renders — the smoke test's six frames — still take the one-shot path.
SINGLE_SHOT_MAX = 64 * 1024 * 1024

def _auth(extra=None):
    h = {"x-render-secret": CALLBACK_SECRET}
    if extra:
        h.update(extra)
    return h

def upload(job_id, path):
    size = os.path.getsize(path)
    if size <= SINGLE_SHOT_MAX:
        with open(path, "rb") as fh:
            r = requests.put(f"{CALLBACK_BASE}/api/internal/upload?jobId={job_id}",
                             data=fh, headers=_auth({"content-type": "video/mp4"}),
                             timeout=600)
        r.raise_for_status()
        return

    r = requests.post(f"{CALLBACK_BASE}/api/internal/upload/start?jobId={job_id}",
                      headers=_auth(), timeout=60)
    r.raise_for_status()
    started = r.json()
    upload_id, part_size = started["uploadId"], int(started["partSize"])
    qs = f"jobId={job_id}&uploadId={upload_id}"
    try:
        parts = []
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(part_size)
                if not chunk:
                    break
                n = len(parts) + 1
                r = requests.put(f"{CALLBACK_BASE}/api/internal/upload/part?{qs}&part={n}",
                                 data=chunk,
                                 headers=_auth({"content-type": "application/octet-stream"}),
                                 timeout=600)
                r.raise_for_status()
                parts.append(r.json())
        r = requests.post(f"{CALLBACK_BASE}/api/internal/upload/finish?{qs}",
                          json={"parts": parts}, headers=_auth(), timeout=120)
        r.raise_for_status()
    except Exception:
        # Parts already stored keep billing until the upload is completed or abandoned.
        try:
            requests.post(f"{CALLBACK_BASE}/api/internal/upload/abort?{qs}",
                          headers=_auth(), timeout=60)
        except Exception:
            traceback.print_exc()
        raise

def work(job_id, cfg):
    try:
        with tempfile.TemporaryDirectory() as d:
            def progress(done, total):
                post_status(job_id, {"status": "rendering", "done": done, "total": total})
            out = render.render_job(cfg, d, progress)
            post_status(job_id, {"status": "uploading", "bytes": os.path.getsize(out)})
            upload(job_id, out)
        post_status(job_id, {"status": "done"})
    except Exception as e:
        traceback.print_exc()
        post_status(job_id, {"status": "failed", "error": str(e)[:400]})

@app.post("/render")
def start():
    if request.headers.get("x-render-secret") != CALLBACK_SECRET:
        return jsonify(error="unauthorized"), 401
    body = request.get_json(force=True)
    job_id, cfg = body["jobId"], body["config"]
    threading.Thread(target=work, args=(job_id, cfg), daemon=True).start()
    return jsonify(accepted=True, jobId=job_id)

@app.get("/health")
def health():
    return jsonify(ok=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
