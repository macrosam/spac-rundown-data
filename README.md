# spac-rundown-data

Public feed of SPAC-related SEC EDGAR filings (S-1/F-1 pipeline, 424B/FWP
pricings, S-1/A / F-1/A amendments) extracted from the EDGAR daily indexes.

- `fetch_spac_filings.py` runs twice each weekday via GitHub Actions
  (see `.github/workflows/update.yml`) and writes `data/latest.json`.
- The **Spac Snapshot** Google Apps Script (under the owner's Google account)
  reads `data/latest.json` from this repo's raw URL and sends the
  "SPAC Rundown" email at ~8:23 AM and ~3:37 PM ET.

Why this split exists: SEC's WAF blocks Google Apps Script traffic entirely
(403 on www.sec.gov, data.sec.gov, and efts.sec.gov), and Apps Script's
`UrlFetchApp` overrides the `User-Agent` header, so the SEC-required declared
UA can never be presented from Apps Script. GitHub Actions runners are not
blocked, so the fetch/parse happens here and the email stays in Apps Script.

All data in this repo is public SEC EDGAR data. The `SEC_USER_AGENT` repo
secret holds the declared contact UA required by SEC fair-access rules.
