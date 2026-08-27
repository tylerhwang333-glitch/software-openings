#!/usr/bin/env python3
"""
Software engineering internship watcher.

Polls the Simplify/Pitt CSC internship feed (and optionally company ATS boards
directly), filters for roles you care about, and posts new ones to Discord/Slack.

First run:   python job_bot.py --seed     (marks everything as seen, no spam)
Every run:   python job_bot.py
Dry run:     python job_bot.py --dry-run  (prints instead of posting)
"""

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import ats
import jobright

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
# Optional: separate channel for direct-ATS alerts. Falls back to WEBHOOK_URL.
WEBHOOK_URL_ATS = os.environ.get("WEBHOOK_URL_ATS", "")
WEBHOOK_KIND = os.environ.get("WEBHOOK_KIND", "discord")  # "discord" or "slack"

FEEDS = [
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
    # Same schema, different community. Uncomment for more coverage:
    # "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/.github/scripts/listings.json",
]

# A title must contain at least one of these (case-insensitive).
INCLUDE = [
    "software engineer",
    "software engineering",
    "software developer",
    "swe intern",
    "swe co-op",
    "backend engineer",
    "backend developer",
    "back-end",
    "frontend engineer",
    "frontend developer",
    "front-end",
    "full stack",
    "full-stack",
    "fullstack",
    "web developer",
    "application developer",
    "applications engineer",
    "platform engineer",
    "infrastructure engineer",
    "systems engineer",
    "site reliability",
    "devops engineer",
    "cloud engineer",
    "mobile engineer",
    "mobile developer",
    "ios developer",
    "ios engineer",
    "android developer",
    "android engineer",
    "machine learning engineer",
    "ml engineer",
]

# ...and none of these. Tune this — it's what keeps the noise down.
EXCLUDE = [
    "phd",
    "graduate student",
    "research scientist",
    "principal",
    "staff",
    "senior",
    "manager",
    "hardware engineer",
    "mechanical engineer",
    "electrical engineer",
    "civil engineer",
    "sales engineer",
    "support engineer",
    "field engineer",
]

# Only alert on these terms. Empty list = any term.
TERMS = []

# Optional location filter, e.g. ["CA", "Remote", "New York"]. Empty = anywhere.
LOCATIONS = []

# Drop anything that isn't clearly US-based. ATS boards are global, so this is
# what keeps Singapore/Mumbai/Buenos Aires roles out of the channel.
US_ONLY = True

# Explicit non-US signals, checked first: "Toronto, ON, Canada" is out even
# though "ON" would otherwise look like a state code.
_NON_US = [
    "canada", "mexico", "brazil", "argentina", "chile", "colombia", "peru",
    "costa rica", "united kingdom", "england", "scotland", "ireland", "france",
    "germany", "spain", "portugal", "italy", "netherlands", "belgium",
    "sweden", "norway", "denmark", "finland", "poland", "romania",
    "switzerland", "austria", "greece", "turkey", "israel", "india", "china",
    "japan", "korea", "singapore", "malaysia", "indonesia", "thailand",
    "vietnam", "philippines", "australia", "new zealand", "south africa",
    "egypt", "nigeria", "kenya", "uae", "dubai", "qatar", "saudi arabia",
    "emea", "apac", "latam", "emirates", "uk", "london", "dublin", "paris",
    "berlin", "munich", "amsterdam", "zurich", "madrid", "barcelona", "milan",
    "warsaw", "prague", "bucharest", "stockholm", "toronto", "vancouver",
    "montreal", "ottawa", "bangalore", "bengaluru", "hyderabad", "mumbai",
    "delhi", "gurgaon", "pune", "chennai", "noida", "tokyo", "osaka", "seoul",
    "beijing", "shanghai", "shenzhen", "taipei", "hong kong", "sydney",
    "melbourne", "auckland", "tel aviv", "sao paulo", "guadalajara", "bogota",
    "buenos aires", "santiago", "lima", "manila", "jakarta", "bangkok",
    "kuala lumpur", "ho chi minh",
]

_US_STATES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming", "puerto rico",
    "district of columbia",
]

# Common US metros that often appear without a state.
_US_CITIES = [
    "new york city", "nyc", "san francisco", "sf bay area", "bay area",
    "seattle", "boston", "chicago", "austin", "atlanta", "denver", "dallas",
    "houston", "miami", "philadelphia", "phoenix", "san diego", "san jose ca",
    "los angeles", "portland", "pittsburgh", "detroit", "minneapolis",
    "charlotte", "nashville", "cincinnati", "columbus", "cleveland",
    "st. louis", "kansas city", "salt lake city", "las vegas", "orlando",
    "tampa", "raleigh", "durham", "arlington", "bentonville", "sunnyvale",
    "mountain view", "palo alto", "santa clara", "cupertino", "redmond",
    "bellevue", "hoboken", "jersey city", "mclean", "reston", "bethesda",
]

