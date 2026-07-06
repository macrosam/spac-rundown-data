#!/usr/bin/env python3
"""Fetch SEC EDGAR daily indexes and extract SPAC-related filings.

Writes data/latest.json (consumed by the "Spac Snapshot" Google Apps Script
that sends the SPAC Rundown email) and maintains data/history.json, a rolling
~35-day store so the email can fall back to a 30-day window when a section has
nothing new. Apps Script cannot fetch sec.gov directly (SEC's WAF blocks
Google Apps Script traffic and UrlFetchApp overrides the User-Agent header),
so this job does the fetching from GitHub Actions.

Sections and their sources:
  pipeline   S-1, F-1                          (SPAC-like names)
  pricings   424B1-424B5, FWP                  (SPAC-like names)
  amends     S-1/A, F-1/A                      (SPAC-like names)
  mergers    425, S-4(/A), F-4(/A), DEFM14A/C, PREM14A + 8-K items 1.01/1.02/2.01
  votes      DEF 14A, DEFA14A, PRE 14A, PRER14A + 8-K items 5.07/5.03
  pipes      8-K item 3.02 (unregistered sales — PIPE signal)
  sponsors   8-K item 5.02 (officer/director changes)
  exchanges  8-A12B, 8-A12G, 25, 25-NSE + 8-K item 3.01
  media      Google News RSS (SPAC search, last ~2 days)
  regs       SEC press-release RSS filtered for SPAC keywords

All data here is public SEC EDGAR / RSS data.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

INDEX_BASE = "https://www.sec.gov/Archives/edgar/daily-index"
HISTORY_PATH = "data/history.json"
LATEST_PATH = "data/latest.json"
HISTORY_DAYS = 35          # rolling store horizon
BOOTSTRAP_DAYS = 31        # first-ever run backfills this much
INCREMENTAL_DAYS = 7       # normal runs re-check this window (covers gaps)

FORMS = {
    "pipeline": {"S-1", "F-1"},
    "pricings": {"424B1", "424B2", "424B3", "424B4", "424B5", "FWP"},
    "amends": {"S-1/A", "F-1/A"},
    "mergers": {"425", "S-4", "S-4/A", "F-4", "F-4/A", "DEFM14A", "DEFM14C", "PREM14A"},
    "votes": {"DEF 14A", "DEFA14A", "PRE 14A", "PRER14A"},
    "exchanges": {"8-A12B", "8-A12G", "25", "25-NSE"},
}
EIGHTK_FORMS = {"8-K", "8-K/A"}
ALL_FORMS = set().union(*FORMS.values()) | EIGHTK_FORMS

# 8-K "ITEM INFORMATION" header text -> (section, short note)
EIGHTK_ITEM_MAP = [
    ("entry into a material definitive agreement", ("mergers", "8-K: entry into material definitive agreement")),
    ("termination of a material definitive agreement", ("mergers", "8-K: termination of material agreement")),
    ("completion of acquisition", ("mergers", "8-K: completion of acquisition")),
    ("unregistered sales of equity securities", ("pipes", "8-K: unregistered equity sale (PIPE signal)")),
    ("submission of matters to a vote", ("votes", "8-K: shareholder vote results")),
    ("amendments to articles", ("votes", "8-K: charter amendment (possible extension)")),
    ("departure of directors", ("sponsors", "8-K: officer/director change")),
    ("notice of delisting", ("exchanges", "8-K: delisting / listing-deficiency notice")),
]

# form.idx is fixed-width with FORM TYPE first. Column offsets drift between
# files, so anchor on CIK digits + YYYYMMDD date + edgar/ path instead.
FIXED_ROW = re.compile(r"^(.+?)\s{2,}(.+?)\s{2,}(\d{1,10})\s{2,}(\d{8})\s{2,}(edgar/\S+)")

ACQ_NAME = re.compile(r"acquisition\s+(corp|co|company|corporation|holdings|i|ii|iii|iv|v)\b", re.I)
SPAC_WORD = re.compile(r"\bspac\b", re.I)
SPAC_PHRASES = ("special purpose acquisition", "blank check", "blank-check")

REG_KEYWORDS = re.compile(r"\bspac(s)?\b|blank[- ]check|special purpose acquisition|shell compan", re.I)

# Quoted deal-specific phrases: bare "SPAC" matches the Saratoga Performing
# Arts Center in Google News.
NEWS_RSS = (
    "https://news.google.com/rss/search?"
    "q=%22SPAC%20merger%22%20OR%20%22de-SPAC%22%20OR%20%22SPAC%20IPO%22%20OR%20%22SPAC%20deal%22"
    "%20OR%20%22blank%20check%20company%22%20when:2d&hl=en-US&gl=US&ceid=US:en"
)
SEC_PRESS_RSS = "https://www.sec.gov/news/pressreleases.rss"


def looks_spac(company: str) -> bool:
    lc = company.lower()
    return bool(ACQ_NAME.search(company)) or bool(SPAC_WORD.search(company)) or any(
        p in lc for p in SPAC_PHRASES
    )


def fetch(url: str, ua: str, max_bytes: int | None = None) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read(max_bytes) if max_bytes else resp.read()
            return data.decode("latin-1", errors="replace")
    except urllib.error.HTTPError as e:
        # Weekends/holidays have no index file; SEC answers 403/404 for those.
        print(f"  not available: {url} ({e.code})")
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
            # master.idx: CIK | Company | Form | Date | Path (CIKs NOT zero-padded)
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


def eightk_items(path: str, ua: str) -> list[str]:
    """Read ITEM INFORMATION lines from the SGML header of a full submission."""
    text = fetch(f"https://www.sec.gov/Archives/{path}", ua, max_bytes=30000)
    if text is None:
        return []
    return [
        m.group(1).strip()
        for m in re.finditer(r"ITEM INFORMATION:\s*(.+)", text)
    ]


# ---------------------------------------------------------------------------
# Cover-page fact extraction ("blurbs")
# ---------------------------------------------------------------------------
BLURB_OFFERING = FORMS["pipeline"] | FORMS["pricings"] | FORMS["amends"]
BLURB_PROXY = FORMS["votes"] | {"DEFM14A", "DEFM14C", "PREM14A"}
BLURB_DEAL = {"425", "S-4", "S-4/A", "F-4", "F-4/A"}
BLURB_FORMS = BLURB_OFFERING | BLURB_PROXY | BLURB_DEAL

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&#\d+;|&[a-zA-Z]+;", " ", text)
    return re.sub(r"\s+", " ", text)


def extract_blurb(form: str, path: str, ua: str) -> str:
    """Best-effort key facts from a filing's cover page. Labeled auto-extracted."""
    raw = fetch(f"https://www.sec.gov/Archives/{path}", ua, max_bytes=120000)
    if raw is None:
        return ""
    # Skip the SGML header; work on the document body text
    body = raw.split("</SEC-HEADER>", 1)[-1]
    text = _strip_html(body)
    parts = []

    if form in BLURB_OFFERING:
        m = re.search(r"\$\s?([1-9][\d,]{6,14})(?!\d)", text)
        if m:
            parts.append("offering $" + m.group(1))
        m = re.search(r"([1-9][\d,]{5,14})\s+Units\b", text)
        if m:
            parts.append(m.group(1) + " units")
        m = re.search(r"offering price of\s+\$([\d.]{1,6})", text) or re.search(
            r"\$([\d.]{1,6})\s+per\s+[Uu]nit", text)
        if m:
            parts.append("$" + m.group(1) + "/unit")
        m = re.search(r"focus(?:ing|ed)?\s+on\s+([^.;]{10,140})", text)
        if m:
            parts.append("focus: " + m.group(1).strip())

    elif form in BLURB_PROXY:
        m = re.search(rf"meeting[\s\S]{{0,300}}?((?:{MONTHS})\s+\d{{1,2}},\s+\d{{4}})", text)
        if m:
            parts.append("meeting " + m.group(1))
        head = text[:20000].lower()
        if "extend" in head or "extension" in head:
            parts.append("extension on the agenda")
        elif "business combination" in head or "merger" in head:
            parts.append("business-combination vote")

    elif form in BLURB_DEAL:
        m = re.search(
            r"business combination(?: agreement)?\s+(?:with|between|among)\s+"
            r"([A-Z][A-Za-z0-9&.,'\- ]{2,60}?)(?:,|\s+and\b|\s+\()", text)
        if m:
            parts.append("business combination with " + m.group(1).strip())

    if not parts:
        return ""
    return "; ".join(parts)[:220] + " (auto-extracted)"


