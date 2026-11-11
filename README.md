# India Day-Trader Skills

Nine agent skills for trading Indian cash equities (NSE) with an AI agent:
scan, read the news, plan, size, gate, ticket, manage, replay, and set alerts.

I built this. It is the Indian-market rebuild of the MIT-licensed
`day-trader-skill` pack, and I did not just translate the labels - I rewrote the
market mechanics underneath: NSE session 09:15-15:30 IST, MIS square-off at
15:12, rupee-denominated costs, circuit and F&O-ban checks, and NSE bhavcopy as
the daily bar source because Yahoo's spark endpoint has no volume.

**Nothing here places an order.** It reads public data, does the arithmetic, and
writes files under `trader/`. Pulling the trigger stays your job - which is
exactly how it should be.

```bash
python in-scanner/scan.py universe      # NSE EQ list -> trader/universe.txt
python in-scanner/scan.py daily         # pre-open setups -> trader/watchlist.json
python in-news/news.py                  # fresh headlines for the watchlist
python in-entry/entry.py RELIANCE --entry 1335 --stop 1310 --target 1390
python in-risk/check.py
python in-broker/ticket.py              # ticket, not an order
python in-exit/exit.py                  # during the session
```

## Why mine is different

- **Daily bars come from NSE bhavcopy** (`sec_bhavdata_full`), not a spark
  endpoint with no volume. Indian liquidity filtering needs turnover. The URL can
  serve a republished prior session on a holiday, so the `DATE1` column decides
  the real session - never the URL.
- **Intraday bars come from Yahoo `.NS` charts.** Free, unofficial, and
  percent-encoded for symbols like `M&M` so they actually resolve.
- **Costs are Indian.** Brokerage cap INR 20 or 0.03% per leg, STT 0.025% on
  sell, exchange 0.00297%, SEBI 0.0001%, stamp 0.003% on buy, GST 18%.
- **MIS is flat by 15:12**, before the broker squares off around 15:15-15:25.
- **No bracket orders** for cash equity at most brokers: the ticket emits an
  SL-M stop leg and treats the target as an alert.
- **Circuit and ban checks** are first-class. An F&O ban or a circuit-locked bar
  can make a planned exit impossible, and the tools say so instead of pretending
  a fill exists.
- **Long only, cash only.** No shorting, no options, no leverage beyond the MIS
  product.

## The nine skills

| skill | script | job |
| --- | --- | --- |
| `in-scanner` | `in-scanner/scan.py` | universe, pre-open setups, opening-range breakouts |
| `in-news` | `in-news/news.py` | new headlines + move from the previous close |
| `in-entry` | `in-entry/entry.py` | checked, sized plan in `trader/plan.json` |
| `in-exit` | `in-exit/exit.py` | stop / target / time verdicts on open positions |
| `in-timemachine` | `in-timemachine/timemachine.py` | replay one session on 5-minute bars |
| `in-budget` | `in-budget/size.py` | risk-based position size and breakeven |
| `in-risk` | `in-risk/check.py` | pre-trade gate, one PASS/FAIL per rule |
| `in-broker` | `in-broker/ticket.py` | order ticket + paper book in `trader/` |
| `in-alerts` | `in-alerts/alerts.py` | OR, prev close, 20-day high, MA20, round numbers |

Each skill is a directory with a `SKILL.md` (frontmatter + playbook) and its
Python entry point beside it. The agent reads the playbook, then runs the script.

## Requirements

- Python 3.9+ and **standard library only**. No `pip install`, no virtualenv,
  no dependency drift.
- On Windows use `python`, not `python3` - the Microsoft Store stub is not Python.

## Quick start

Run everything from the repo root so `common.py` is importable:

```bash
git clone https://github.com/<you>/trader-skill-india.git
cd trader-skill-india

python in-scanner/scan.py universe
python in-scanner/scan.py daily
python in-news/news.py
```

Every tool accepts:

- `--now "2026-11-12 10:15"` - pin the clock (great for testing and replays)
- `--offline` - use saved data, fail network calls fast
- `--json` - machine-readable output

## Use it in opencode

This is the part I care about. opencode discovers `SKILL.md` files automatically,
so these nine become first-class tools your agent can load on demand.

**Option 1 - point opencode at the clone.** Add a `skills` entry to your
`opencode.json` (this repo ships one already):

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "skills": ["./"]
}
```

Relative paths resolve from opencode's working directory. Start opencode **inside
the cloned repo**, or use an absolute/`~/` path instead:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "skills": ["~/trader-skill-india"]
}
```

