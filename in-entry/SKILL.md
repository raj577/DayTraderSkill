---
name: in-entry
description: Turn an NSE chart idea into a checked, sized trade plan with a stop, target, risk in rupees, and charges. Use when the user says "plan a trade in RELIANCE", "how many shares should I buy", "where should my stop be", or asks to size an intraday Indian trade.
---

# Entry Planner (NSE cash equity, intraday)

A plan is a decision written down before the money moves. `entry.py` refuses
plans that break the owner's rules and writes `trader/plan.json` when they pass.

```bash
python in-entry/entry.py RELIANCE --entry 1335 --stop 1310 --target 1390
python in-entry/entry.py TCS --entry auto --stop 3980 --target 4120
python in-entry/entry.py INFY --entry 1520 --stop atr
```

## What it checks

1. **Window** - entries only between `no_entries_before` (09:20) and
   `no_entries_after` (14:45) IST unless `--allow-outside-hours`.
2. **Stop below structure** - the stop must sit at or below the higher of the
   5-session swing low and today's opening-range low. A stop above support gets
   hit before support is even tested; `--allow-untested` overrides, with a warning.
3. **Stop distance** - warns under 0.3%, over 8%, or inside half an ATR14.
4. **Reward:risk** - default minimum 1.0 (`--min-rr`).
5. **Size** - `shares = floor(risk / (entry - stop))`, then capped by
   `max_position_pct` of budget and by free cash after the buffer.
6. **Charges** - brokerage cap, STT on sell, exchange, SEBI, stamp, GST; refuses
   when round-trip charges exceed the risk cap.
7. **Ban/blocked lists** - F&O ban symbols and the user's blocked list.

Default risk per trade is `max_loss_per_trade` (INR 1,000 in the sample config);
the cap is also limited by `max_daily_loss`.

## Reading the result

```
Plan for RELIANCE: 18 shares at INR 1,335.00, stop INR 1,310.00, target INR 1,390.00.
Risk INR 450.00 (0.45% of budget), reward INR 990.00, R:R 2.20.
Costs INR 26.12 round trip ... Breakeven move: 0.11%.
```

40 shares would fit the INR 1,000 risk cap (25 per share), but 40 x 1,335 is
INR 53,400, over the 25% position limit (INR 25,000), so the size stops at 18.
Whichever cap binds, the plan prints the risk it actually takes.

Then: stop order type, flat-by time, gap risk. A stop is not a guarantee; a gap
or a circuit can fill far worse.

## Rules of engagement

- Long only, cash equity only. Shorting and options are out of scope.
- MIS (intraday) needs `flat_by` (15:12) - before the broker's square-off.
- Do not move the stop down. If the plan is wrong, exit; do not re-plan mid-trade.
- One plan at a time: an existing `plan.json` needs `--force` to replace.
- `--allow-no-stop` exists but is a bad idea; say so if the user asks.

After a passing plan: `python in-risk/check.py`, then
`python in-broker/ticket.py`.
