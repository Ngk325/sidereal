# Getting this onto GitHub

Everything below runs on your machine — I can't push for you, since that needs
your credentials.

## With the GitHub CLI (one command)

```bash
cd sidereal-ink
gh repo create sidereal-ink --public --source=. --remote=origin --push \
  --description "A generative figure drawn from moments in time and places on earth"
```

`gh auth login` first if you haven't. Add `--private` instead of `--public` to keep it closed.

## Without the CLI

Create an empty repo at https://github.com/new — no README, no .gitignore, no licence,
since this repo already has them. Then:

```bash
cd sidereal-ink
git init -b main
git add .
git commit -m "Sidereal Ink: generative figure from moments in time and places on earth"
git remote add origin git@github.com:YOUR-USERNAME/sidereal-ink.git
git push -u origin main
```

Use the `https://github.com/...` URL instead if you aren't set up with SSH keys.

## Turn on GitHub Pages — free, no card, nothing else to sign up for

**Settings → Pages → Source → GitHub Actions.** That is the whole setup.

`.github/workflows/pages.yml` publishes `site/` on every push to main. It has to be
a workflow rather than the simpler branch setting, because publishing from a branch
only offers the repository root or `/docs` — not an arbitrary folder like `site/`.

Live at `https://YOUR-USERNAME.github.io/REPO-NAME/` a minute or two after the first
push, or after running the workflow by hand from the Actions tab.

Geolocation and canvas capture both need HTTPS, which Pages provides — so the
location button and video recording work there but not from a local file.

What you get is the whole artwork: the figure, the cipher, the decode table, PNG
export, and lapses rendered in the browser. What you don't get is the hosted render
service, which needs Cloudflare. The page checks for the service on load and quietly
uses the in-browser renderer when it isn't there, so nothing looks broken.

## Automatic deploys to Cloudflare (optional)

`.github/workflows/deploy-site.yml` publishes `site/` as a Cloudflare Worker. It is
set to manual (Actions tab → Run workflow) because it needs two repository secrets —
Settings → Secrets and variables → Actions:

- `CLOUDFLARE_API_TOKEN` — a token with the *Edit Cloudflare Workers* template
- `CLOUDFLARE_ACCOUNT_ID` — from your Cloudflare dashboard URL

Without them a push trigger would fail on every commit, so add one back once the
secrets exist. Delete the file if you are using Pages and don't want it.

Note the Worker name in that workflow is `sidereal-ink-site`, kept apart from the
`sidereal-ink` name the render service claims. Publishing a static Worker over the
service would silently remove `/api/render`.

## Before you make it public

`service/worker/wrangler.jsonc` has a placeholder KV id and a `MAIL_FROM` address —
both are fine to commit. Real secrets (`RENDER_SECRET`, `RESEND_API_KEY`) live in
`wrangler secret put` and never touch the repo. `.gitignore` already excludes
`.env`, `.wrangler/` and rendered output.
