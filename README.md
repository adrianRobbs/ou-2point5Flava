# OU2.5 — Over/Under 2.5 Goals Decision Engine

A with/against-trend betting decision engine for the Over/Under 2.5 goals market. **Not a goal-prediction system.** It reads the market's own implied goal-variance shape (from a 14-price odds ladder) and identifies when the favourite side is statistically underpriced. See `src/ou25_pipeline/market/decision.py` for the full rationale and validated zones.

Two ways to use it: a **web app** (matchday view + admin pages) and a **CLI** (everything the web app does, plus historical backfill, backtesting, and competition validation — the CLI is a superset).

---

## 1. First-time setup

1. **Clone and install:**
   ```bash
   uv sync
   cd frontend && npm install && cd ..
   ```
2. **Copy `.env.example` to `.env`** and fill in:
   - `DATABASE_URL` — Postgres connection string (Neon recommended; use the **pooled** endpoint)
   - `THESTATSAPI_KEY` — your TheStatsAPI key
   - `THESTATSAPI_RATE_LIMIT_PER_MIN` — match your actual plan/trial limit (check the provider's dashboard — trial tiers are often throttled well below the paid plan's advertised rate; a mismatch here causes 429s and slow/failed syncs)
3. **Apply the schema:**
   ```bash
   uv run ou25-pipeline init-db
   ```
4. **Seed the originally-tracked 13 competitions** (only needed once, on a fresh database):
   ```bash
   uv run ou25-pipeline sync-backfill --dry-run   # sanity check — should report per-competition gaps
   uv run ou25-pipeline seed-tracked-competitions  # requires the competitions already registered; run backfill or the Competitions page's catalog sync first if any are missing
   ```

---

## 2. Running it

**Web app (local dev):**
```bash
cd frontend && npm run build && cd ..
uv run ou25-pipeline serve
```
Serves both the API and the built frontend from one process at `http://localhost:8000`. In production this same app runs as a single Render web service (`render.yaml`), deployed via a git-tag-gated GitHub Actions workflow — pushing to `main` does **not** deploy; pushing a `v*` tag does (see §6).

**CLI:** `uv run ou25-pipeline --help` for the full list, or see §4 below.

---

## 3. Navigating the web app

Three pages, top nav:

### Matchday (default view)
Read-only. Date picker across days with assessed predictions. Each row shows kickoff, competition, fixture, call (`BACK OVER`/`BACK UNDER` or nothing), decision zone, and result once the match is finished. **Download CSV** exports exactly what's on screen. No login required — this page has no admin gate.

### Backfill
**Requires the admin token** (see §5) — you'll be prompted once per browser session; it's cached in `localStorage` after that.

- **Daily Sync panel** (top): "Fetch upcoming fixtures" (1-7 day window) and "Refresh odds & results" — manual stand-ins for the cron jobs that are commented out while the app runs on Render's free tier. Both run synchronously in the request and can take a while depending on your API rate limit; if a request times out at a low rate limit, use the equivalent CLI command instead (§4) — CLI runs have no HTTP timeout.
- **Competition list**: every currently *tracked* competition, expandable to its seasons. Each season shows our stored match count, a "Check completeness" button (compares against the live provider count, with the real kickoff date range so you can tell "not started yet" from an actual gap), and a "Sync" button that triggers a full backfill. **The Sync button requires a paid Render Job and will 501 on the free tier** — use the CLI `backfill`/`sync-backfill` commands instead; they do the same thing without needing a paid plan.

### Competitions
**Also requires the admin token.**

