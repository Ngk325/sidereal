// What keeps /api/render from being an open render farm: bounds on the work a
// single request can ask for, and a ceiling on how often one address can ask.
//
// Deliberately free of Cloudflare imports so it can be run and tested on its own
// — see ../../test/worker.test.mjs.

// ── input bounds ───────────────────────────────────────────────
// Every field the container will act on, checked before a job exists.
//
// A 60-second lapse at 60fps is 3600 frames, so a flat frame cap can no longer be
// the ceiling — 3600 frames at 1440px is a legitimate request, and 3600 frames at
// 4096px is not. What actually costs CPU is pixels drawn, so that is what is
// bounded: MAX_WORK is frames x megapixels, and the frame and size caps below only
// stop absurd single values from reaching the container at all.
export const MAX_FRAMES = 3600;      // 60s at 60fps, the longest the front end offers
export const MAX_FPS = 60;
export const MAX_DURATION = 60;      // seconds of finished video
export const MAX_SIZE = 2160;        // px, square
export const MIN_SIZE = 240;
export const MAX_SPAN_HOURS = 48;
export const MAX_WORK = 3600 * 2.1;  // ~= 3600 frames at 1440px, the documented job

export const QUALITIES = ["web", "master"];

/** Frames x megapixels — what a job actually costs to draw. */
export const workOf = (frames: number, size: number) => (frames * size * size) / 1e6;

export function validateConfig(c: any): string | null {
  const moment = (m: any, which: string): string | null => {
    if (!m || typeof m !== "object") return `config.${which} must be an object`;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(m.date || "")) return `config.${which}.date must be YYYY-MM-DD`;
    if (!/^\d{2}:\d{2}$/.test(m.time || "")) return `config.${which}.time must be HH:MM`;
    if (!num(m.lat, -90, 90)) return `config.${which}.lat must be between -90 and 90`;
    if (!num(m.lon, -180, 180)) return `config.${which}.lon must be between -180 and 180`;
    if (!num(m.tz, -14, 14)) return `config.${which}.tz must be a UTC offset between -14 and 14`;
    return null;
  };
  if (!c || typeof c !== "object") return "config must be an object";

  // An explicit null defeats a default on both sides of the wire: `??` skips it
  // here, and Python's dict.get returns it rather than the fallback, so the
  // container gets None where it expected a number and dies mid-render. Omitting
  // a key is how you ask for its default. `present: null` is the one exception —
  // there it carries the meaning "no present moment", same as leaving it out.
  for (const [k, v] of Object.entries(c))
    if (v === null && k !== "present") return `config.${k} must not be null — omit it for the default`;

  const e = moment(c.seed, "seed") || (c.present ? moment(c.present, "present") : null);
  if (e) return e;

  const fps = c.fps === undefined ? 60 : Number(c.fps);
  if (!Number.isInteger(fps) || fps < 1 || fps > MAX_FPS)
    return `config.fps must be a whole number from 1 to ${MAX_FPS}`;

  // `frames` and `duration` are two ways of saying the same thing, and the
  // container resolves frames first. Mirror that here so what is validated is what
  // is rendered, and refuse both at once rather than silently ignoring one.
  if (c.frames !== undefined && c.duration !== undefined)
    return "config must set either frames or duration, not both";
  const duration = c.duration === undefined ? 60 : c.duration;
  if (!num(duration, 0, MAX_DURATION) || duration === 0)
    return `config.duration must be a number of seconds above 0 and up to ${MAX_DURATION}`;
  const frames = c.frames === undefined ? Math.round(duration * fps) : Number(c.frames);
  if (!Number.isInteger(frames) || frames < 1 || frames > MAX_FRAMES)
    return `config.frames must be a whole number from 1 to ${MAX_FRAMES}`;

  const size = c.size === undefined ? 1440 : c.size;
  if (!num(size, MIN_SIZE, MAX_SIZE) || !Number.isInteger(size))
    return `config.size must be a whole number from ${MIN_SIZE} to ${MAX_SIZE}`;
  if (size % 2 !== 0) return "config.size must be even — H.264 cannot encode odd dimensions";

  const span = c.span_hours === undefined ? 12 : c.span_hours;
  if (!num(span, 0, MAX_SPAN_HOURS) || span === 0)
    return `config.span_hours must be a number of hours above 0 and up to ${MAX_SPAN_HOURS}`;

  if (workOf(frames, size) > MAX_WORK)
    return `config asks for too much work — ${frames} frames at ${size}px. Reduce the duration, the frame rate or the size.`;

  if (c.quality !== undefined && !QUALITIES.includes(c.quality))
    return `config.quality must be one of ${QUALITIES.join(", ")}`;
  if (!Number.isInteger(c.print_seed === undefined ? 19890325 : Number(c.print_seed)))
    return "config.print_seed must be a whole number";
  if (c.anchor && !["calendar", "start", "centre"].includes(c.anchor))
    return "config.anchor must be calendar, start or centre";
  if (typeof c.message === "string" && c.message.length > 240)
    return "config.message must be 240 characters or fewer";
  for (const k of ["name", "place_label"])
    if (typeof c[k] === "string" && c[k].length > 200) return `config.${k} must be 200 characters or fewer`;
  return null;
}

