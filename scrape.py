#!/usr/bin/env python3
"""
Sheriff Sale scraper — Montgomery County, PA (CivilView).
Pulls the current foreclosure listing table and (optionally) judgment
amounts from detail pages, then writes data/leads.json for the dashboard.

Runs daily via GitHub Actions (.github/workflows/scrape.yml).
"""
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://salesweb.civilview.com"
COUNTY_ID = 23  # Montgomery County, PA
LIST_URL = f"{BASE}/Sales/SalesSearch?countyId={COUNTY_ID}"
OUT = Path(__file__).resolve().parent / "leads.json"
FETCH_DETAILS = True          # judgment amounts live on detail pages
DETAIL_DELAY_SECONDS = 1.5    # be polite; ~5 min for ~200 listings
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}


def get(url, session):
    r = session.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def parse_list(html):
    soup = BeautifulSoup(html, "html.parser")
    leads = []
    for tr in soup.select("table tr"):
        tds = tr.find_all("td")
        if len(tds) < 7:
            continue
        link = tr.find("a", href=re.compile(r"SaleDetails"))
        if not link:
            continue
        pid_m = re.search(r"PropertyId=(\d+)", link["href"])
        if not pid_m:
            continue
        cells = [td.get_text(" ", strip=True) for td in tds]
        # layout: [details-link, sheriff#, township, sales date, plaintiff, defendant, address]
        leads.append({
            "pid": pid_m.group(1),
            "sheriff": cells[1],
            "township": cells[2],
            "saleDate": cells[3],
            "plaintiff": cells[4],
            "defendant": cells[5],
            "address": cells[6],
            "judgment": None,
        })
    return leads


def parse_judgment(html):
    """Detail pages show 'Approx. Judgment' (label varies slightly)."""
    m = re.search(
        r"(?:Approx(?:imate)?\.?\s*(?:Upset|Judgment)\*?|Judgment)\s*[:<][^$]*\$\s*([\d,]+(?:\.\d{2})?)",
        html, re.I)
    if not m:
        m = re.search(r"\$\s*([\d,]+\.\d{2})", html)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def main():
    session = requests.Session()
    print(f"Fetching list: {LIST_URL}")
    leads = parse_list(get(LIST_URL, session))
    if len(leads) < 10:
        print(f"Only {len(leads)} rows parsed — refusing to overwrite existing data.", file=sys.stderr)
        sys.exit(1)
    print(f"Parsed {len(leads)} listings")

    if FETCH_DETAILS:
        for i, lead in enumerate(leads, 1):
            try:
                html = get(f"{BASE}/Sales/SaleDetails?PropertyId={lead['pid']}", session)
                lead["judgment"] = parse_judgment(html)
                # full (untruncated) plaintiff/defendant also live on the detail page
                soup = BeautifulSoup(html, "html.parser")
                for row in soup.select("tr"):
                    cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                    if len(cells) == 2:
                        label, val = cells[0].lower(), cells[1]
                        if "plaintiff" in label and val:
                            lead["plaintiff"] = val
                        elif "defendant" in label and val:
                            lead["defendant"] = val
            except Exception as e:
                print(f"  detail {lead['pid']}: {e}", file=sys.stderr)
            if i % 25 == 0:
                print(f"  details {i}/{len(leads)}")
            time.sleep(DETAIL_DELAY_SECONDS)

    out = {
        "updated": datetime.now().isoformat(timespec="seconds"),
        "source": f"salesweb.civilview.com countyId={COUNTY_ID}",
        "county": "Montgomery",
        "saleDates": sorted({l["saleDate"] for l in leads},
                            key=lambda d: datetime.strptime(d, "%m/%d/%Y")),
        "fields": ["pid", "sheriff", "township", "saleDate", "plaintiff", "defendant", "address", "judgment"],
        "leads": [[l["pid"], l["sheriff"], l["township"], l["saleDate"],
                   l["plaintiff"], l["defendant"], l["address"], l["judgment"]] for l in leads],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=0))
    print(f"Wrote {OUT} ({len(leads)} leads)")


if __name__ == "__main__":
    main()
