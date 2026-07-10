# WIP notes — Community Report (experimental)

**Status: work in progress.** `feed.py` + `index.html` (the viewer) are the
stable product. `report.py` + `report.html` are the experimental analysis
layer — expect rough edges.

## What works now

- `report.py` — pure-counting analyzer (no models, no API): distinctive
  lexicon vs general English (Norvig list, auto-downloads on first run),
  coined vs repurposed word split, most-mentioned people (member handles +
  @mentions + first-names dictionary), patient zero (first speaker + adopters,
  links to founding message), emerging/dying words, words-over-time,
  conversation culture (reply latency, thread depth, question/unanswered,
  lengths, richness), new voices + retention, spark/last-word, heartbeat,
  emoji palette. Filters: platform-word stoplist, person detection,
  distinctiveness ratio ≥3 for emerging/dying.
- `report.html` — renders all of it, ayu palette, per-server chips,
  crosshair tooltip on the timeline, data tables for accessibility.
- Run: `python3 report.py --archive <dirs of beamline *.jsonl>` or bare
  (falls back to `feed/feed.json`, 24h window). Output: `report/report.json`.
- The committed `report/report.json` was built from ORI's full 17-month
  archive (commons channels only — member logs unlocked after that export).

## Open threads (the refinement list)

1. **External orbit** — people mentioned but never authoring (aella, xelia)
   leak into the coined lexicon; wants member-vs-outside-world mention split
   + a community-editable alias/exclusion file.
2. **Emerging words are event-dominated** (one dramatic conversation owns the
   quarter) — separate bursty event-words from sustained identity-words
   (burstiness metric).
3. **Cross-community TF-IDF** — every report stores `rates_per_million`
   (top 300 words) so "ORI vs LessWrong" is a subtraction once Omar's other
   communities are in. This is the real fix for platform-word noise.
4. Modern English baseline (Norvig is 2006 — "onboarding" scores as coined);
   consider the wordfreq package data as an upgrade.
5. Re-run the full archive export with unlocked logs, rebuild beamline data,
   regenerate report (the killed pass-3 export).
6. Deployed-version plan (issue #2): shared bot via Discord Team, hourly
   Action first, Cloudflare pull-on-read worker later, per-guild feeds keyed
   by server id, wiping policy = kick bot → feed deleted next cycle.
7. Landing-page states for index.html: no-params → product pitch + add-bot
   button; `?guild_id=` → "feed within the hour"; `?feed=` → viewer (done).

## Working remotely

```
git clone git@github.com:faltz009/ORI-feed.git
python3 feed.py --setup          # token + prefs (token.txt is gitignored)
python3 feed.py                  # fresh 24h pull
python3 report.py                # report from the 24h feed
python3 -m http.server 8000      # view both pages
```

The 17-month archive lives only on the home machine (`~/ori-archive`) — for
full-history reports remotely, copy `~/ori-archive/beamline/data/` over
privately (9 MB); don't commit it here.