_NON_US_RE = re.compile(r"\b(" + "|".join(map(re.escape, _NON_US)) + r")\b")
_US_TEXT_RE = re.compile(
    r"\b(" + "|".join(map(re.escape, _US_STATES + _US_CITIES)) + r"|"
    r"united states|usa|u\.s\.a?\.?|us remote|remote in usa|nationwide)\b")
# Two-letter state codes, matched case-sensitively so "in"/"or"/"me" don't hit.
_US_ABBR_RE = re.compile(
    r"\b(A[LKZR]|C[AOT]|DE|DC|FL|GA|HI|I[DLNA]|K[SY]|LA|M[EDAINSOT]|"
    r"N[EVHJMYCD]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[TA]|W[AVIY])\b")


def is_us(job):
    """True if the listing looks US-based. Unknown locations are dropped."""
    locs = ", ".join(job.get("locations") or []).strip()
    if not locs:
        return False
    low = locs.lower()
    if _NON_US_RE.search(low):
        return False
    if _US_TEXT_RE.search(low) or _US_ABBR_RE.search(locs):
        return True
    # Bare "Remote" with no country attached — usually US on these boards.
    return "remote" in low

# Ignore anything first posted more than this many days ago. Guards against a
# feed re-flagging an old listing as active and pinging you about a stale role.
MAX_AGE_DAYS = 14

STATE_FILE = Path(__file__).parent / "seen.json"
UA = {"User-Agent": "Mozilla/5.0 (job-watcher)"}


# ---------------------------------------------------------------------------
# FETCH + FILTER
# ---------------------------------------------------------------------------

