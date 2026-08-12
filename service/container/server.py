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

def work(job_id, cfg):
    try:
        with tempfile.TemporaryDirectory() as d:
            def progress(done, total):
                post_status(job_id, {"status": "rendering", "done": done, "total": total})
            out = render.render_job(cfg, d, progress)
            size = os.path.getsize(out)
            post_status(job_id, {"status": "uploading", "bytes": size})
            with open(out, "rb") as fh:
                r = requests.put(f"{CALLBACK_BASE}/api/internal/upload?jobId={job_id}",
                                 data=fh,
                                 headers={"x-render-secret": CALLBACK_SECRET,
                                          "content-type": "video/mp4"},
                                 timeout=600)
                r.raise_for_status()
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
