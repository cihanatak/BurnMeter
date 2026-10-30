# Deploying burnmeter.dev

The website at **<https://burnmeter.dev>** is the `docs/` folder of this public
repository, served as static files. There is no build step.

| File | What it is |
|---|---|
| `docs/index.html` | The landing page: one self-contained file with inline CSS and no dependencies |
| `docs/dashboard.png` | Dashboard screenshot (Claude view), used by the landing page and the README |
| `docs/dashboard-codex.png` | Dashboard screenshot (Codex view), used by the README |
| `docs/og.png` | Social preview image (1200×630), rendered from `docs/og.html` |
| `docs/.nojekyll` | Only matters on GitHub Pages: it turns off the Jekyll build |

## How it deploys

The site runs on **Vercel**, connected to this repository through Vercel's Git
integration:

- **Root Directory:** `docs` · **Framework preset:** Other · no build command
- Every push to `main` deploys to Production on burnmeter.dev.
- Branches and pull requests may get Vercel preview deployments, depending on
  the Vercel project settings.

## Everything in `docs/` is public

Any file in `docs/` is served at `https://burnmeter.dev/<file name>`. Keep design
notes, plans and anything internal out of it; design documents live in
[`design/`](./design/).

## Screenshots: synthetic data only

Screenshots must never show real usage: no real project names, folder paths,
chat titles, user names or email addresses. Take them from a Burnmeter instance
that only sees made-up logs:

1. Generate synthetic logs into a throwaway folder that stands in for your home
   folder. For Claude-only data:
   `python tests/generate_fixture.py --out <fake-home>/.claude/projects`
2. Start Burnmeter with its home and app-data folders pointed at the throwaway
   folder, on a spare port. On Windows (bash):
   ```bash
   USERPROFILE='<fake-home>' HOME='<fake-home>' APPDATA='<fake-appdata>' \
   LOCALAPPDATA='<fake-localappdata>' COMPUTERNAME='DEV-LAPTOP' \
   python -m burnmeter serve --port 8780 --no-browser --no-shortcut
   ```
   On macOS or Linux, setting `HOME` is enough for the logs and chat titles; the
   computer name still comes from the system.
3. Check that it really reads the fake data: every project, chat title and
   device name on screen must be made up. Then take a 1440×900 screenshot of
   <http://127.0.0.1:8780> and replace the PNG in `docs/`.
4. Stop the server and delete the throwaway folders.

The current screenshots show Burnmeter v0.3.4 on synthetic data.

## Social preview image

`docs/og.png` is rendered from `docs/og.html` with headless Chrome:

```bash
chrome --headless --window-size=1200,630 --screenshot=docs/og.png "file:///<absolute path to repo>/docs/og.html"
```

The landing page refers to it by absolute URL (`https://burnmeter.dev/og.png`),
so a new image appears in link previews after the next deploy (and after the
social network refreshes its cache).

## Other hosts

`docs/` works on any static host. Point the host at the `docs` folder with no
build command:

- **Netlify:** Build command empty, Publish directory `docs`.
- **Cloudflare Pages:** Framework preset None, Build command empty, Build output
  directory `docs`.
- **GitHub Pages:** Settings → Pages → Deploy from a branch → `main`, folder
  `/docs`. `docs/.nojekyll` is already there. For a custom domain, also add a
  `CNAME` file containing the domain to `docs/`.
