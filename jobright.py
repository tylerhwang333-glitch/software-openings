"""
Jobright.ai internship repos.

Unlike the Simplify feed these repos publish no JSON — the listings live in a
markdown table in README.md, between the TABLE_START/TABLE_END markers. We
parse that table into the same dict shape everything else uses.

Two quirks of the format:
  * A company cell of "↳" means "same company as the row above" — the repo
    uses it to group multiple postings from one employer.
  * Dates are "Jun 04" with no year, so we infer it (see _date_to_epoch).

Add more repos to REPOS; any jobright-ai repo with the same table works.
"""

import json
import re
import sys
import urllib.request
from datetime import datetime, timezone

REPOS = [
    "https://raw.githubusercontent.com/jobright-ai/"
    "2026-Software-Engineer-Internship/master/README.md",
]

_TIMEOUT = 30
_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_JOB_ID = re.compile(r"/jobs/info/([0-9a-zA-Z]+)")
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _date_to_epoch(s, now=None):
    """'Jun 04' -> epoch. No year in the source, so assume the most recent
    occurrence: this year, unless that lands in the future (then last year)."""
    now = now or datetime.now(timezone.utc)
    m = re.match(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})", (s or "").strip())
    if not m:
        return 0
    month = _MONTHS.get(m.group(1).lower())
    if not month:
        return 0
    try:
        d = datetime(now.year, month, int(m.group(2)), tzinfo=timezone.utc)
    except ValueError:
        return 0
    if d > now:                       # e.g. "Dec 20" seen in January
        d = d.replace(year=now.year - 1)
    return int(d.timestamp())


def _cell_link(cell):
    """('Text', 'url') from a markdown cell, url stripped of tracking params."""
    m = _LINK.search(cell or "")
    if not m:
        return re.sub(r"[*`]", "", cell or "").strip(), ""
    return m.group(1).strip(), m.group(2).split("?")[0].strip()


def parse_table(md):
    """Markdown README -> list of normalized job dicts."""
    body = md
    if "TABLE_START" in md and "TABLE_END" in md:
        body = md.split("TABLE_START", 1)[1].split("TABLE_END", 1)[0]

    out, company = [], ""
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 5:
            continue
        if set(cells[0].lower().split()) & {"company"} or set(cells[0]) <= {"-", " "}:
            continue                                   # header / separator

        co_cell, title_cell, loc, model, date = cells[:5]
        if "↳" not in co_cell:                         # else: same as row above
            company = _cell_link(co_cell)[0] or company
        title, url = _cell_link(title_cell)
        if not title or not company:
            continue

        jid = _JOB_ID.search(url)
        loc = re.sub(r"[*`]", "", loc).strip()
        if model.lower() == "remote" and "remote" not in loc.lower():
            loc = f"Remote — {loc}" if loc else "Remote"

        out.append({
            "id": f"jr-{jid.group(1) if jid else abs(hash((company, title, loc)))}",
            "company_name": company,
            "title": title,
            "locations": [loc],
            "url": url,
            "date_posted": _date_to_epoch(date),
            "terms": [],
            "active": True,
            "is_visible": True,
            "source": "jobright",
        })
    return out


def fetch_jobright(ssl_ctx, repos=None):
    """Fetch every configured repo. Failures are logged, not fatal."""
    jobs = []
    for url in (repos or REPOS):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (job-watcher)"})
            with urllib.request.urlopen(req, timeout=_TIMEOUT,
                                        context=ssl_ctx) as r:
                rows = parse_table(r.read().decode())
            jobs.extend(rows)
            print(f"  fetched {len(rows):,} from {url.split('/')[4]}")
        except Exception as e:
            print(f"  WARN jobright {url.split('/')[4]}: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
    return jobs
