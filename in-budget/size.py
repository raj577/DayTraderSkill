#!/usr/bin/env python3
"""Position Sizer for NSE intraday trades.

Fixed-fractional sizing: risk per share decides quantity, the budget caps it,
and the costs tell you where breakeven actually is.

    size.py --entry 1335 --stop 1310
    size.py --entry 1335 --stop 1310 --risk 750 --max-pct 15
    size.py --entry 220.5 --stop 218 --atr 3.1
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common as C  # noqa: E402


def size_for(entry, stop, budget, risk_cap, max_pct, cash):
    per_share = entry - stop
    if per_share <= 0:
        raise C.TraderError("stop must sit below the entry")
    by_risk = int(math.floor(risk_cap / per_share))
    by_pct = int(math.floor(budget * max_pct / 100.0 / entry))
    by_cash = int(math.floor(max(cash, 0.0) / entry))
    qty = min(by_risk, by_pct, by_cash)
    caps = []
    if qty == by_risk:
        caps.append("risk")
    if qty == by_pct:
        caps.append("max position %")
    if qty == by_cash:
        caps.append("cash")
    return max(qty, 0), caps


def main(argv=None):
    p = argparse.ArgumentParser(prog="size.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--entry", type=float, required=True)
    p.add_argument("--stop", type=float, required=True)
    p.add_argument("--target", type=float)
    p.add_argument("--atr", type=float, help="ATR14, for a volatility sanity check")
    p.add_argument("--budget", type=float)
    p.add_argument("--risk", type=float, help="risk cap in INR (default max_loss_per_trade)")
    p.add_argument("--max-pct", type=float)
    p.add_argument("--open-positions", type=int, default=0,
                   help="committed capital is read from trader/portfolio.json when present")
    p.add_argument("--flat-fee", type=float, default=None,
                   help="override the brokerage cap (some brokers charge INR 20 per leg)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    cfg = C.load_config()
    budget = float(args.budget if args.budget is not None else cfg["budget"])
    risk_cap = float(args.risk if args.risk is not None else cfg["max_loss_per_trade"])
    risk_cap = min(risk_cap, float(cfg["max_daily_loss"]))
    max_pct = float(args.max_pct if args.max_pct is not None else cfg["max_position_pct"])
    costs = C.costs_config(cfg)
    if args.flat_fee is not None:
        costs["brokerage_cap"] = float(args.flat_fee)

    pf = C.load_portfolio(cfg)
    committed = sum(pos["shares"] * pos["avg_cost"] for pos in pf["positions"].values())
    cash = min(float(pf.get("cash") or budget), budget - committed)
    cash -= budget * float(cfg.get("cash_buffer_pct", 0)) / 100.0
    if args.open_positions and not pf["positions"]:
        cash = min(cash, budget / max(args.open_positions + 1, 1))

    entry, stop = args.entry, args.stop
    if stop >= entry:
        print("Stop must be below the entry for a long trade.", file=sys.stderr)
        return 1
    dist = entry - stop
    dist_pct = dist / entry * 100.0

    qty, caps = size_for(entry, stop, budget, risk_cap, max_pct, cash)
    if qty <= 0:
        print("Zero shares fit. Entry INR {:,.2f}, risk per share INR {:,.2f}, "
              "risk cap INR {:,.2f}, cash INR {:,.2f}.".format(entry, dist, risk_cap, max(cash, 0)))
        return 1

    risk_inr = qty * dist
    position = qty * entry
    print("Size: {} shares at INR {:,.2f} = INR {:,.2f} position.".format(qty, entry, position))
    print("Risk: INR {:,.2f} ({:.2f}% of budget) if the stop at {:,.2f} is hit ({:.2f}% away)."
          .format(risk_inr, risk_inr / budget * 100.0, stop, dist_pct))
    if args.atr:
        frac = dist / args.atr
        note = "inside normal noise" if frac < 0.5 else ("wide" if frac > 2.0 else "reasonable")
        print("Stop is {:.2f} ATR14 from the entry ({}).".format(frac, note))
    cost = C.round_trip_costs(entry, args.target or entry + 2.0 * dist, qty, costs)
    be = cost["total"] / position * 100.0 if position else 0.0
    print("Charges: about INR {:,.2f} round trip; the price must move {:.2f}% before you break even."
          .format(cost["total"], be))
    print("Binding cap: {}.".format(", ".join(caps) if caps else "none"))
    if qty * (entry - stop) > risk_cap + 1e-6:
        print("Warning: the sizing math exceeded the risk cap; do not send this order.")
    print("Cash left after this order: INR {:,.2f}.".format(max(cash - position, 0)))

    payload = {"entry": entry, "stop": stop, "qty": qty, "risk_inr": round(risk_inr, 2),
               "position_inr": round(position, 2), "costs": cost, "breakeven_pct": round(be, 3),
               "caps": caps, "cash_after": round(cash - position, 2)}
    if args.json:
        print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
