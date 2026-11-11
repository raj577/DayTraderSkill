---
name: in-scanner
description: Scan NSE cash equities for pre-open setups or opening-range breakouts. Use when the user wants Indian stocks to watch for an intraday trade, asks "what's setting up in NSE", "scan the Indian market", wants a watchlist from NSE bhavcopy, or wants opening-range breakouts after 09:31 IST.
---

# Indian Market Scanner (NSE)

Find charts that match a rule. A scan is never a recommendation to buy.

`scan.py` is next to this file. Run it with `python`, using the repo root as the
working directory so `common.py` is found:

```bash
python in-scanner/scan.py universe
python in-scanner/scan.py daily
python in-scanner/scan.py orb --at 10:15
```

## Before the open (daily)

```bash
python in-scanner/scan.py daily
```

Reads NSE bhavcopy (EQ series) for the last 40 sessions, filters to liquid
names (default: INR 20+ price, INR 5 lakh+ average turnover), then tags charts:

- `breakout_20d` - close above the prior 20-session high
- `bull_flag` - 10%+ run in 15 sessions, then a tight 5-session range
- `reclaim_ma20` - close back above the 20-day average after closing below
- `oversold` - 14-day RSI under 30

Writes `trader/watchlist.json`. Show the user the ranked table, not the raw JSON.

Useful flags: `--symbols RELIANCE,TCS`, `--limit 50`, `--min-turnover-lacs 1000`,
`--refresh` (re-download bhavcopy), `--json`, `--now "2026-11-12 07:30"`.

Bhavcopy is published after 18:00 IST, so a pre-open run uses the previous
session's close - exactly what a breakout scan wants. On a holiday the scan runs
on the last session; say so.

## After 09:30 (opening range)

```bash
python in-scanner/scan.py orb --at 10:15
```

Stage 1 checks 5-minute closes via Yahoo spark (20 symbols per request), then
stage 2 re-checks candidates with real bars and reports the first completed bar
that closed above the opening-range high, its time, and how far over it went.

Rules that matter:
- The range is 09:15-09:30 by default (`--or-minutes`).
- Nothing prints before 09:31 IST; the tool says the range is still forming.
- After `no_entries_after` (14:45) the tool refuses unless `--allow-late`.
- A `CIRCUIT-LOCKED` marker means high == low in that bar; do not chase it.
- `--max-confirm 200` bounds stage-2 fetches; a `PARTIAL` line says when it bit.

Yahoo 5-minute data is free and unofficial; treat it as a screen, confirm on the
broker chart.

## What to tell the user

- Which rule matched and on what date, with the liquidity that got it through.
- That a scan narrows attention; it does not predict direction.
- NSE session is 09:15-15:30 IST, pre-open auction 09:00-09:08, and MIS positions
  need a plan to be flat before the broker's ~15:15-15:25 square-off.
- Only cash equity. No shorts, no options (the planner is long-only).

Next: check news, then `in-entry/entry.py` to size a plan.
