import { Container, getContainer } from "@cloudflare/containers";
import { validateConfig, overLimit, countAgainstLimit, humanise } from "./limits";

export class Renderer extends Container<Env> {
  defaultPort = 8080;
  sleepAfter = "10m";              // idle containers shut down; you pay for CPU used

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    // container/server.py reads CALLBACK_SECRET and CALLBACK_BASE from its own
    // environment: the first authenticates every /render call and every callback,
    // the second is where it posts progress and uploads the file. Nothing else
    // sets them. Left empty, the container 401s the Worker's dispatch and the job
    // sits at "queued" forever — so they are filled here, from the Worker's config.
    // CALLBACK_SECRET is deliberately the same value as RENDER_SECRET; it is one
    // shared secret with two names on the two sides of the wire.
    this.envVars = {
      CALLBACK_BASE: env.CALLBACK_BASE ?? "",
      CALLBACK_SECRET: env.RENDER_SECRET ?? "",
    };
  }
}

const json = (o: unknown, status = 200) =>
  new Response(JSON.stringify(o), { status, headers: { "content-type": "application/json" } });

interface Job {
  id: string;
  name: string;
  email: string;
  config: any;
  status: "queued" | "rendering" | "uploading" | "done" | "failed";
  done?: number;
  total?: number;
  bytes?: number;
  error?: string;
  createdAt: string;
  key?: string;
}

export default {
  async fetch(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(req.url);

    // ── submit a render ──────────────────────────────────────────
    if (url.pathname === "/api/render" && req.method === "POST") {
      const body = await req.json<any>().catch(() => null);
      if (!body?.email || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(body.email))
        return json({ error: "a valid email address is required" }, 400);
      if (!body?.config?.seed) return json({ error: "config.seed is required" }, 400);

      // Bound the work before counting it. An unbounded `frames` is a larger
      // hole than an unbounded request rate: one job could run for hours.
      const bad = validateConfig(body.config);
      if (bad) return json({ error: bad }, 400);

      // Rate limit by IP. Validation happens first, so a malformed probe costs
      // nothing. KV is eventually consistent, so this is a soft ceiling — it
      // stops an open render farm, it is not a hard quota.
      const ip = req.headers.get("CF-Connecting-IP") || "unknown";
      const hit = await overLimit(env, ip);
      if (hit)
        return new Response(
          JSON.stringify({
            error: `render limit reached — ${hit.max} per ${hit.label}. Try again in ${humanise(hit.retryAfter)}, or render in your browser instead.`,
          }),
          {
            status: 429,
            headers: { "content-type": "application/json", "retry-after": String(hit.retryAfter) },
          }
        );

      const id = crypto.randomUUID();
      const job: Job = {
        id,
        name: body.config.name || "Sidereal Ink",
        email: body.email,
        config: body.config,
        status: "queued",
        createdAt: new Date().toISOString(),
      };
      await env.JOBS.put(id, JSON.stringify(job), { expirationTtl: 60 * 60 * 24 * 30 });
      await env.RENDER_QUEUE.send({ id });
      ctx.waitUntil(countAgainstLimit(env, ip));
      return json({ jobId: id, status: "queued" });
    }

    // ── poll a job ───────────────────────────────────────────────
    if (url.pathname.startsWith("/api/job/") && req.method === "GET") {
      const id = url.pathname.split("/").pop()!;
      const raw = await env.JOBS.get(id);
      if (!raw) return json({ error: "not found" }, 404);
      const job = JSON.parse(raw) as Job;
      const { email, ...safe } = job;                    // don't echo the address back
      return json(safe);
    }

    // ── download (signed by job id — unguessable UUID) ────────────
    if (url.pathname.startsWith("/f/")) {
      const id = url.pathname.split("/").pop()!;
      const obj = await env.RENDERS.get(`${id}.mp4`);
      if (!obj) return new Response("Not found", { status: 404 });
      const raw = await env.JOBS.get(id);
      const name = raw ? slug(JSON.parse(raw).name) : "sidereal-ink";
      return new Response(obj.body, {
        headers: {
          "content-type": "video/mp4",
          "content-disposition": `attachment; filename="${name}.mp4"`,
          "cache-control": "public, max-age=31536000, immutable",
        },
      });
    }

    // ── container callbacks ──────────────────────────────────────
    if (url.pathname === "/api/internal/status" && req.method === "POST") {
      if (req.headers.get("x-render-secret") !== env.RENDER_SECRET) return json({ error: "no" }, 401);
      const b = await req.json<any>();
      const raw = await env.JOBS.get(b.jobId);
      if (!raw) return json({ error: "unknown job" }, 404);
      const job = JSON.parse(raw) as Job;
      Object.assign(job, b);
      await env.JOBS.put(job.id, JSON.stringify(job), { expirationTtl: 60 * 60 * 24 * 30 });
      if (b.status === "done") ctx.waitUntil(sendMail(job, env, url.origin));
      if (b.status === "failed") ctx.waitUntil(sendFailure(job, env));
      return json({ ok: true });
    }

    if (url.pathname === "/api/internal/upload" && req.method === "PUT") {
      if (req.headers.get("x-render-secret") !== env.RENDER_SECRET) return json({ error: "no" }, 401);
      const id = url.searchParams.get("jobId")!;
      await env.RENDERS.put(`${id}.mp4`, req.body, {
        httpMetadata: { contentType: "video/mp4" },
      });
      const raw = await env.JOBS.get(id);
      if (raw) {
        const job = JSON.parse(raw) as Job;
        job.key = `${id}.mp4`;
        await env.JOBS.put(id, JSON.stringify(job), { expirationTtl: 60 * 60 * 24 * 30 });
      }
      return json({ ok: true });
    }

    // ── static site ──────────────────────────────────────────────
    return env.ASSETS.fetch(req);
  },

  // ── queue consumer: hands the job to a container ───────────────
  async queue(batch: MessageBatch<{ id: string }>, env: Env): Promise<void> {
    for (const msg of batch.messages) {
      try {
        const raw = await env.JOBS.get(msg.body.id);
        if (!raw) { msg.ack(); continue; }
        const job = JSON.parse(raw) as Job;

        // one container instance per job id — parallelism is just more ids
        const container = getContainer(env.RENDERER, job.id);
        const res = await container.fetch(
          new Request("http://container/render", {
            method: "POST",
            headers: {
              "content-type": "application/json",
              "x-render-secret": env.RENDER_SECRET,
            },
            body: JSON.stringify({ jobId: job.id, config: job.config }),
          })
        );
        if (!res.ok) throw new Error(`container said ${res.status}`);
        msg.ack();
      } catch (err: any) {
        console.error("dispatch failed", err?.message);
        msg.retry({ delaySeconds: 30 });
      }
    }
  },
};