def fetch_rss_items(url: str, ua: str) -> list[dict]:
    text = fetch(url, ua)
    if not text:
        return []
    try:
        root = ET.fromstring(text.encode("latin-1", errors="replace"))
    except ET.ParseError as e:
        print(f"  rss parse error for {url}: {e}")
        return []
    items = []
    for item in root.iter("item"):
        get = lambda tag: (item.findtext(tag) or "").strip()  # noqa: E731
        pub = get("pubDate")
        try:
            pub_iso = datetime.strptime(pub[:25].strip(), "%a, %d %b %Y %H:%M:%S").strftime("%Y-%m-%d")
        except ValueError:
            pub_iso = pub[:16]
        src = item.find("source")
        items.append({
            "title": get("title"),
            "link": get("link"),
            "published": pub_iso,
            "source": (src.text or "").strip() if src is not None else "",
            "summary": re.sub(r"<[^>]+>", " ", get("description"))[:300],
        })
    return items


def main() -> int:
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua:
        print("SEC_USER_AGENT env var is required (SEC needs a declared contact).", file=sys.stderr)
        return 1

    # --- rolling history store ---
    history: dict[str, dict] = {}
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH) as f:
            history = json.load(f).get("rows", {})
    days = INCREMENTAL_DAYS if history else BOOTSTRAP_DAYS
    print(f"history rows: {len(history)}; fetching last {days} days of indexes")

    today = date.today()
    for k in range(days + 1):
        d = today - timedelta(days=k)
        q = (d.month - 1) // 3 + 1
        base = f"{INDEX_BASE}/{d.year}/QTR{q}"
        print(f"{d}:")
        text = fetch(f"{base}/form.{d:%Y%m%d}.idx", ua)
        if text is None:
            text = fetch(f"{base}/master.{d:%Y%m%d}.idx", ua)
        if text is None:
            continue
        added = 0
        for r in parse_daily_idx(text):
            form = r["form"].upper()
            if form not in ALL_FORMS or not looks_spac(r["company"]) or r["path"] in history:
                continue
            history[r["path"]] = r
            added += 1
        print(f"  +{added} SPAC-relevant rows")
        time.sleep(0.4)  # polite pause per SEC fair-access guidance

    # Enrich 8-Ks with their item lines (once per filing; cached in history)
    pending = [r for r in history.values() if r["form"].upper() in EIGHTK_FORMS and "items" not in r]
    print(f"enriching {len(pending)} 8-K filings with item headers")
    for r in pending:
        r["items"] = eightk_items(r["path"], ua)
        time.sleep(0.3)

    # Extract cover-page facts for offering/proxy/deal filings (once per filing)
    pending = [r for r in history.values()
               if r["form"].upper() in BLURB_FORMS and "blurb" not in r]
    print(f"extracting cover-page facts for {len(pending)} filings")
    for r in pending:
        r["blurb"] = extract_blurb(r["form"].upper(), r["path"], ua)
        time.sleep(0.3)

    # Prune beyond the rolling horizon
    cutoff = (today - timedelta(days=HISTORY_DAYS)).isoformat()
    history = {p: r for p, r in history.items() if r["filed"] >= cutoff}

    # --- build sections ---
    sections: dict[str, list[dict]] = {k: [] for k in
        ("pipeline", "pricings", "amends", "mergers", "votes", "pipes", "sponsors", "exchanges")}
    for r in history.values():
        form = r["form"].upper()
        rec = {k: r[k] for k in ("company", "form", "cik", "filed", "path")}
        if r.get("blurb"):
            rec["blurb"] = r["blurb"]
        placed = False
        for section, forms in FORMS.items():
            if form in forms:
                sections[section].append(rec)
                placed = True
                break
        if not placed and form in EIGHTK_FORMS:
            seen = set()
            for item_text in r.get("items", []):
                lt = item_text.lower()
                for needle, (section, note) in EIGHTK_ITEM_MAP:
                    if needle in lt and section not in seen:
                        seen.add(section)
                        sections[section].append({**rec, "note": note})
    for lst in sections.values():
        lst.sort(key=lambda r: r["filed"], reverse=True)

    # --- non-EDGAR sources ---
    print("fetching media + regulatory RSS")
    media = fetch_rss_items(NEWS_RSS, ua)[:6]
    regs = [
        {k: it[k] for k in ("title", "link", "published", "source")}
        for it in fetch_rss_items(SEC_PRESS_RSS, ua)
        if REG_KEYWORDS.search(it["title"] + " " + it["summary"])
    ][:5]
    for it in media:
        it.pop("summary", None)

    feed = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "history_days": HISTORY_DAYS,
        "source": "SEC EDGAR daily index + 8-K item headers; Google News RSS; SEC.gov RSS",
        **sections,
        "media": media,
        "regs": regs,
    }
    os.makedirs("data", exist_ok=True)
    with open(LATEST_PATH, "w") as f:
        json.dump(feed, f, indent=1)
    with open(HISTORY_PATH, "w") as f:
        json.dump({"rows": history}, f, indent=1)
    print("wrote", LATEST_PATH, "|", " ".join(f"{k}={len(v)}" for k, v in sections.items()),
          f"media={len(media)} regs={len(regs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