- **Sync catalog**: fetches the full list of competitions the provider offers (~150) and caches it. Safe to click anytime — never changes which competitions are already tracked, only adds/refreshes catalog entries.
- **Tracked** section, grouped by tier (1/2/3/Unclassified) — competitions currently in scope for daily discover/refresh/backfill.
- **Not tracked** section, grouped by country — pick one, optionally assign a tier, click **Track**. Tracking alone does **not** make it bettable (see §4's `validate-competition`) — tracking only puts it in scope for data collection; a competition needs enough backfilled history and a passing validation run before the engine will ever place a bet in it.

---

## 4. CLI commands

Run any of these with `--help` for full option details.

| Command | What it does |
|---|---|
| `init-db` | Applies `db/schema.sql`. Safe to re-run. |
| `backfill --competition <id-or-name> --season <year>` | Backfills one competition/season: matches + all satellite data. Resumable — safe to re-run after a quota cutoff. |
| `sync-backfill [--competition <id>...] [--dry-run]` | Finds gaps between our stored data and the live finished-match count, for every already-registered season of every *tracked* competition (or specific ones given). Backfills each gap via the same logic as `backfill`. |
| `seed-tracked-competitions` | One-off: marks the original 13 competitions as tracked in the database. Only needed once, on a fresh DB. |
| `validate-competition [--competition <id>...]` | **How a competition earns entry into the live betting rule.** Omit `--competition` to scan every unvalidated competition with stored matches. Reports zone-bet count, edge, ROI, and a pass/fail verdict against pre-registered criteria (≥100 zone bets, positive edge, ROI>0 with P(ROI>0)≥0.90). Zero API cost — reads only the database. A PASS is a recommendation: add the id to `decision.VALIDATED_COMPETITIONS` and bump `RULE_VERSION` to actually admit it. |
| `discover-fixtures [--competition <id>...] [--max-days-ahead N]` | Lists upcoming priced fixtures for tracked competitions and inserts placeholder predictions. Same logic as the Backfill page's "Fetch upcoming fixtures" button. |
| `refresh-odds [--competition <id>...]` | Re-fetches odds for pending/upcoming predictions, classifies them, and syncs finished results. Same logic as "Refresh odds & results". Run this several times a day close to kickoff — the market signal needs a mature odds ladder. |
| `recommend-bets [--competition <id>...]` | Fetches live odds and applies the rule for real, appending to `data/decisions/forward_log.csv` — the actual forward-tracking evidence trail. |
| `export-matches` / `export-features` | Flatten the database into CSVs for EDA/modeling. |
| `train-model` / `backtest-zones` | Modeling and backtest tooling (see `market/backtest.py` for methodology — bootstrap CIs, walk-forward, thirds stability, threshold sensitivity). |
| `validate --competition-id <id> --season-id <id>` | Post-backfill data-quality checks (row counts, null rates, referential integrity) — not to be confused with `validate-competition` above, which checks betting *profitability*, not data quality. |
| `serve [--host] [--port] [--reload]` | Runs the web app locally. |

**Recommended day-to-day loop:** `discover-fixtures` once a day, `refresh-odds` a few times a day close to kickoffs. Both work identically via CLI or the UI buttons — CLI is the more reliable choice if your API rate limit is low enough that a sync might exceed an HTTP request timeout.

**Onboarding a new competition into the betting rule (not just tracking it):**
1. Track it (Competitions page, or it happens automatically via live discovery).
2. Backfill enough seasons for it: `backfill --competition <id> --season <year>`, repeated per season needed.
3. Run `validate-competition --competition <id>` to see if it has enough zone bets yet, and whether it passes.
4. If it needs more data, the command tells you so directly — backfill another season and re-run.
5. On a PASS, add it to `VALIDATED_COMPETITIONS` in `market/decision.py`, bump `RULE_VERSION`, and ship it as a deliberate, versioned change (see `market/decision.py`'s module docstring and the existing zones for the pattern to follow).

---

## 5. The admin token

The Backfill and Competitions pages are the only authenticated surface in the app — a single shared-secret header (`X-Admin-Token`), not a real login system. Set `BACKFILL_ADMIN_TOKEN` (any string you choose) as an env var; if it's unset, those routes 403 for everyone (fails closed, not open). The frontend prompts for it once via `window.prompt` and caches it in `localStorage`.

`RENDER_API_KEY` / `RENDER_SERVICE_ID` are only needed for the Backfill page's per-season Sync button (which requires a paid Render plan) — leave them unset if you're only using the CLI for backfills.

---

## 6. Deployment

Render Blueprint (`render.yaml`), one free-tier web service serving both API and built frontend. **Deploys are git-tag-gated**, not automatic on push:

```bash
git push origin main          # does NOT deploy
git tag vX.Y.Z
git push origin vX.Y.Z        # triggers .github/workflows/deploy.yml -> Render
```

The workflow resolves the tag's exact commit and calls Render's deploy API directly (not a bare deploy-hook, so it never accidentally deploys whatever `main` has moved to since the tag was cut). Needs two GitHub Actions secrets set once: `RENDER_API_KEY`, `RENDER_WEB_SERVICE_ID`.

Cron jobs for scheduled discover/refresh are defined but commented out in `render.yaml` (no free tier for Render Cron Jobs) — the Backfill page's manual buttons and the CLI commands in §4 are the free-tier substitute.

`DATABASE_URL`, `THESTATSAPI_KEY`, `RENDER_API_KEY`, `RENDER_SERVICE_ID`, `BACKFILL_ADMIN_TOKEN` are all set directly in the Render dashboard (marked `sync: false` in `render.yaml`, so a blueprint re-sync never overwrites them) — not through the deploy workflow.