const slug = (t: string) =>
  (t || "sidereal-ink").trim().replace(/[^\w\s-]/g, "").replace(/\s+/g, "-").toLowerCase().slice(0, 48);

async function sendMail(job: Job, env: Env, origin: string) {
  const link = `${origin}/f/${job.id}`;
  const c = job.config;
  const dual = !!c.present;
  const detail = dual
    ? `Seed ${c.seed.date} ${c.seed.time} at ${c.seed.lat}, ${c.seed.lon}<br>Present ${c.present.date} ${c.present.time} at ${c.present.lat}, ${c.present.lon}`
    : `Seed ${c.seed.date} ${c.seed.time} at ${c.seed.lat}, ${c.seed.lon} — single moment`;

  const html = `<div style="font-family:Georgia,serif;max-width:520px;margin:0 auto;color:#141413">
    <h1 style="font-weight:500;font-size:24px;margin:0 0 4px">${escapeHtml(job.name)}</h1>
    <p style="color:#b0aea5;font-size:13px;margin:0 0 22px">Your 24-hour lapse has finished rendering.</p>
    <p style="font-family:monospace;font-size:12px;line-height:1.8;color:#4a4a47;background:#faf9f5;padding:14px 16px;border-radius:8px">
      ${detail}<br>${c.frames || 144} frames · ${c.anchor || "start"} · print #${c.print_seed}
      ${job.bytes ? `<br>${(job.bytes / 1048576).toFixed(1)} MB` : ""}
    </p>
    <p style="margin:24px 0">
      <a href="${link}" style="background:#d97757;color:#fff;text-decoration:none;padding:13px 22px;border-radius:8px;display:inline-block;font-family:system-ui;font-size:15px">Download the video</a>
    </p>
    <p style="color:#b0aea5;font-size:11px;line-height:1.6">
      The link stays live for 30 days. Anyone holding it can download the file, so treat it as unlisted rather than private.
    </p>
  </div>`;

  const r = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.RESEND_API_KEY}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      from: env.MAIL_FROM,
      to: [job.email],
      subject: `${job.name} — your render is ready`,
      html,
    }),
  });
  if (!r.ok) console.error("resend failed", r.status, await r.text());
}

async function sendFailure(job: Job, env: Env) {
  await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { authorization: `Bearer ${env.RESEND_API_KEY}`, "content-type": "application/json" },
    body: JSON.stringify({
      from: env.MAIL_FROM,
      to: [job.email],
      subject: `${job.name} — render failed`,
      html: `<p style="font-family:system-ui">The render didn't complete.</p>
             <pre style="font-family:monospace;font-size:12px;background:#faf9f5;padding:12px;border-radius:8px">${escapeHtml(job.error || "unknown error")}</pre>`,
    }),
  });
}

const escapeHtml = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));

interface Env {
  ASSETS: Fetcher;
  JOBS: KVNamespace;
  RENDERS: R2Bucket;
  RENDER_QUEUE: Queue<{ id: string }>;
  RENDERER: DurableObjectNamespace<Renderer>;
  RENDER_SECRET: string;
  RESEND_API_KEY: string;
  MAIL_FROM: string;
  CALLBACK_BASE?: string;          // secret: https://sidereal-ink.<subdomain>.workers.dev
  RATE_PER_HOUR?: string;
  RATE_PER_DAY?: string;
}
