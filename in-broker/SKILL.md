---
name: in-broker
description: Turn a passing NSE plan into an order ticket and keep a paper trading book. Use when the user says "make the order", "what do I place", "record my fill", or wants a printable intraday ticket with entry, stop leg, and target for an Indian broker.
---

# Broker Ticket (NSE, paper by default)

`ticket.py` never sends an order. It writes `trader/orders.json` and prints a
ticket you place by hand (or wire to your own bridge).

```bash
python in-broker/ticket.py
python in-broker/ticket.py --dry-run
python in-broker/ticket.py fill --price 1335.05
python in-broker/ticket.py close --price 1390
python in-broker/ticket.py list
```

## The ticket

- `BUY`, LIMIT, NSE, EQ, product `MIS`, validity `DAY`.
- Limit = plan entry + `--limit-slippage` (default 0.5%), rounded up to the 0.05
  tick.
- A **SL-M stop leg** at the plan stop - Indian brokers do not offer a native
  OCO/bracket for cash equity, so the two orders are placed together and the
  user owns the second one.
- The target is an **alert**, not an order.

Refusals: no plan, zero shares, past `flat_by`, or an `--qty` that breaks sizing
(the fill still applies floor and cap when recording).

## Paper book

`fill` records a buy into `trader/portfolio.json` (weighted average cost, stop,
target, opened_at) and charges half the round trip. `close` records the exit in
`trader/trades.json` with gross, charges, and net P&L, and updates cash.

## India notes

- MIS positions are squared off by the broker near 15:15-15:25; the ticket prints
  the 15:12 deadline.
- SL-M triggers a market order once the trigger is touched; in a gap it fills
  worse than the trigger.
- Some brokers need the stop as SL-L (limit); the trigger is the same, the fill is
  not guaranteed.
- Do not use these numbers for CNC/delivery or F&O.

Never claim an order was placed. After the user places it, record the fill so the
exit and risk tools have real numbers.
