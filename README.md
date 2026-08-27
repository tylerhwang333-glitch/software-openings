# data-openings

Watches for new software engineering internship postings and pings a Discord channel when one opens.

## How it works

Three sources, every 30 minutes:

1. **Simplify / Pitt CSC feed** (~14,000 listings) — broad coverage, but curated by hand, so postings show up hours to days after going live.
2. **Jobright.ai repo** (`jobright.py`) — a second curated list, updated hourly. Publishes no JSON, so the markdown table in its README is parsed directly.
3. **Direct ATS polling** (`ats.py`) — hits company job-board APIs (Greenhouse, Lever, Ashby, Workday, SmartRecruiters) for 60 companies, so new roles are caught within one poll instead of waiting on a feed.

Everything is filtered for software engineering roles, deduped, and posted. The workflow commits `seen.json` back to the repo after each run.

Because the same role often appears in more than one source, listings are also deduped **across** sources on a normalized company + title key — season tags, requisition ids, company suffixes, and `internship` vs `intern` are all normalized away. When a role shows up in both a feed and a company's own board, the ATS copy wins so the link points at the original posting.

## Channels

Set `WEBHOOK_URL_ATS` and direct-ATS finds go to their own channel; both feeds (Simplify and Jobright) stay in `WEBHOOK_URL`. If it's unset, everything goes to one channel.

Each channel keeps one **heartbeat** message showing what was checked and when ("last run 3 minutes ago"). It edits itself in place on quiet runs, and re-posts below new jobs so it stays at the bottom.

## Setup

1. Create a Discord webhook: Server Settings → Integrations → Webhooks → New Webhook → copy URL.
2. Add repo secrets (Settings → Secrets and variables → Actions):
   - `WEBHOOK_URL` — main channel
   - `WEBHOOK_URL_ATS` — optional, direct-ATS channel
3. Settings → Actions → General → Workflow permissions → **Read and write**.
4. Seed locally so the first run doesn't dump every open role:
   ```bash
   python3 job_bot.py --seed
   git add seen.json && git commit -m "seed" && git push
   ```
5. Actions tab → **job watch** → Run workflow.

## Commands

```bash
python3 job_bot.py --dry-run     # print matches, post nothing, touch nothing
python3 job_bot.py --check-ats   # verify every ATS board still responds
python3 job_bot.py --test 3      # post 3 newest to Discord, don't touch state
python3 job_bot.py --seed        # mark everything open as seen, post nothing
python3 job_bot.py               # normal run: post new roles, update state
```

Set `WEBHOOK_URL` in your shell before running anything that posts.

## Configuration

Filters, at the top of `job_bot.py`:

| Setting | Purpose |
|---|---|
| `INCLUDE` | Title must contain one of these |
| `EXCLUDE` | Title must contain none of these |
| `TERMS` | e.g. `Summer 2027`; empty = any |
| `US_ONLY` | Drop anything not clearly US-based |
| `LOCATIONS` | Substring match; empty = anywhere |
| `MAX_AGE_DAYS` | Ignore listings older than this |

`EXCLUDE` is what keeps the channel usable. Add to it aggressively.

`US_ONLY` rejects known foreign countries and cities first, then accepts US states, state codes, major metros, and bare "Remote". Anything unrecognized is dropped — so if a US role goes missing, add its location string to `_US_CITIES` in `job_bot.py`.

Companies, in `ats.py` → `COMPANIES`. Each entry is `("ats_kind", "slug")`, where the slug comes from the careers URL:

| ATS | URL | Slug |
|---|---|---|
| Greenhouse | `boards.greenhouse.io/spacex` | `spacex` |
| Lever | `jobs.lever.co/palantir` | `palantir` |
| Ashby | `jobs.ashbyhq.com/cohere` | `cohere` |
| Workday | `nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite` | `nvidia.wd5/NVIDIAExternalCareerSite` |
| SmartRecruiters | `careers.smartrecruiters.com/westerndigital` | `westerndigital` |

Add a friendly display name to `_NAMES` if the slug doesn't title-case nicely — it's used in the embeds and for cross-source dedupe, so it should match how the feeds spell the company. Run `--check-ats` after editing.

Workday boards are too large to pull whole, so they're searched server-side for the terms in `WORKDAY_SEARCHES` (`intern`, `co-op`), capped at 200 results per term per board. SmartRecruiters is paged to 600 per board.

Feeds, in `jobright.py` → `REPOS`. Any jobright-ai repo using the same README table works — just add its raw URL.

## Alert colors

The bar on the left of each embed shows how old the posting is:

- 🟢 Green — under 24h
- 🟡 Amber — 1–3 days
- ⚪ Grey — older, or no date available

Thresholds live in `freshness_color()`.

## Troubleshooting

**Push fails with 403** — workflow permissions are read-only. See setup step 3.

**Runs green but nothing posts** — `WEBHOOK_URL` secret missing or misnamed.

**Duplicate alerts** — `seen.json` isn't being committed. Check the "Save state" step.

**`CERTIFICATE_VERIFY_FAILED` locally on macOS** — run `pip3 install --upgrade certifi`. Only affects local runs; CI is unaffected.

**One board WARNs every run** — the slug moved, or that company blocks datacenter IPs (Walmart does). Confirm with `--check-ats`; delete the line if it stays broken.

**0 matches** — filters are too narrow, or it's the off-season. Postings ramp up August through October.

**A feed suddenly parses 0 rows** — `jobright.py` reads a markdown table; if the repo changes its format, the parser goes quiet rather than erroring. Check with `python3 -c "import jobright,job_bot; print(len(jobright.fetch_jobright(job_bot.SSL_CTX)))"`.

## Notes

Feed listings have a latency floor of an hour or two since the upstream lists are rebuilt on their own schedule. Direct ATS polling has no such floor — it's limited only by the 30-minute cron, which GitHub often runs late.

`seen.json` stores two keys per listing (its source id and its dedupe key) and is never pruned, so it grows over time.
