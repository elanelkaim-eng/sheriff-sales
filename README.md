# Sheriff Sale Engine — Deploy Guide

Live dashboard of Montgomery County (PA) sheriff sale listings with outreach
tracking, auto-refreshed daily by GitHub Actions.

**Passcode:** `elkaim26` (to change it, see "Change the passcode" below)

## Go live (one time, ~10 minutes)

1. **Create a GitHub account** (if needed) at github.com — free.
2. **Create a new repository**: click "+" → New repository → name it
   `sheriff-sales` → set to **Public** (required for free GitHub Pages) →
   Create. (The data shown is already public county record; the passcode
   gate keeps casual visitors out and the site is marked noindex.)
3. **Upload this folder's contents**: on the repo page → "uploading an
   existing file" → drag in everything inside `sheriff-sale-site/`
   (index.html, the data/, scraper/, and .github/ folders) → Commit.
   - If the web uploader won't take the `.github` folder, create the file
     manually: Add file → Create new file → type
     `.github/workflows/scrape.yml` as the name and paste the file contents.
4. **Turn on Pages**: repo → Settings → Pages → Source: "Deploy from a
   branch" → Branch: `main`, folder `/ (root)` → Save.
5. Wait ~2 minutes. Your dashboard is live at:
   `https://YOUR-USERNAME.github.io/sheriff-sales/`

## Daily auto-refresh

The workflow in `.github/workflows/scrape.yml` runs every morning (~6:15am
ET), scrapes CivilView, fills in judgment amounts + full owner names, and
republishes. To run it on demand: repo → Actions → "Daily sheriff sale
scrape" → Run workflow.

The first run replaces the seed data (which has names truncated by the
list view) with complete details.

## Change the passcode

1. Pick a new passcode, e.g. `mynewcode`.
2. Get its SHA-256 hash: on a Mac, Terminal → `echo -n "mynewcode" | shasum -a 256`
3. In `index.html`, replace the value of `GATE_HASH` with the new hash and
   update the file:// fallback string on the `const ok =` line.

## How outreach tracking works

Statuses, notes, and follow-up dates save in the browser (localStorage),
keyed by sheriff number — so they survive daily data refreshes. They are
per-device: you and Armen each keep your own board unless you export/share
CSV. (A shared backend is the natural Phase 2 upgrade.)

## Phase 2 ideas (from the tech handoff + Armen's prototype)

- Zillow value + equity % via RapidAPI (key goes in the scraper)
- Contact enrichment: BatchSkipTracing bulk upload from the CSV export
- More counties: Chester/Philadelphia/Berks (Bid4Assets), Bucks/Delaware (PDF)
- Shared status backend so the team sees one pipeline
