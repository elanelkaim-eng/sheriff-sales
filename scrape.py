#!/usr/bin/env python3
"""
Sheriff Sale scraper v2 — Montgomery County, PA.

1. Scrapes the CivilView foreclosure listing (sheriff #, parties, address,
   sale date) and judgment amounts from detail pages.
2. Enriches every lead from the county Board of Assessment GIS table:
   assessed value, estimated market value (assessment x common level ratio),
   land use / property type, sqft, year built, beds/baths, full owner name,
   owner mailing address (absentee detection), last sale price.
3. Optional: auto skip-traces NEW leads via BatchData when the
   BATCHDATA_API_KEY environment variable / repo secret is set, writing
   phones & emails to contacts.json.

Runs daily via GitHub Actions (.github/workflows/scrape.yml).
"""
import json
import os
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
GIS_URL = ("https://gis.montcopa.org/arcgis/rest/services/Parcels/"
           "GIS_BOA_LAND/MapServer/0/query")

# PA Dept of Revenue common level ratio factor for Montgomery County.
# July 1, 2026 - June 30, 2027 factor. UPDATE EACH JULY (published by STEB /
# PA Dept of Revenue; search "common level ratio factors" + year).
CLR_FACTOR = 3.36

OUT = Path(__file__).resolve().parent / "leads.json"
CONTACTS = Path(__file__).resolve().parent / "contacts.json"
FETCH_DETAILS = True
DETAIL_DELAY_SECONDS = 1.5
GIS_DELAY_SECONDS = 0.4
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}

SUFFIXES = {"STREET", "ST", "ROAD", "RD", "AVENUE", "AVE", "COURT", "CT",
            "LANE", "LN", "DRIVE", "DR", "CIRCLE", "CIR", "TERRACE", "TER",
            "BOULEVARD", "BLVD", "PLACE", "PL", "PIKE", "WAY", "SQUARE", "SQ",
            "PARKWAY", "PKWY", "HIGHWAY", "HWY", "TURNPIKE", "TPKE", "TRAIL",
            "TRL", "WALK", "RUN", "PATH", "ALLEY", "ALY", "ROW"}
DIRECTIONALS = {"EAST": "E", "WEST": "W", "NORTH": "N", "SOUTH": "S",
                "E": "E", "W": "W", "N": "N", "S": "S"}


def get(url, session, **kw):
    r = session.get(url, headers=HEADERS, timeout=30, **kw)
    r.raise_for_status()
    return r


# ---------------- CivilView ----------------

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
        leads.append({
            "pid": pid_m.group(1), "sheriff": cells[1], "township": cells[2],
            "saleDate": cells[3], "plaintiff": cells[4], "defendant": cells[5],
            "address": cells[6], "judgment": None,
        })
    return leads


def parse_judgment(html):
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


def enrich_details(leads, session):
    for i, lead in enumerate(leads, 1):
        try:
            r = get(f"{BASE}/Sales/SaleDetails?PropertyId={lead['pid']}", session)
            lead["judgment"] = parse_judgment(r.text)
        except Exception as e:
            print(f"  detail {lead['pid']}: {e}", file=sys.stderr)
        if i % 25 == 0:
            print(f"  details {i}/{len(leads)}")
        time.sleep(DETAIL_DELAY_SECONDS)


# ---------------- County assessment (GIS) ----------------

def addr_parts(address):
    """'71 Black Watch Court Horsham PA 19044' -> (71, 'BLACK WATCH', '19044')"""
    zm = re.search(r"\bPA\s+(\d{5})\s*$", address)
    zip5 = zm.group(1) if zm else ""
    body = re.sub(r"\bPA\s+\d{5}\s*$", "", address).strip().upper()
    body = body.split(" AKA ")[0].split(" A/K/A ")[0].split(" AND ")[0].strip()
    nm = re.match(r"^(\d+)", body)
    if not nm:
        return None, None, zip5
    num = int(nm.group(1))
    tokens = re.sub(r"[^A-Z0-9 ]", " ", body[nm.end():]).split()
    # drop fraction / leading unit noise
    name_tokens = []
    for t in tokens:
        if t in SUFFIXES:
            break
        if not name_tokens and t in DIRECTIONALS:
            continue  # county stores directional separately
        if re.fullmatch(r"\d+/\d+|\d+", t) and not name_tokens:
            continue
        name_tokens.append(t)
    name = " ".join(name_tokens[:3])
    return num, name, zip5


