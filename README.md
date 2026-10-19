# Sheriff Sale Engine — v2

Live dashboard of Montgomery County (PA) sheriff sale listings, auto-refreshed
daily by GitHub Actions and enriched with county assessment data.

**Live:** https://elanelkaim-eng.github.io/sheriff-sales/ · Passcode: `elkaim26`

## What the daily scraper does (6:15am ET)

1. Scrapes CivilView for all listings + judgment amounts.
2. Enriches every lead from Montgomery County's Board of Assessment GIS:
   - Assessed value → **estimated market value** (assessment × common level ratio)
   - **Estimated equity** = est. value − judgment
   - Property type (residential / multi-family / commercial / …), sqft,
     year built, beds/baths, last sale
   - Full owner name + **owner mailing address** (absentee detection)
3. Optional: auto skip-traces new leads (see below).

## Common level ratio — update every July

`CLR_FACTOR` in `scrape.py` is **3.36** (official PA factor for Montgomery
County, July 1 2026 – June 30 2027). Each July, search "PA common level ratio
factors <year>" and update the constant. Values are estimates (±10–15%) —
always verify before making an offer.

## Auto skip tracing (optional)

With a BatchData account (batchdata.io, pay-as-you-go wallet, roughly
$0.07–0.18 per matched record):

1. Get an API key from the BatchData dashboard.
2. Repo → Settings → Secrets and variables → Actions → New repository secret:
   name `BATCHDATA_API_KEY`, value = the key.
3. Add `BATCHDATA_API_KEY: ${{ secrets.BATCHDATA_API_KEY }}` under `env:` of
   the "Run scraper" step in `.github/workflows/scrape.yml`.

Each morning, any NEW person-owned lead gets skip-traced once; phones/emails
land in `contacts.json` and appear on the dashboard with tap-to-call /
pre-filled text links. Entities (LLCs, estates, unknown heirs) are skipped.
Note: the API request format was written against BatchData's documented
endpoint; on first run check the Actions log — a field rename may be needed.

Manual route (no API): dashboard → filter leads → "Skip-trace CSV" → upload to
batchskiptracing.com → download results → dashboard "Import contacts".
Imported contacts save per-browser.

## Outreach queue

The "Outreach queue" tab computes which touch is due per lead from days to
sale: letter at ~45d → second letter at ~30d → text at ~21d → call at ~14d.
"Print letters" generates personalized letters (merged with name, equity and
sale date; mailed to the owner's mailing address for absentee owners).
SMS: always send texts manually (TCPA) — the queue pre-fills them.

## Change the passcode

`echo -n "newcode" | shasum -a 256` → replace `GATE_HASH` in index.html and
the file:// fallback string.

## Phase 3 ideas

Zillow Zestimate overlay (RapidAPI), auto-mail via Lob, more counties
(Chester/Philly/Berks via Bid4Assets; Bucks/Delaware PDFs), shared status
backend so the whole team sees one pipeline.
