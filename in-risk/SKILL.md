---
name: in-risk
description: Pre-trade risk gate for NSE intraday orders. Use before placing any order, when the user asks "can I take this trade", "am I within my limits", "have I traded too much today", or wants a go/no-go check on an Indian plan.
---

# Risk Gate (NSE)

The last check before an order: budget, daily loss, trade count, time window,
size, reward:risk, cash, ban lists, and the MIS square-off clock.

```bash
python in-risk/check.py
python in-risk/check.py --plan path/to/plan.json
python in-risk/check.py --now "2026-11-12 10:30"
```

Exit 0 = every blocking check passed. Exit 1 = at least one failed; do not trade.

## Checks

| check | rule |
| --- | --- |
| market_open | NSE trading day, session not closed |
| plan_identity | symbol, entry, stop, positive shares |
| risk_per_trade | (entry-stop)*shares <= max_loss_per_trade |
| position_pct | position <= max_position_pct% of budget |
| order_bounds | min_order <= position <= max_order |
| reward_risk | target set, R:R >= min_reward_risk |
| cash_available | cost <= free cash |
| daily_trades | closed trades today < max_trades_per_day |
| daily_loss | realized today > -max_daily_loss |
| daily_orders | orders today < max_orders_per_day |
| entry_window | now inside no_entries_before..no_entries_after (WARN outside) |
| before_flat_by | now < flat_by (MIS square-off) |
| open_risk | open stop risk <= max_daily_loss |
| blocked_list / fno_ban | symbol not blocked or in the ban period |

Realized P&L comes from `trader/trades.json`; order count from
`trader/orders.json`; positions from `trader/portfolio.json`.

## India-specific notes

- The F&O ban list affects derivatives, but a banned name often has a thin cash
  book; treat the check as a caution, not an absolute bar.
- Circuit filters and halts can make a stop unfillable. A passed check is not a
  guarantee.
- After `flat_by` (15:12) any new MIS entry is refused by the planner; the gate
  repeats it.

## What to tell the user

- The verdict first, then only the failed rules with their numbers.
- A pass is permission, not a reason: a good plan still needs a good chart.
- A fail is not negotiable by argument; change the plan or skip the trade.
