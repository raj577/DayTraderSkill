---
name: in-alerts
description: Print the NSE price levels worth an alert - opening range, previous close, 20-day high, MA20, and round numbers. Use when the user asks "what levels should I watch", wants alerts set for an Indian stock, or needs intraday key levels.
---

# Alert Levels (NSE)

Print levels; set them in the broker app yourself. Nothing is pushed.

```bash
python in-alerts/alerts.py RELIANCE TCS
python in-alerts/alerts.py
python in-alerts/alerts.py --or-minutes 30 --json
```

With no symbols it reads the scanner watchlist.

## Levels printed

- **Opening range** high and low (09:15-09:30 default) with up/down break prices.
  Not printed until the range is complete.
- **Previous close** and the gap from it - the number a gap-fill trade watches.
- **20-day high** and **MA20** from daily bars (context, not intraday triggers).
- **Today's high/low** so far.
- **Nearest round numbers** (step scales with price: 5 / 10 / 25 / 50 INR).

## India notes

- The pre-open auction (09:00-09:08) sets the open; a gap through a level makes
  the level an exit reference, not an entry.
- Round numbers matter less than in some markets, but stops cluster there.
- A level inside a circuit band may never print; the row simply won't update.
- Re-run after 09:31 for a real opening range; before that it says "still forming".

## What to tell the user

- The exact prices to enter into the alert screen, and which side each one breaks.
- That an alert is a prompt to look at the chart, not an order.
- Pair with `in-exit/exit.py` for positions and `in-entry/entry.py` for plans.