**Option 2 - make them global.** Copy (or symlink) the skill directories into
your global skills folder:

```bash
# macOS / Linux
mkdir -p ~/.config/opencode/skills
ln -s "$PWD"/in-* ~/.config/opencode/skills/
```

```powershell
# Windows (run in the repo root)
New-Item -ItemType Directory -Force "$env:USERPROFILE\.config\opencode\skills" | Out-Null
Get-ChildItem -Directory -Filter "in-*" | ForEach-Object {
  New-Item -ItemType Junction -Path "$env:USERPROFILE\.config\opencode\skills\$($_.Name)" -Target $_.FullName
}
```

Then in opencode, just talk to it:

```
scan the Indian market for pre-open setups
plan a trade in RELIANCE with entry 1335, stop 1310, target 1390
size this trade for INR 1,000 risk
am I within my risk limits?
what would have happened if I bought the breakout yesterday?
```

You can also force a specific skill by mentioning its ID, e.g. `@in-entry` or
`@in-risk`. The agent will load that playbook and run the matching script.

The same directories also work in Claude Code and any agent that reads
`SKILL.md`-style skills; `.claude-plugin/` carries the plugin manifest.

## The flow I actually run

1. **Scan** - `in-scanner/scan.py daily` before the open, or `orb` after 09:31
   IST for opening-range breakouts. It narrows attention; it does not predict.
2. **News** - `in-news/news.py` checks fresh headlines and the move from the
   previous close.
3. **Plan** - `in-entry/entry.py` produces a checked, sized plan with a stop
   below structure, reward:risk, and charges. It refuses plans that break your
   rules.
4. **Gate** - `in-risk/check.py` says PASS/FAIL per rule before any order.
5. **Ticket** - `in-broker/ticket.py` writes an order ticket and a paper book.
   Still not an order.
6. **Exit** - `in-exit/exit.py` audits open positions against stop, target, and
   the 15:12 square-off.
7. **Review** - `in-timemachine/timemachine.py` replays the session bar by bar so
   you can see what your rule would have done.

## Configuration

Copy `config.example.json` to `trader/config.json` (or call
`common.ensure_config`) and tune:

- `budget`, `cash_buffer_pct`
- `max_loss_per_trade`, `max_daily_loss`, `max_trades_per_day`, `max_orders_per_day`
- `max_position_pct`, `max_order`, `min_order`, `min_reward_risk`
- `no_entries_before` 09:20, `no_entries_after` 14:45, `flat_by` 15:12
- `min_price`, `min_turnover_lacs`, `blocked`, `costs`

Environment variables: `TRADER_DIR` (state directory, default `trader/`) and
`TRADER_OFFLINE=1` (fail network calls fast, for tests).

## Files written under `trader/`

`universe.txt`, `watchlist.json`, `plan.json`, `orders.json`, `trades.json`,
`portfolio.json`, `prices.json`, `config.json`, `state/news.json`,
`state/nse-holidays.json`, `cache/bhav/*.csv`.

## Tests

```bash
python -m unittest discover -s tests -v
```

The suite is offline: synthetic bhavcopy CSVs, synthetic chart JSON,
`TRADER_OFFLINE=1`, and a temporary `TRADER_DIR`. No network, no real money, no
clock dependence.

## What I deliberately left out

- **`in-analyst`** (the US pack's SEC XBRL reader) has no honest Indian
  equivalent. NSE/BSE publish results as PDFs and XBRL under varied taxonomies
  with no free machine feed like SEC company facts. A scraper there would fake
  precision, so I left it out. Read the filing.
- **`in-pelosi`** (a US politician trade tracker) does not port. India has no
  STOCK Act analogue and no machine-readable MP disclosure feed; the disclosures
  are scanned PDFs published late. That is a weak, stale signal, so I did not
  ship it.

I shipped nine real tools and a real review loop instead of shells with no data
behind them.

## Data sources and honesty

- **NSE archives**: symbol list, bhavcopy, holiday master, F&O ban list.
- **Yahoo Finance**: 5-minute charts, quotes, RSS headlines. Free and unofficial;
  it can lag, rate-limit, or be wrong. Confirm on your broker chart.
- A scan narrows attention. A plan bounds risk. Neither predicts a price.
- Stops are not guarantees: gaps, circuits, and halts can fill far worse.

## Licence

MIT, following the original pack. Trading is your risk; this software is not
investment advice.
