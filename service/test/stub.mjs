// A stand-in for the Worker, faithful to src/index.ts's contract, so the front
// end can be exercised without Cloudflare. The mode picks which reality to test:
//
//   static       no /api route at all — a static host, or GitHub Pages
//   live         the service is up and jobs run through to done
//   ratelimited  the service is up and refuses this one
import http from 'http';
import fs from 'fs';

import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
const SITE = join(dirname(fileURLToPath(import.meta.url)), '../../site/index.html');
export function makeServer(mode) {
  const submissions = [];
  const jobs = new Map();
  const srv = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://x');
    const send = (code, obj, headers = {}) => {
      res.writeHead(code, { 'content-type': 'application/json', ...headers });
      res.end(JSON.stringify(obj));
    };

    if (url.pathname === '/api/render' && req.method === 'POST') {
      if (mode === 'static') { res.writeHead(404); return res.end('Not found'); }
      let raw = ''; for await (const c of req) raw += c;
      let body = null; try { body = JSON.parse(raw); } catch {}
      // validation first — exactly as the Worker orders it, so the probe is free
      if (!body?.email || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(body.email))
        return send(400, { error: 'a valid email address is required' });
      if (!body?.config?.seed) return send(400, { error: 'config.seed is required' });
      if (mode === 'ratelimited')
        return send(429, { error: 'render limit reached — 3 per hour. Try again in 41 minutes, or render in your browser instead.' }, { 'retry-after': '2460' });
      const id = 'job-' + (submissions.length + 1) + '-uuid';
      submissions.push(body);
      jobs.set(id, { polls: 0 });
      return send(200, { jobId: id, status: 'queued' });
    }

    if (url.pathname.startsWith('/api/job/')) {
      if (mode === 'static') { res.writeHead(404); return res.end('Not found'); }
      const id = url.pathname.split('/').pop();
      const j = jobs.get(id);
      if (!j) return send(404, { error: 'not found' });
      const n = j.polls++;
      if (n === 0) return send(200, { id, status: 'queued', total: 3600 });
      if (n < 4) return send(200, { id, status: 'rendering', done: n * 900, total: 3600 });
      if (n === 4) return send(200, { id, status: 'uploading', done: 3600, total: 3600, bytes: 4194304 });
      return send(200, { id, status: 'done', done: 3600, total: 3600, bytes: 4194304 });
    }

    if (url.pathname.startsWith('/f/')) {
      res.writeHead(200, { 'content-type': 'video/mp4', 'content-disposition': 'attachment; filename="x.mp4"' });
      return res.end('not really an mp4');
    }

    if (url.pathname === '/' || url.pathname === '/index.html') {
      res.writeHead(200, { 'content-type': 'text/html' });
      return res.end(fs.readFileSync(SITE, 'utf8'));
    }
    res.writeHead(404); res.end('Not found');
  });
  srv.submissions = submissions;
  return srv;
}
