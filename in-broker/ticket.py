#!/usr/bin/env python3
"""Broker Ticket Maker for NSE cash equities.

    ticket.py                 # turn trader/plan.json into an order ticket
    ticket.py fill --price 1335
    ticket.py close --price 1390
    ticket.py list

No broker API, no network, no order is ever sent by this tool. It writes
trader/orders.json for you to place by hand (or wire to your own bridge), and
keeps the paper book in trader/portfolio.json and trader/trades.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common as C  # noqa: E402

TICK = 0.05


def tick_round(value, up=True):
    ticks = value / TICK
    import math
    return round((math.ceil(ticks) if up else math.floor(ticks)) * TICK, 2)


def load_orders():
    return C.read_json(C.trader_dir() / "orders.json", {}) or {"orders": []}


def save_orders(data):
    return C.write_json(C.trader_dir() / "orders.json", data)


def make_ticket(args):
    cfg = C.load_config()
    now = C.now_ist(args.now)
    state = C.market_state(now)
    plan = C.read_json(args.plan or (C.trader_dir() / "plan.json"), None)
    if not plan:
        print("No plan. Run in-entry/entry.py first.", file=sys.stderr)
        return 1
    entry = float(plan["entry"])
    stop = float(plan["stop"])
    target = plan.get("target")
    qty = int(args.qty or plan["shares"])
    if qty <= 0:
        print("The plan has zero shares.", file=sys.stderr)
        return 1
    if qty != int(plan["shares"]):
        print("Note: {} shares instead of the planned {}.".format(qty, plan["shares"]))

    product = (args.product or cfg.get("product") or "MIS").upper()
    flat_by = C.parse_hhmm(cfg.get("flat_by"), C.dtime(15, 12))
    limit = tick_round(entry * (1.0 + args.limit_slippage / 100.0))
    position = limit * qty
    costs = C.round_trip_costs(entry, float(target or entry), qty, C.costs_config(cfg))

    warnings = []
    cap_pct = float(cfg.get("max_position_pct") or 0)
    if cap_pct:
        cap = float(cfg["budget"]) * cap_pct / 100.0
        if position > cap + 0.01:
            warnings.append(
                "Limit price makes this INR {:,.2f}, over the {:.0f}% position cap "
                "(INR {:,.2f}). Lower the limit or the quantity.".format(
                    position, cap_pct, cap))
    if plan.get("session") and plan["session"] != now.date().isoformat():
        warnings.append("Plan was written on {}; re-check the chart before using it today."
                        .format(plan["session"]))
    if state["status"] == "closed":
        warnings.append("Market is closed; this ticket is for the next session.")
    if now.time() >= flat_by:
        warnings.append("Past flat_by {:%H:%M}: a {} position would be squared off almost at once."
                        .format(flat_by, product))
        if not args.dry_run:
            print("Refusing: past flat_by. Nothing recorded.", file=sys.stderr)
            return 1

    ticket = {
        "id": "T" + uuid.uuid4().hex[:8].upper(),
        "created_at": now.isoformat(timespec="seconds"),
        "symbol": C.bare_symbol(plan["symbol"]),
        "exchange": "NSE",
        "segment": "EQ",
        "transaction": "BUY",
        "quantity": qty,
        "order_type": "LIMIT",
        "product": product,
        "validity": "DAY",
        "limit_price": limit,
        "trigger_price": round(stop, 2),
        "stop_order_type": "SL-M",
        "target_price": round(float(target), 2) if target else None,
        "flat_by": "{:%H:%M}".format(flat_by),
        "position_value": round(position, 2),
        "estimated_costs": costs,
        "plan": plan,
        "status": "pending_manual",
        "note": "Place the BUY limit and the SL-M stop leg together; the target is an alert, not an order.",
        "warnings": warnings,
    }

    print("Order ticket {}  ({} IST)".format(ticket["id"], now.strftime("%H:%M")))
    print("  BUY {:12s}  {} shares  LIMIT {:,.2f}  {}  {}  DAY".format(
        ticket["symbol"], qty, limit, product, "NSE"))
    print("  Stop leg: SELL {} shares  SL-M  trigger {:,.2f}".format(qty, stop))
    if target:
        print("  Target alert: {:,.2f} (do not send as a market order)".format(float(target)))
    print("  Value about INR {:,.2f}; charges about INR {:,.2f} round trip.".format(
        position, costs["total"]))
    print("  Square-off deadline: {:%H:%M} IST.".format(flat_by))
    for w in warnings:
        print("  warning: " + w)
    print("  This tool sends nothing. Place the order yourself and record the fill with:")
    print("    ticket.py fill --price {:.2f}".format(limit))

    if args.dry_run:
        print("Dry run: nothing written.")
        return 0

    data = load_orders()
    data.setdefault("orders", []).append(ticket)
    out = save_orders(data)
    print("Wrote {}".format(out))
    if args.json:
        print(json.dumps(ticket, indent=2))
    return 0


def cmd_fill(args):
    cfg = C.load_config()
    now = C.now_ist(args.now)
    data = load_orders()
    orders = data.get("orders") or []
    order = None
    for o in reversed(orders):
        if o.get("status") == "pending_manual":
            if args.symbol and C.bare_symbol(o["symbol"]) != C.bare_symbol(args.symbol):
                continue
            order = o
            break
    if not order:
        print("No pending order to fill. Run `ticket.py` first.", file=sys.stderr)
        return 1
    qty = int(args.qty or order["quantity"])
    price = float(args.price)

    pf = C.load_portfolio(cfg)
    pos = pf["positions"].get(order["symbol"]) or {"shares": 0, "avg_cost": 0.0}
    total_shares = pos["shares"] + qty
    pos["avg_cost"] = round((pos["avg_cost"] * pos["shares"] + price * qty) / total_shares, 4)
    pos["shares"] = total_shares
    pos["stop"] = order.get("trigger_price")
    pos["target"] = order.get("target_price")
    pos["product"] = order.get("product")
    pos["opened_at"] = now.isoformat(timespec="seconds")
    pf["positions"][order["symbol"]] = pos
    buy_cost = C.round_trip_costs(price, price, qty, C.costs_config(cfg))["total"] / 2.0
    pf["cash"] = round(float(pf.get("cash") or cfg["budget"]) - price * qty - buy_cost, 2)
    C.write_json(C.trader_dir() / "portfolio.json", pf)

    order["status"] = "filled"
    order["filled_at"] = now.isoformat(timespec="seconds")
    order["fill_price"] = price
    order["filled_qty"] = qty
    save_orders(data)

    print("Recorded fill: {} {} shares at INR {:,.2f}. Position {} shares, average {:,.2f}."
          .format(order["symbol"], qty, price, total_shares, pos["avg_cost"]))
    print("Stop {:,.2f}, target {}. Cash after: INR {:,.2f}.".format(
        float(pos["stop"] or 0),
        "{:,.2f}".format(float(pos["target"])) if pos.get("target") else "n/a",
        pf["cash"]))
    return 0


def cmd_close(args):
    cfg = C.load_config()
    now = C.now_ist(args.now)
    pf = C.load_portfolio(cfg)
    symbol = C.bare_symbol(args.symbol) if args.symbol else None
    targets = [symbol] if symbol else list(pf["positions"])
    if not targets:
        print("No open positions.", file=sys.stderr)
        return 1
    trades = C.read_json(C.trader_dir() / "trades.json", {}) or {"trades": []}
    total = 0.0
    for sym in targets:
        pos = pf["positions"].get(sym)
        if not pos:
            continue
        qty = int(args.qty or pos["shares"])
        qty = min(qty, pos["shares"])
        price = float(args.price)
        costs = C.round_trip_costs(pos["avg_cost"], price, qty, C.costs_config(cfg))
        gross = (price - pos["avg_cost"]) * qty
        pnl = gross - costs["total"]
        trade = {
            "date": now.date().isoformat(), "closed_at": now.isoformat(timespec="seconds"),
            "symbol": sym, "qty": qty, "entry": pos["avg_cost"], "exit": price,
            "gross": round(gross, 2), "costs": costs["total"], "pnl": round(pnl, 2),
            "product": pos.get("product") or cfg.get("product"),
        }
        trades.setdefault("trades", []).append(trade)
        total += pnl
        pf["cash"] = round(float(pf.get("cash") or 0) + price * qty - costs["total"] / 2.0, 2)
        pos["shares"] -= qty
        if pos["shares"] <= 0:
            del pf["positions"][sym]
        print("Closed {} {} shares at INR {:,.2f}: gross {:+,.2f}, charges {:,.2f}, net {:+,.2f}."
              .format(sym, qty, price, gross, costs["total"], pnl))
    C.write_json(C.trader_dir() / "portfolio.json", pf)
    C.write_json(C.trader_dir() / "trades.json", trades)
    print("Day total so far: {:+,.2f}. Cash {:,.2f}.".format(total, pf["cash"]))
    return 0


def cmd_list(args):
    data = load_orders()
    orders = data.get("orders") or []
    if not orders:
        print("No recorded orders.")
        return 0
    print("{} order(s):".format(len(orders)))
    for o in orders:
        print("  {}  {}  {:12s} {:>6d} @ {:>9,.2f}  {}".format(
            o.get("created_at", "")[:16].replace("T", " "), o.get("id"),
            o.get("symbol"), o.get("quantity", 0),
            float(o.get("limit_price") or 0), o.get("status")))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="ticket.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--plan")
    p.add_argument("--qty", type=int)
    p.add_argument("--limit-slippage", type=float, default=0.5,
                   help="percent above the plan entry for the limit (default 0.5)")
    p.add_argument("--product", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--now")
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    f = sub.add_parser("fill", help="record a paper fill")
    f.add_argument("--price", type=float, required=True)
    f.add_argument("--qty", type=int)
    f.add_argument("--symbol")
    f.add_argument("--now", default=argparse.SUPPRESS)
    f.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    c = sub.add_parser("close", help="record a paper close")
    c.add_argument("--price", type=float, required=True)
    c.add_argument("--qty", type=int)
    c.add_argument("--symbol")
    c.add_argument("--now", default=argparse.SUPPRESS)
    c.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    lst = sub.add_parser("list", help="list recorded orders")
    lst.add_argument("--now", default=argparse.SUPPRESS)
    lst.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    args = p.parse_args(argv)

    if args.cmd == "fill":
        return cmd_fill(args)
    if args.cmd == "close":
        return cmd_close(args)
    if args.cmd == "list":
        return cmd_list(args)
    return make_ticket(args)


if __name__ == "__main__":
    sys.exit(main())
