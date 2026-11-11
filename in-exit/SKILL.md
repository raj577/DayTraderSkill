---
name: in-exit
description: Audit open NSE intraday positions against their stops, targets, and the 15:12 IST MIS square-off. Use when the user asks "should I exit", "check my positions", "is my stop hit", or wants an intraday position review near the close.
---

# Position Exit Advisor (NSE)

`exit.py` is next to this file. It reads `trader/portfolio.json` (and the plan
for the same symbol), then prints one verdict per position.

```bash
python in-exit/exit.py
python in-exit/exit.py --now "2026-11-12 15:05"
python in-exit/exit.py --offline prices.json
```

## Verdicts

- **EXIT NOW** - last price at/below the stop; or now past `flat_by` (15:12) for
  a MIS position. A breached stop is an exit at the market, not a wider stop.
- **TAKE OR TRAIL** - price at/above the target. The plan's reward is reached;
  take it or move the stop, but decide now.
- **HOLD** - inside the plan. With an R multiple >= 1.0, suggest moving the stop
  to breakeven, not adding.

Also shown: P&L in rupees, R multiple, stop, target, and the flat-by time.

Prices come from `trader/prices.json` when it is under 5 minutes old, else from
Yahoo quotes (free, unofficial). If a quote fails, the row says `NO PRICE` -
never guess.

## Why the Indian close needs its own rule

- NSE cash session ends 15:30 IST, but discount brokers square off MIS around
  15:15-15:25. `flat_by` 15:12 leaves room to exit on your own terms.
- A stock in a circuit band, an F&O ban, or with a halted book may not fill. The
  verdict says what to try; the fill is not guaranteed.
- Muhurat sessions (Diwali) run about 18:15-19:15; `flat_by` must be earlier that
  day. Check `market_state` before assuming 15:12.

## What to tell the user

- The verdict, the number that triggered it, and the flat-by clock.
- That a losing intraday trade is a decision, not a discount - do not average down.
- That a profitable position is protected by a trailing stop, not hope.
- Exit 1 means no open positions; that is fine.