const num = (v: any, lo: number, hi: number) =>
  typeof v === "number" && Number.isFinite(v) && v >= lo && v <= hi;

// ── rate limiting ──────────────────────────────────────────────
// Two windows, stored in the JOBS namespace under an `rl:` prefix so no extra
// binding is needed. Counted only when a job is actually created.
//
// KV is eventually consistent: simultaneous requests can each read a stale count
// and all be let through. This is a soft ceiling that closes the open render farm,
// not a quota. A hard one needs a Durable Object.
export interface RateStore {
  get(key: string): Promise<string | null>;
  put(key: string, value: string, options?: { expirationTtl?: number }): Promise<void>;
}
export interface RateConfig {
  JOBS: RateStore;
  RATE_PER_HOUR?: string;
  RATE_PER_DAY?: string;
}
interface RateWindow { label: string; seconds: number; max: number }

const windows = (env: RateConfig): RateWindow[] => [
  { label: "hour", seconds: 3600, max: Number(env.RATE_PER_HOUR ?? 3) },
  { label: "day", seconds: 86400, max: Number(env.RATE_PER_DAY ?? 10) },
];

export async function overLimit(env: RateConfig, ip: string) {
  const now = Date.now();
  for (const w of windows(env)) {
    if (!(w.max > 0)) continue;                     // 0, or an unparseable value, disables the window
    const raw = await env.JOBS.get(`rl:${w.label}:${ip}`);
    if (!raw) continue;
    const rec = JSON.parse(raw) as { n: number; reset: number };
    if (rec.reset > now && rec.n >= w.max)
      return { label: w.label, max: w.max, retryAfter: Math.ceil((rec.reset - now) / 1000) };
  }
  return null;
}

export async function countAgainstLimit(env: RateConfig, ip: string) {
  const now = Date.now();
  for (const w of windows(env)) {
    if (!(w.max > 0)) continue;
    const key = `rl:${w.label}:${ip}`;
    const raw = await env.JOBS.get(key);
    let rec = raw ? (JSON.parse(raw) as { n: number; reset: number }) : null;
    if (!rec || rec.reset <= now) rec = { n: 0, reset: now + w.seconds * 1000 };
    rec.n++;
    await env.JOBS.put(key, JSON.stringify(rec), {
      expirationTtl: Math.max(60, Math.ceil((rec.reset - now) / 1000)),
    });
  }
}

export const humanise = (s: number) =>
  s < 60 ? `${s} seconds` : s < 5400 ? `${Math.ceil(s / 60)} minutes` : `${Math.ceil(s / 3600)} hours`;