def prop_type(land_use, liv_units):
    code = (land_use or "").strip()
    try:
        units = int((liv_units or "0").strip() or 0)
    except ValueError:
        units = 0
    if code.startswith("4"):
        return "Commercial"
    if code.startswith("5"):
        return "Industrial"
    if code.startswith("1"):
        if units > 1:
            return "Multi-family"
        return "Residential"
    if code.startswith("2"):
        return "Multi-family"
    if code.startswith(("6", "7")):
        return "Exempt/Other"
    if code.startswith("3"):
        return "Land/Agric"
    return "Other"


def gis_lookup(lead, session):
    num, name, zip5 = addr_parts(lead["address"])
    if num is None or not name:
        return None
    first_word = name.split()[0].replace("'", "''")
    where = f"LOC_NO={num} AND LOC_STR LIKE '{first_word}%'"
    if zip5:
        where += f" AND LOC_ZIP1_ZIP2 LIKE '{zip5}%'"
    fields = ("PARCEL,LOCATION1,TOTAL_ASSESSMENT,LAND_USE,LIV_UNITS,SFLA,"
              "YEAR_BUILT,BEDROOMS,BATHS,OWN1,OWN2,ADDR1,ADDR3,ZIP1_ZIP2,"
              "SALE_DATE,CONSIDERATION")
    try:
        r = get(GIS_URL, session, params={
            "where": where, "outFields": fields, "f": "json",
            "resultRecordCount": 10,
        })
        feats = r.json().get("features", [])
    except Exception as e:
        print(f"  gis {lead['sheriff']}: {e}", file=sys.stderr)
        return None
    if not feats:
        return None
    # prefer candidate whose LOCATION1 begins with full street-name match
    best = feats[0]["attributes"]
    for f in feats:
        a = f["attributes"]
        loc = (a.get("LOCATION1") or "").upper()
        if name in loc:
            best = a
            break
    return best


def apply_gis(lead, a):
    assessed = a.get("TOTAL_ASSESSMENT") or None
    est = round(assessed * CLR_FACTOR) if assessed else None
    owner = " & ".join(x.strip() for x in [a.get("OWN1") or "", a.get("OWN2") or ""]
                       if x and x.strip())
    mail_street = (a.get("ADDR1") or "").strip()
    mail_rest = (a.get("ADDR3") or "").strip()
    mail_zip = (a.get("ZIP1_ZIP2") or "").strip()[:5]
    mail = ", ".join(x for x in [mail_street, mail_rest] if x)
    if mail_zip:
        mail = f"{mail} {mail_zip}".strip()
    # absentee: mailing house number+zip differ from property
    pnum, _, pzip = addr_parts(lead["address"])
    mnum_m = re.match(r"^(\d+)", mail_street)
    absentee = bool(mail_street) and (
        (mnum_m and pnum is not None and int(mnum_m.group(1)) != pnum)
        or (mail_zip and pzip and mail_zip != pzip))
    sqft = a.get("SFLA") or None
    yr = (a.get("YEAR_BUILT") or "").strip() or None
    beds = (a.get("BEDROOMS") or "").strip() or None
    baths = (a.get("BATHS") or "").strip() or None
    lead.update({
        "assessed": assessed, "estValue": est,
        "landUse": (a.get("LAND_USE") or "").strip(),
        "propType": prop_type(a.get("LAND_USE"), a.get("LIV_UNITS")),
        "sqft": sqft, "yearBuilt": yr, "beds": beds, "baths": baths,
        "owner": owner or None, "ownerMail": mail or None,
        "absentee": absentee,
        "lastSold": (a.get("SALE_DATE") or "").strip() or None,
        "lastPrice": a.get("CONSIDERATION") or None,
        "parcel": (a.get("PARCEL") or "").strip() or None,
    })


def enrich_gis(leads, session):
    hit = 0
    for i, lead in enumerate(leads, 1):
        a = gis_lookup(lead, session)
        if a:
            apply_gis(lead, a)
            hit += 1
        else:
            lead.update({k: None for k in
                         ("assessed", "estValue", "landUse", "sqft", "yearBuilt",
                          "beds", "baths", "owner", "ownerMail", "lastSold",
                          "lastPrice", "parcel")})
            lead["propType"] = "Unknown"
            lead["absentee"] = False
        if i % 25 == 0:
            print(f"  gis {i}/{len(leads)} (matched {hit})")
        time.sleep(GIS_DELAY_SECONDS)
    print(f"GIS matched {hit}/{len(leads)}")


# ---------------- Optional auto skip trace (BatchData) ----------------

ENTITY = re.compile(
    r"llc|l\.l\.c|inc\b|corp|company|estate of|unknown heirs|unknow heirs|"
    r"successors|association|church|trust\b|holdings|realty|construction|"
    r"enterpri|school|borough of|pentecosta|and son|foundation", re.I)


