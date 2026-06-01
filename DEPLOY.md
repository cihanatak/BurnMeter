# Deploy — Burnmeter landing page

The public landing/home page is **`docs/index.html`** — one self-contained file
(inline CSS, **no build step**, no dependencies). To go live, point any static
host at the **`docs/`** folder. Pick one option below.

## Read first — the repo is PRIVATE
- **Netlify / Cloudflare Pages / Vercel** can host from a *private* repo for free
  — easiest path, no visibility change needed.
- **GitHub Pages** on a *private* repo requires **GitHub Pro/Team** (paid). On a
  free account, Pages only works for a **public** repo.
- The "GitHub" and "★ Star on GitHub" buttons link to
  `https://github.com/cihanatak/BurnMeter`. While the repo is private those 404
  for the public — either make the repo public, or edit those two `<a href>` lines
  in `docs/index.html`.

## Option A — Netlify (easiest, free, private repo OK)
1. netlify.com → **Add new site → Import from Git** → pick `cihanatak/BurnMeter`.
2. **Build command:** *(leave empty)*  ·  **Publish directory:** `docs`
3. Deploy → you get a `*.netlify.app` URL.
   *(No-Git alternative: drag the `docs/` folder onto app.netlify.com/drop.)*

## Option B — Cloudflare Pages (free, private repo OK)
1. dash.cloudflare.com → **Workers & Pages → Create → Pages → Connect to Git** →
   `cihanatak/BurnMeter`.
2. **Framework preset:** None  ·  **Build command:** *(empty)*  ·  **Build output dir:** `docs`
3. Save & Deploy.

## Option C — Vercel (free, private repo OK)
1. vercel.com → **Add New → Project** → import `cihanatak/BurnMeter`.
2. **Framework:** Other  ·  **Root Directory:** `docs`  ·  no build command.
3. Deploy.

## Option D — GitHub Pages (only if repo is PUBLIC, or you have GitHub Pro)
1. GitHub → repo → **Settings → Pages**.
2. **Source:** Deploy from a branch → **Branch:** `main` → **Folder:** `/docs` → Save.
3. Live at `https://cihanatak.github.io/BurnMeter/` in ~1 min.
   `docs/.nojekyll` is already present, so files are served as-is (no Jekyll build).

## Custom domain (optional, any host)
Set the domain in the host's dashboard. For GitHub Pages, also drop a `CNAME`
file containing your domain into `docs/`.

---
**Note:** `docs/` also contains `SYNC.md`; with GitHub Pages it'd be reachable at
`/SYNC.md` (harmless — no secrets). Move it out of `docs/` if you'd rather it not
be web-reachable.
