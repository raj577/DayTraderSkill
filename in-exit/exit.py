#!/usr/bin/env python3
"""Position Exit Advisor for NSE intraday trades.

Checks every open position against its plan: stop, target, time, and the
15:12 IST MIS square-off. Reads prices from trader/prices.json (kept fresh by
news/alerts) or asks Yahoo for each symbol when the file is stale.

It never places an order; it tells you what the plan says to do.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common as C  # noqa: E402


def price_for(symbol, now, offline=None):
    if offline:
        data = C.read_json(offline, {}) or {}
        val = data.get(symbol) or data.get(C.bare_symbol(symbol)) or data.get(
            C.yahoo_symbol(symbol))
        if isinstance(val, dict):
            val = val.get("price") or val.get("last")
        return float(val) if val else None
    cached = C.load_prices(max_age_minutes=5, now=now)
    if not cached.get("stale") and symbol in cached["prices"]:
        return cached["prices"][symbol]
    try:
        return C.yahoo_quote(symbol)["price"]
    except C.TraderError:
        return None


def main(argv=None):
    p = argparse.ArgumentParser(prog="exit.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--now")
    p.add_argument("--offline", help="JSON of {symbol: price}")
    p.add_argument("--json", action="store_true")
    p.add_argument("--flat-by", help="HH:MM IST square-off time (default from config)")
    args = p.parse_args(argv)

    cfg = C.load_config()
    now = C.now_ist(args.now)
    state = C.market_state(now)
    flat_by = C.parse_hhmm(args.flat_by or cfg.get("flat_by"), C.dtime(15, 12))
    portfolio = C.load_portfolio(cfg)
    plan = C.read_json(C.trader_dir() / "plan.json", {}) or {}
    positions = portfolio.get("positions") or {}

    print("Position check, {} IST ({}).".format(
        now.strftime("%a %d %b %Y %H:%M"), state["status"]))

    if not positions:
        print("No open positions. Nothing to manage.")
        return 1

    rows = []
    total_pnl = total_value = total_cost = 0.0
    for symbol, pos in positions.items():
        shares = int(pos.get("shares") or 0)
        if shares <= 0:
            continue
        avg = float(pos.get("avg_cost") or 0)
        stop = pos.get("stop")
        target = pos.get("target")
        if plan.get("symbol") == symbol:
            stop = plan.get("stop", stop)
            target = plan.get("target", target)
        last = price_for(symbol, now, args.offline)
        if last is None:
            rows.append({"symbol": symbol, "shares": shares, "avg_cost": avg,
                         "price": None, "action": "NO PRICE", "why": "quote failed"})
            continue
        pnl = (last - avg) * shares
        value = last * shares
        total_pnl += pnl
        total_value += value
        total_cost += avg * shares

        actions = []
        if stop and last <= float(stop):
            actions.append(("EXIT NOW", "price {:.2f} is at or below the stop {:.2f}".format(
                last, float(stop))))
        if target and last >= float(target):
            actions.append(("TAKE OR TRAIL", "price {:.2f} is at or above the target {:.2f}".format(
                last, float(target))))
        if now.time() >= flat_by:
            actions.append(("EXIT NOW", "past flat_by {:%H:%M} for a {} position".format(
                flat_by, portfolio.get("product") or cfg.get("product", "MIS"))))

        risk = (avg - float(stop)) if stop else None
        r_mult = (last - avg) / risk if risk and risk > 0 else None
        if not actions:
            if r_mult is not None and r_mult >= 1.0:
                why = "up {:.2f}R; consider moving the stop to breakeven".format(r_mult)
            elif r_mult is not None and r_mult > 0:
                why = "up {:.2f}R; still inside the plan".format(r_mult)
            elif r_mult is not None:
                why = "down {:.2f}R; stop still ahead".format(r_mult)
            else:
                why = "no stop on the position; plan is incomplete"
            actions.append(("HOLD", why))

        action, why = actions[0]
        rows.append({
            "symbol": symbol, "shares": shares, "avg_cost": round(avg, 2),
            "price": round(last, 2), "pnl_inr": round(pnl, 2),
            "pnl_pct": round((last / avg - 1.0) * 100.0, 2) if avg else None,
            "stop": round(float(stop), 2) if stop else None,
            "target": round(float(target), 2) if target else None,
            "r_multiple": round(r_mult, 2) if r_mult is not None else None,
            "action": action, "why": why,
            "flat_by": "{:%H:%M}".format(flat_by),
        })

    for row in rows:
        if row.get("price") is None:
            print("  {:12s} {:>6d} shares  NO PRICE ({})".format(
                row["symbol"], row["shares"], row["why"]))
            continue
        extra = ""
        if row["r_multiple"] is not None:
            extra = " {:+.2f}R".format(row["r_multiple"])
        print("  {:12s} {:>6d} @ {:>9,.2f}  now {:>9,.2f}  {:>+9,.2f} ({}){}".format(
            row["symbol"], row["shares"], row["avg_cost"], row["price"],
            row["pnl_inr"], C.pct(row["pnl_pct"]), extra))
        print("               {}: {}".format(row["action"], row["why"]))
        if row["stop"] or row["target"]:
            print("               stop {}  target {}  flat by {}".format(
                "n/a" if row["stop"] is None else "{:,.2f}".format(row["stop"]),
                "n/a" if row["target"] is None else "{:,.2f}".format(row["target"]),
                row["flat_by"]))

    open_cost = total_cost
    cash = float(portfolio.get("cash") or 0)
    print("\nOpen: {} position(s), value INR {:,.2f}, cost INR {:,.2f}, "
          "unrealized {:+,.2f}. Cash {:,.2f}.".format(
              len(rows), total_value, open_cost, total_pnl, cash))
    if total_pnl > 0:
        print("Protect the gain: a trailing stop turns an open profit into a decided one.")
    else:
        print("Do not average down. A losing intraday trade is a decision, not a discount.")

    payload = {"as_of": now.isoformat(timespec="seconds"), "positions": rows,
               "unrealized_inr": round(total_pnl, 2),
               "value_inr": round(total_value, 2),
               "cash": cash}
    if args.json:
        print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