def person_name(text):
    """County/CivilView 'LAST FIRST M' -> (first, last) or None for entities."""
    if not text or ENTITY.search(text):
        return None
    p = text.split("&")[0].strip()
    p = re.split(r"\s+(?:AKA|A/K/A|FKA|F/K/A)\s+", p, flags=re.I)[0]
    t = re.sub(r"[.,]", " ", p).split()
    if len(t) < 2:
        return None
    return t[1].title(), t[0].title()


def skip_trace_new(leads):
    api_key = os.environ.get("BATCHDATA_API_KEY", "").strip()
    if not api_key:
        print("BATCHDATA_API_KEY not set - skipping auto skip trace.")
        return
    contacts = {}
    if CONTACTS.exists():
        try:
            contacts = json.loads(CONTACTS.read_text())
        except Exception:
            contacts = {}
    todo = []
    for l in leads:
        if l["sheriff"] in contacts:
            continue
        name = person_name(l.get("owner") or l.get("defendant") or "")
        if not name:
            continue
        num, street, zip5 = addr_parts(l["address"])
        if num is None:
            continue
        todo.append((l["sheriff"], name, l))
    if not todo:
        print("Skip trace: no new person-leads to trace.")
        return
    print(f"Skip trace: {len(todo)} new leads via BatchData...")
    traced = 0
    for sheriff, (first, last), l in todo:
        body_addr = re.sub(r"\s+PA\s+\d{5}\s*$", "", l["address"]).strip()
        zm = re.search(r"(\d{5})\s*$", l["address"])
        payload = {"requests": [{
            "name": {"first": first, "last": last},
            "propertyAddress": {
                "street": body_addr, "city": "", "state": "PA",
                "zip": zm.group(1) if zm else ""},
        }]}
        try:
            r = requests.post(
                "https://api.batchdata.com/api/v1/property/skip-trace",
                json=payload, timeout=30,
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"})
            r.raise_for_status()
            data = r.json()
            persons = (data.get("results", {}).get("persons")
                       or data.get("results", {}).get("properties") or [])
            phones, emails = [], []
            for p in persons:
                for ph in (p.get("phoneNumbers") or []):
                    n = ph.get("number") if isinstance(ph, dict) else ph
                    if n and n not in phones:
                        phones.append(n)
                for em in (p.get("emails") or []):
                    e = em.get("email") if isinstance(em, dict) else em
                    if e and e not in emails:
                        emails.append(e)
            contacts[sheriff] = {
                "phones": phones[:4], "emails": emails[:3],
                "name": f"{first} {last}", "source": "batchdata",
                "date": datetime.now().strftime("%Y-%m-%d")}
            traced += 1
        except Exception as e:
            print(f"  skiptrace {sheriff}: {e}", file=sys.stderr)
        time.sleep(0.5)
    CONTACTS.write_text(json.dumps(contacts, indent=0))
    print(f"Skip trace complete: {traced}/{len(todo)} traced -> contacts.json")


# ---------------- main ----------------

def main():
    session = requests.Session()
    print(f"Fetching list: {LIST_URL}")
    leads = parse_list(get(LIST_URL, session).text)
    if len(leads) < 10:
        print(f"Only {len(leads)} rows parsed - refusing to overwrite.", file=sys.stderr)
        sys.exit(1)
    print(f"Parsed {len(leads)} listings")

    if FETCH_DETAILS:
        enrich_details(leads, session)
    print("Enriching from county assessment GIS...")
    enrich_gis(leads, session)
    skip_trace_new(leads)

    fields = ["pid", "sheriff", "township", "saleDate", "plaintiff", "defendant",
              "address", "judgment", "assessed", "estValue", "landUse", "propType",
              "sqft", "yearBuilt", "beds", "baths", "owner", "ownerMail",
              "absentee", "lastSold", "lastPrice", "parcel"]
    out = {
        "updated": datetime.now().isoformat(timespec="seconds"),
        "source": f"salesweb.civilview.com countyId={COUNTY_ID} + Montco BOA GIS (CLR {CLR_FACTOR})",
        "county": "Montgomery",
        "clr": CLR_FACTOR,
        "saleDates": sorted({l["saleDate"] for l in leads},
                            key=lambda d: datetime.strptime(d, "%m/%d/%Y")),
        "fields": fields,
        "leads": [[l.get(f) for f in fields] for l in leads],
    }
    OUT.write_text(json.dumps(out, indent=0))
    print(f"Wrote {OUT} ({len(leads)} leads)")


if __name__ == "__main__":
    main()
