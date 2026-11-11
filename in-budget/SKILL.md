---
name: in-budget
description: Size an NSE intraday position from risk, budget, and stop distance, including charges and breakeven. Use when the user asks "how many shares can I buy", "what lot size", "size this trade for INR 1,000 risk", or wants the cost drag on an Indian trade.
---

# Position Sizer (NSE)

Fixed-fractional sizing: risk per share decides quantity, budget and position
caps trim it, and charges show the real breakeven.

```bash
python in-budget/size.py --entry 1335 --stop 1310
python in-budget/size.py --entry 1335 --stop 1310 --risk 750 --max-pct 15
python in-budget/size.py --entry 220.5 --stop 218 --atr 3.1 --json
```

## The formula

```
shares = floor(risk_cap / (entry - stop))
shares = min(shares, floor(budget * max_pct% / entry))
shares = min(shares, floor(free_cash / entry))
```

- `risk_cap` = `max_loss_per_trade`, capped by `max_daily_loss`.
- Free cash = budget - committed cost of open positions - `cash_buffer_pct`.
- `--atr` reports the stop distance in ATR units: under 0.5 is inside normal
  noise, over 2.0 is wide.
- `--flat-fee` overrides the brokerage cap (many Indian discount brokers charge
  INR 20 per executed order).

Charges used for the breakeven line: brokerage (cap INR 20 or 0.03% per leg),
STT 0.025% on sell, exchange 0.00297%, SEBI 0.0001%, stamp 0.003% on buy, GST
18% on brokerage+exchange+SEBI. Delivery and F&O have different rates - do not
use these numbers for them.

## What matters in India

- The INR 20 brokerage cap binds only above INR 66,666 per leg (0.03% x 66,666
  = 20). Below that, brokerage is 0.03% per leg, so round-trip charges are about
  0.10-0.11% of turnover whatever the size: a INR 500 order pays about INR 0.53.
- STT is on the sell side only for intraday; with the other charges the breakeven
  move is about 0.11% even on a clean, cost-efficient trade.
- Whole shares only; NSE cash equity has no lot size (that is F&O).
- `min_order` / `max_order` from config bound what is worth sending at all.

Present: shares, position value, risk in INR and % of budget, charges, breakeven
move, and which cap bound the size. Never present size without the stop it
assumes.