def get_json(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
        return json.loads(r.read().decode())


def fetch_all():
    jobs = []
    for url in FEEDS:
        try:
            data = get_json(url)
            jobs.extend(data)
            print(f"  fetched {len(data):,} from {url.split('/')[4]}")
        except Exception as e:
            print(f"  WARN: {url} failed: {e}", file=sys.stderr)
    jobs.extend(jobright.fetch_jobright(SSL_CTX))
    jobs.extend(ats.fetch_ats(SSL_CTX))
    return jobs


def matches(job):
    if not (job.get("active") and job.get("is_visible", True)):
        return False

    title = (job.get("title") or "").lower()
    if str(job.get("source", "")).startswith("ats:") and not any(
            w in title for w in ("intern", "co-op", "coop")):
        return False
    if not any(k in title for k in INCLUDE):
        return False
    if any(k in title for k in EXCLUDE):
        return False

    terms = job.get("terms") or []
    if TERMS and terms and not any(t in terms for t in TERMS):
        return False   # ATS listings have no term tags; keep them

    if US_ONLY and not is_us(job):
        return False

    if LOCATIONS:
        locs = " ".join(job.get("locations") or []).lower()
        if not any(l.lower() in locs for l in LOCATIONS):
            return False

    posted = job.get("date_posted") or 0
    if posted and (time.time() - posted) > MAX_AGE_DAYS * 86400:
        return False

    return True


def job_key(job):
    """Stable identity. Falls back to company+title if the feed has no id."""
    return job.get("id") or f"{job.get('company_name')}::{job.get('title')}"


_CO_SUFFIXES = re.compile(
    r"\b(inc|llc|ltd|corp|corporation|co|company|group|holdings|technologies|"
    r"technology|usa|us|the)\b")
_SEASON = re.compile(
    r"\b(summer|fall|autumn|winter|spring)\b|\b20\d\d\b|\bfy\d\d\b")
_REQ_ID = re.compile(r"\b[a-z]?\d{4,}\b")


def _norm(s, extra=None):
    s = (s or "").lower()
    s = re.sub(r"[‐-―]", "-", s)      # unicode dashes -> ascii
    s = s.replace("&", " and ")
    if extra:
        s = extra(s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def dedupe_key(job):
    """Source-agnostic identity, so the same role from the Simplify feed and
    from a company's own ATS board doesn't get posted twice.

    Normalizes away the things that differ between sources: company suffixes,
    season/year tags, requisition ids, and 'internship' vs 'intern'."""
    company = _norm(job.get("company_name"), lambda s: _CO_SUFFIXES.sub(" ", s))
    title = _norm(job.get("title"),
                  lambda s: _REQ_ID.sub(" ", _SEASON.sub(" ", s)))
    title = re.sub(r"\binternships?\b", "intern", title)
    title = re.sub(r"\b(co op|coop)\b", "intern", title)
    if not company or not title:      # too little to match on — stay unique
        return f"!{job_key(job)}"
    return f"{company.replace(' ', '')}::{title}"


def seen_keys(job):
    """Every key that should mark this job as seen."""
    return {job_key(job), f"dk:{dedupe_key(job)}"}


def drop_cross_source_dupes(jobs):
    """Within one run, keep one copy of each role. Prefer the direct-ATS
    version — it links to the company's own posting."""
    best = {}
    for j in jobs:
        k = dedupe_key(j)
        cur = best.get(k)
        if cur is None:
            best[k] = j
            continue
        is_ats = lambda x: str(x.get("source", "")).startswith("ats:")
        if is_ats(j) and not is_ats(cur):
            best[k] = j
    return list(best.values())


# ---------------------------------------------------------------------------
# NOTIFY
# ---------------------------------------------------------------------------

def post(payload, hook=None):
    hook = hook or WEBHOOK_URL
    if not hook:
        print("ERROR: WEBHOOK_URL not set", file=sys.stderr)
        return False
    req = urllib.request.Request(
        hook,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as r:
            return r.status < 300
    except Exception as e:
        print(f"ERROR posting: {e}", file=sys.stderr)
        return False


def human_age(ts):
    """'3h ago' style, for Slack and dry-run output."""
    if not ts:
        return "unknown"
    secs = time.time() - ts
    if secs < 0:
        return "just posted"
    mins = secs / 60
    if mins < 60:
        return "just posted" if mins < 5 else f"{int(mins)}m ago"
    hours = mins / 60
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours / 24
    if days < 14:
        return f"{int(days)}d ago"
    return f"{int(days / 7)}w ago"


def freshness_color(ts):
    """Green under a day, amber under three, grey beyond."""
    if not ts:
        return 0x95A5A6
    hrs = (time.time() - ts) / 3600
    return 0x2ECC71 if hrs < 24 else 0xF1C40F if hrs < 72 else 0x95A5A6


def fmt_line(job):
    loc = ", ".join((job.get("locations") or ["—"])[:3])
    terms = "/".join(job.get("terms") or [])
    return (job["company_name"], job["title"], loc, terms,
            job.get("url", ""), job.get("date_posted") or 0)


def notify_discord(jobs, hook=None):
    # Discord allows 10 embeds per message.
    for i in range(0, len(jobs), 10):
        chunk = jobs[i:i + 10]
        embeds = []
        for j in chunk:
            company, title, loc, terms, url, posted = fmt_line(j)
            embeds.append({
                "title": f"{title[:200]}",
                "url": url,
                "color": freshness_color(posted),
                "fields": [
                    {"name": "Company", "value": company[:100], "inline": True},
                    {"name": "Location", "value": loc[:100], "inline": True},
                    {"name": "Term", "value": terms[:100] or "—", "inline": True},
                    {"name": "Posted", "value": (
                        f"<t:{posted}:d> — <t:{posted}:R>" if posted else "unknown"
                    ), "inline": False},
                ],
            })
        post({"content": f"**{len(chunk)} new SWE role(s)**", "embeds": embeds}, hook)
        time.sleep(1)  # stay under Discord's rate limit


def notify_slack(jobs, hook=None):
    for i in range(0, len(jobs), 20):
        chunk = jobs[i:i + 20]
        lines = []
        for j in chunk:
            company, title, loc, terms, url, posted = fmt_line(j)
            lines.append(f"• <{url}|*{title}*> — {company} · {loc} · {terms}"
                         f" · _posted {human_age(posted)}_")
        post({
            "text": f"{len(chunk)} new SWE role(s)",
            "blocks": [{
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(lines)[:2900]},
            }],
        }, hook)
        time.sleep(1)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def dispatch(jobs):
    """Route ATS-sourced jobs to their own channel when one is configured."""
    notify = notify_slack if WEBHOOK_KIND == "slack" else notify_discord
    is_ats = lambda j: str(j.get("source", "")).startswith("ats:")
    if WEBHOOK_URL_ATS:
        ats_jobs = [j for j in jobs if is_ats(j)]
        feed_jobs = [j for j in jobs if not is_ats(j)]
        if ats_jobs:
            notify(ats_jobs, WEBHOOK_URL_ATS)
        if feed_jobs:
            notify(feed_jobs)
    else:
        notify(jobs)


def _discord_heartbeat(msg, hook, state, key, repost=False):
    """Post the heartbeat once, then edit that same message every later run.

    With repost=True (channel just got new job posts), delete and re-post
    instead, so the heartbeat stays the newest message in the channel.
    """
    if not hook:
        return
    ids = state.setdefault("heartbeat", {})
    body = json.dumps({"content": msg}).encode()
    headers = {"Content-Type": "application/json", **UA}

    mid = ids.get(key)
    if mid and repost:  # drop the old message; fresh one lands below the jobs
        req = urllib.request.Request(f"{hook}/messages/{mid}",
                                     method="DELETE", headers=dict(UA))
        try:
            with urllib.request.urlopen(req, timeout=30, context=SSL_CTX):
                pass
        except Exception:
            pass  # already gone — fine either way
        mid = None

    if mid:  # try editing the existing message
        req = urllib.request.Request(f"{hook}/messages/{mid}", data=body,
                                     method="PATCH", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30, context=SSL_CTX):
                return
        except Exception:
            pass  # message deleted or id stale — post a fresh one

    sep = "&" if "?" in hook else "?"
    req = urllib.request.Request(f"{hook}{sep}wait=true", data=body,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as r:
            ids[key] = json.loads(r.read().decode()).get("id")
    except Exception as e:
        print(f"ERROR heartbeat: {e}", file=sys.stderr)


def post_status(fresh, all_jobs, state):
    """Self-editing heartbeat per channel: what was checked, and when."""
    is_ats = lambda j: str(j.get("source", "")).startswith("ats:")
    ats_new = sum(1 for j in fresh if is_ats(j))
    feed_new = len(fresh) - ats_new
    feed_total = sum(1 for j in all_jobs if not is_ats(j))
    stamp = f"<t:{int(time.time())}:R>"

    ats_msg = (f"Checked for SWE openings from: "
               f"{', '.join(ats.company_names())} — last run {stamp}")
    if ats_new:
        ats_msg += f" — **{ats_new} new this run**"
    feed_msg = (f"Checked {feed_total:,} Simplify + Jobright feed listings"
                f" for SWE openings — last run {stamp}")
    if feed_new:
        feed_msg += f" — **{feed_new} new this run**"

    if WEBHOOK_KIND == "slack":  # Slack webhooks can't edit; post plainly
        post({"text": ats_msg}, WEBHOOK_URL_ATS or WEBHOOK_URL)
        post({"text": feed_msg}, WEBHOOK_URL)
        return
    _discord_heartbeat(ats_msg, WEBHOOK_URL_ATS or WEBHOOK_URL, state, "ats",
                       repost=ats_new > 0)
    _discord_heartbeat(feed_msg, WEBHOOK_URL, state, "main",
                       repost=feed_new > 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true",
                    help="mark everything currently open as seen, send nothing")
    ap.add_argument("--dry-run", action="store_true",
                    help="print matches instead of posting")
    ap.add_argument("--check-ats", action="store_true",
                    help="verify every configured ATS slug responds, then exit")
    ap.add_argument("--test", type=int, metavar="N", nargs="?", const=3,
                    help="post the N newest matches to check formatting; "
                         "does not modify seen.json")
    args = ap.parse_args()

    if args.check_ats:
        sys.exit(0 if ats.check_ats(SSL_CTX) else 1)

    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}] polling...")
    all_jobs = fetch_all()
    hits = [j for j in all_jobs if matches(j)]
    deduped = drop_cross_source_dupes(hits)
    if len(deduped) < len(hits):
        print(f"  {len(hits)} listings match your filters"
              f" ({len(hits) - len(deduped)} cross-source duplicates merged)")
    else:
        print(f"  {len(hits)} listings match your filters")
    hits = deduped

    state = {}
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
    seen = set(state.get("seen", []))

    fresh = [j for j in hits if not (seen_keys(j) & seen)]
    fresh.sort(key=lambda j: j.get("date_posted", 0), reverse=True)
    print(f"  {len(fresh)} are new since last run")

    if args.test:
        sample = sorted(hits, key=lambda j: j.get("date_posted", 0),
                        reverse=True)[:args.test]
        print(f"  TEST: posting {len(sample)} newest match(es), state untouched")
        dispatch(sample)
        return

    if args.seed:
        state["seen"] = sorted(set().union(*(seen_keys(j) for j in hits))
                               if hits else [])
        STATE_FILE.write_text(json.dumps(state))
        print(f"  seeded {len(hits)} listings. Future runs alert on new ones only.")
        return

    if fresh and args.dry_run:
        for j in fresh:
            company, title, loc, terms, url, posted = fmt_line(j)
            print(f"    [{human_age(posted):>12}] {company} | {title} | {loc}")
    elif fresh:
        dispatch(fresh)
        print(f"  posted {len(fresh)}")

    # Persist. Keep seen keys for everything currently matching so a role that
    # briefly disappears from the feed doesn't re-alert when it comes back.
    # post_status may stash heartbeat message ids in state, so save after.
    if not args.dry_run:
        post_status(fresh, all_jobs, state)
        for j in hits:
            seen |= seen_keys(j)
        state["seen"] = sorted(seen)
        STATE_FILE.write_text(json.dumps(state))


if __name__ == "__main__":
    main()
