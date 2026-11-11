---
name: in-timemachine
description: Replay one NSE session on 5-minute bars to see what a mechanical opening-range break would have done. Use when the user asks "what would have happened if I bought the breakout", wants to backtest an intraday rule, or asks to review a session bar by bar.
---

# Time Machine (NSE, 5-minute bars)

`timemachine.py` replays a session in order. It is evidence about prices, not a
promise about tomorrow.

```bash
python in-timemachine/timemachine.py RELIANCE
python in-timemachine/timemachine.py TCS --date 2026-11-06 --or 15 --target-r 2
python in-timemachine/timemachine.py INFY --fail-window 10 --json
```

## What it prints

1. The 09:15-09:30 opening range (high, low, width).
2. The first completed bar that closed above the OR high: time and price.
3. Mechanical size from the risk cap and 2R target, plus round-trip charges.
4. Whether the target or stop was hit first, and the R result.
5. Best and worst excursion after entry (MFE/MAE).
6. Whether the break failed - a close back under the OR high within
   `--fail-window` minutes.

## Limits to state plainly

- Yahoo keeps about 60 days of 5-minute data (free, unofficial). Older dates
  return nothing; say so instead of inventing bars.
- One path was replayed. It cannot show the paths prices did not take.
- No slippage model beyond the limit in the plan; a fast market fills worse.
- Opening range rules are sensitive to the window (15 vs 30 minutes). Show the
  user how the result changes when they change `--or`.
- Replaying with hindsight always looks cleaner than trading it live. The
  failure check and costs are there to keep the replay honest.

## How to use it

- Compare two symbols or two dates, not 200 sessions; this is a microscope.
- If the break failed early, note that a rule requiring a second close above the
  OR high would have avoided it.
- Follow with `in-entry/entry.py` only when the user has a rule they can repeat,
  not because a replay looked good.

Offline: `--offline DIR` reads `SYMBOL.json` Yahoo chart files (a 1-month, 5-minute
chart payload) for reproducible tests.
