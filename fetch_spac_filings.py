#!/usr/bin/env python3
"""Fetch SEC EDGAR daily indexes and extract SPAC-related filings.

Writes data/latest.json, consumed by the "Spac Snapshot" Google Apps Script
that sends the SPAC Rundown email. Apps Script cannot fetch sec.gov directly
(SEC's WAF blocks Google Apps Script traffic and UrlFetchApp overrides the
User-Agent header), so this job does the fetching from GitHub Actions.

All data here is public SEC EDGAR data.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

INDEX_BASE = "https://www.sec.gov/Archives/edgar/daily-index"
LOOKBACK_DAYS = 10  # superset of the email's AM (7d) and PM (2d) windows

FORMS_PIPELINE = {"S-1", "F-1"}
FORMS_PRICING = {"424B1", "424B2", "424B3", "424B4", "424B5", "FWP"}
FORMS_AMENDS = {"S-1/A", "F-1/A"}

# form.idx is fixed-width with FORM TYPE first. Column offsets drift between
# files, so anchor on CIK digits + YYYYMMDD date + edgar/ path instead.
FIXED_ROW = re.compile(r"^(.+?)\s{2,}(.+?)\s{2,}(\d{1,10})\s{2,}(\d{8})\s{2,}(edgar/\S+)")

ACQ_NAME = re.compile(r"acquisition\s+(corp|co|company|corporation|holdings|i|ii|iii|iv|v)\b", re.I)
SPAC_WORD = re.compile(r"\bspac\b", re.I)
SPAC_PHRASES = ("special purpose acquisition", "blank check", "blank-check")


def looks_spac(company: str) -> bool:
    lc = company.lower()
    return bool(ACQ_NAME.search(company)) or bool(SPAC_WORD.search(company)) or any(
        p in lc for p in SPAC_PHRASES
    )


def fetch(url: str, ua: str) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "text/plain,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("latin-1")
    except urllib.error.HTTPError as e:
        # Weekends/holidays have no index file; SEC answers 403/404 for those.
        print(f"  index not available: {url} ({e.code})")
        return None
    except Exception as e:  # noqa: BLE001
        print(f"  fetch error: {url} ({e})")
        return None


def parse_daily_idx(text: str) -> list[dict]:
    out = []
    lines = text.splitlines()
    i = 0
    while i < len(lines) and not re.fullmatch(r"-{5,}", lines[i].strip()):
        i += 1
    i += 1
    for line in lines[i:]:
        if not line.strip():
            continue
        if "|" in line:
            # master.idx: CIK | Company | Form | Date | Path (CIKs are NOT zero-padded)
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 5:
                continue
            if re.fullmatch(r"\d+", parts[0]):
                cik, company, form, filed, path = parts[:5]
            else:
                company, form, cik, filed, path = parts[:5]
        else:
            m = FIXED_ROW.match(line)
            if not m:
                continue
            form, company, cik, filed, path = (g.strip() for g in m.groups())
        if not (company and form and filed and path):
            continue
        if re.fullmatch(r"\d{8}", filed):
            filed = f"{filed[0:4]}-{filed[4:6]}-{filed[6:8]}"
        out.append({"company": company, "form": form, "cik": cik, "filed": filed, "path": path})
    return out


def main() -> int:
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua:
        print("SEC_USER_AGENT env var is required (SEC needs a declared contact).", file=sys.stderr)
        return 1

    today = date.today()
    rows: list[dict] = []
    for k in range(LOOKBACK_DAYS + 1):
        d = today - timedelta(days=k)
        q = (d.month - 1) // 3 + 1
        base = f"{INDEX_BASE}/{d.year}/QTR{q}"
        print(f"{d}:")
        text = fetch(f"{base}/form.{d:%Y%m%d}.idx", ua)
        if text is None:
            text = fetch(f"{base}/master.{d:%Y%m%d}.idx", ua)
        if text is None:
            continue
        parsed = parse_daily_idx(text)
        print(f"  parsed {len(parsed)} rows")
        rows.extend(parsed)
        time.sleep(0.4)  # polite pause per SEC fair-access guidance

    pipeline, pricings, amends = [], [], []
    for r in rows:
        form = r["form"].upper()
        if form in FORMS_PIPELINE and looks_spac(r["company"]):
            pipeline.append(r)
        elif form in FORMS_PRICING and looks_spac(r["company"]):
            pricings.append(r)
        elif form in FORMS_AMENDS and looks_spac(r["company"]):
            amends.append(r)

    by_date_desc = lambda r: r["filed"]  # noqa: E731
    for bucket in (pipeline, pricings, amends):
        bucket.sort(key=by_date_desc, reverse=True)

    feed = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lookback_days": LOOKBACK_DAYS,
        "source": "SEC EDGAR daily index",
        "pipeline": pipeline,
        "pricings": pricings,
        "amends": amends,
    }
    os.makedirs("data", exist_ok=True)
    with open("data/latest.json", "w") as f:
        json.dump(feed, f, indent=1)
    print(
        f"wrote data/latest.json: pipeline={len(pipeline)} "
        f"pricings={len(pricings)} amends={len(amends)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
