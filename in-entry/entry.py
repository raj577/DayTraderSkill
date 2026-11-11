#!/usr/bin/env python3
"""Entry Planner for NSE cash equities.

Turns a chart idea into a checked, sized plan and writes trader/plan.json.
It never places an order; in-broker/ticket.py turns the plan into a ticket.

A plan is refused when it breaks the owner's rules: too much risk, a stop the
structure does not support, a reward smaller than the risk, or an order the
budget cannot carry.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common as C  # noqa: E402


def daily_bars(symbol, now, offline=None):
    if offline:
        p = Path(offline)
        for name in ("{}.json".format(symbol), "{}.NS.json".format(symbol)):
            f = p / name
            if f.exists():
                return C.bars_from_chart(C.read_json(f), 1440, now=now)
        return []
    payload = C.yahoo_chart(symbol, "3mo", "1d")
    return C.bars_from_chart(payload, 1440, now=now)


def main(argv=None):
    p = argparse.ArgumentParser(prog="entry.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("symbol", nargs="?", help="NSE symbol, e.g. RELIANCE or RELIANCE.NS")
    p.add_argument("--symbol", dest="symbol_opt", help="same, as a flag")
    p.add_argument("--entry", help='limit price, or "auto" for the live last price')
    p.add_argument("--stop", help='stop price, or "atr" or "structure"')
    p.add_argument("--target", type=float, help="target price (default: 2x risk)")
    p.add_argument("--qty", type=int, help="shares override (re-portioned, floor and cap still apply)")
    p.add_argument("--budget", type=float)
    p.add_argument("--max-loss", type=float, help="risk cap in INR for this trade")
    p.add_argument("--atr-mult", type=float, default=1.5)
    p.add_argument("--min-rr", type=float)
    p.add_argument("--max-pct", type=float, help="max position as percent of budget")
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--allow-untested", action="store_true",
                   help="accept a stop that sits above real structure")
    p.add_argument("--allow-no-stop", action="store_true")
    p.add_argument("--allow-outside-hours", action="store_true")
    p.add_argument("--force", action="store_true", help="replace an existing plan")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--offline", help="folder of saved Yahoo daily chart JSON")
    p.add_argument("--now")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    raw = args.symbol_opt or args.symbol
    if not raw:
        print("Give a symbol: entry.py RELIANCE --entry 1335 --stop 1310 --target 1390", file=sys.stderr)
        return 2
    symbol = C.bare_symbol(raw)
    cfg = C.load_config()
    now = C.now_ist(args.now)
    state = C.market_state(now)
    warnings, blockers = [], []

    blocked = {C.bare_symbol(b) for b in (cfg.get("blocked") or [])}
    if symbol in blocked:
        blockers.append("{} is on the owner's blocked list.".format(symbol))

    try:
        ban = {C.bare_symbol(s) for s in C.fno_ban()}
        if symbol in ban:
            blockers.append("{} is in the F&O ban list today (only if the ban applies to cash).".format(symbol))
    except C.TraderError:
        warnings.append("Could not check the F&O ban list.")

    existing = C.read_json(C.trader_dir() / "plan.json", None)
    if existing and not args.force:
        print("A plan already exists for {} ({}). Pass --force to replace it.".format(
            existing.get("symbol"), existing.get("created_at")), file=sys.stderr)
        return 1

    budget = float(args.budget if args.budget is not None else cfg["budget"])
    risk_cap = float(args.max_loss if args.max_loss is not None else cfg["max_loss_per_trade"])
    risk_cap = min(risk_cap, float(cfg["max_daily_loss"]))
    no_before = C.parse_hhmm(cfg.get("no_entries_before"), C.dtime(9, 20))
    no_after = C.parse_hhmm(cfg.get("no_entries_after"), C.dtime(14, 45))
    flat_by = C.parse_hhmm(cfg.get("flat_by"), C.dtime(15, 12))

    if state["status"] == "closed":
        warnings.append("Market is closed ({}). This is a plan for the next session.".format(state["reason"]))
    elif state["status"] == "pre_open":
        warnings.append("NSE is still in pre-open; the entry will not fill until 09:15.")
    else:
        if now.time() < no_before:
            warnings.append("Before no_entries_before ({:%H:%M}); the open is still finding its level."
                            .format(no_before))
        if now.time() > no_after:
            warnings.append("After no_entries_after ({:%H:%M}); a fresh entry is late in the day."
                            .format(no_after))
    if warnings and not args.allow_outside_hours and any("late" in w or "Before" in w for w in warnings):
        blockers.append("Outside the entry window; pass --allow-outside-hours if you mean it anyway.")

    bars = []
    try:
        bars = daily_bars(symbol, now, args.offline)
    except C.TraderError as exc:
        warnings.append("No daily structure: {}".format(exc))
    if len(bars) < 25:
        warnings.append("Only {} daily bars; structure and ATR are thin.".format(len(bars)))

    entry = None
    if args.entry and args.entry.lower() == "auto":
        try:
            q = C.yahoo_quote(symbol)
            entry = q["price"]
            print("Live last price: INR {:,.2f} (Yahoo, unofficial)".format(entry))
        except C.TraderError as exc:
            blockers.append("Cannot price the entry: {}".format(exc))
    elif args.entry:
        try:
            entry = float(args.entry)
        except ValueError:
            blockers.append("--entry must be a number or 'auto'.")
    else:
        blockers.append("Give --entry LIMIT (or --entry auto). A market entry is a choice, not a default.")

    atr = C.atr(bars, 14) if len(bars) >= 15 else None
    stop = None
    if args.stop is not None:
        word = str(args.stop).strip().lower()
        if word == "atr":
            if entry and atr:
                stop = round(entry - args.atr_mult * atr, 2)
                print("Stop from {:.1f}x ATR14: INR {:,.2f}.".format(args.atr_mult, stop))
            else:
                blockers.append("--stop atr needs daily bars for an ATR.")
        elif word in ("structure", "swing"):
            if bars:
                stop = round(min(b["l"] for b in bars[-5:]) * 0.999, 2)
                print("Stop from 5-session swing low: INR {:,.2f}.".format(stop))
            else:
                blockers.append("--stop structure needs daily bars.")
        else:
            try:
                stop = float(args.stop)
            except ValueError:
                blockers.append("--stop must be a number, 'atr', or 'structure'.")
    if stop is None and entry and atr:
        stop = round(entry - args.atr_mult * atr, 2)
        warnings.append("No stop given; using {:.1f}x ATR14 below the entry (INR {:,.2f})."
                        .format(args.atr_mult, stop))
    if stop is None:
        if not args.allow_no_stop:
            blockers.append("No stop. Pass --stop (or --allow-no-stop to override).")
        else:
            warnings.append("No stop: a position with no exit price can lose the whole account.")

    if entry and stop and stop >= entry:
        blockers.append("Stop ({:,.2f}) must sit below the entry ({:,.2f}); this planner is long-only."
                        .format(stop, entry))

    structural = None
    swing_low = None
    orr = None
    if bars and entry:
        swing_low = min(b["l"] for b in bars[-5:])
        orr = None
        try:
            if not args.offline:
                intra = C.yahoo_chart(symbol, "1d", "5m")
                ib = C.bars_from_chart(intra, 5, now=now)
                o = C.opening_range(ib, args.or_minutes, day=now.date())
                orr = o["low"] if o else None
        except C.TraderError:
            pass
        floors = [swing_low] + ([orr] if orr else [])
        structural = max(floors)
        if stop and stop > structural * 1.0005:
            msg = ("Stop {:,.2f} is above real structure (5-session swing low {:,.2f}{}). "
                   "It would be hit before support is even tested.").format(
                       stop, swing_low, "; today's OR low {:,.2f}".format(orr) if orr else "")
            if args.allow_untested:
                warnings.append(msg + " Overridden with --allow-untested.")
            else:
                blockers.append(msg + " Pass --allow-untested to accept it.")

    if entry and stop:
        risk_per_share = entry - stop
        dist_pct = risk_per_share / entry * 100.0
        if dist_pct < 0.3:
            warnings.append("Stop is only {:.2f}% away; normal noise can take it out.".format(dist_pct))
        if dist_pct > 8.0:
            warnings.append("Stop is {:.1f}% away; either size down or find a tighter test.".format(dist_pct))
        if atr and risk_per_share < 0.5 * atr:
            warnings.append("Stop is inside half an ATR ({:,.2f}); expect a coin-flip stop-out.".format(atr))

    target = args.target
    if target is None and entry and stop:
        target = round(entry + 2.0 * (entry - stop), 2)
        print("No target given; using 2x risk: INR {:,.2f}.".format(target))
    if entry and stop and target:
        if target <= entry:
            blockers.append("Target ({:,.2f}) must sit above the entry ({:,.2f}).".format(target, entry))
        rr = (target - entry) / (entry - stop)
        min_rr = args.min_rr if args.min_rr is not None else cfg["min_reward_risk"]
        if rr < min_rr:
            blockers.append("Reward:risk is {:.2f}; the minimum is {:.2f}.".format(rr, min_rr))
    else:
        rr = None

    if entry and stop and not blockers:
        per_share = entry - stop
        qty = int(math.floor(risk_cap / per_share))
        max_pct = float(args.max_pct if args.max_pct is not None else cfg["max_position_pct"])
        qty = min(qty, int(math.floor(budget * max_pct / 100.0 / entry)))
        pf = C.load_portfolio(cfg)
        committed = sum(pos["shares"] * pos["avg_cost"] for pos in pf["positions"].values())
        buffer_amt = budget * float(cfg.get("cash_buffer_pct", 0)) / 100.0
        cash = budget - committed - buffer_amt
        qty = min(qty, int(math.floor(cash / entry)))
        if qty <= 0:
            blockers.append("Budget cannot carry one share (INR {:,.2f} entry, INR {:,.2f} free)."
                            .format(entry, max(cash, 0)))
        if args.qty:
            if args.qty > qty:
                warnings.append("Requested {} shares, capped at {} by the risk/budget rules.".format(args.qty, qty))
            qty = min(args.qty, qty)
        if qty <= 0:
            blockers.append("Zero shares fit; nothing to plan.")
    else:
        qty = 0

    costs = None
    if qty > 0 and entry and target:
        costs = C.round_trip_costs(entry, target, qty, C.costs_config(cfg))
        costs["breakeven_pct"] = round(costs["total"] / (qty * entry) * 100.0, 3)
        if costs["total"] > risk_cap:
            blockers.append("Round-trip costs (INR {:,.2f}) exceed the risk cap (INR {:,.2f})."
                            .format(costs["total"], risk_cap))

    if blockers:
        print("Plan refused for {}.".format(symbol))
        for b in blockers:
            print("  - " + b)
        for w in warnings:
            print("  note: " + w)
        return 1

    risk_inr = round(qty * (entry - stop), 2)
    reward_inr = round(qty * (target - entry), 2)
    plan = {
        "symbol": symbol,
        "created_at": now.isoformat(timespec="seconds"),
        "session": now.date().isoformat(),
        "side": "long",
        "product": cfg.get("product", "MIS"),
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "target": round(target, 2) if target else None,
        "shares": qty,
        "risk_inr": risk_inr,
        "risk_pct_of_budget": round(risk_inr / budget * 100.0, 2),
        "reward_inr": reward_inr,
        "reward_risk": round(rr, 2) if rr else None,
        "position_inr": round(qty * entry, 2),
        "budget": budget,
        "atr14": round(atr, 2) if atr else None,
        "structural_stop": round(structural, 2) if structural else None,
        "costs": costs,
        "flat_by": "{:%H:%M}".format(flat_by),
        "checks": {
            "market_state": state["status"],
            "outside_window": bool(warnings),
            "stop_below_structure": bool(structural and stop <= structural * 1.0005),
            "fno_ban_checked": True,
        },
        "warnings": warnings,
    }

    print("Plan for {}: {} shares at INR {:,.2f}, stop INR {:,.2f}, target INR {:,.2f}."
          .format(symbol, qty, entry, stop, target))
    print("Risk INR {:,.2f} ({:.2f}% of budget), reward INR {:,.2f}, R:R {:.2f}.".format(
        risk_inr, plan["risk_pct_of_budget"], reward_inr, rr))
    if costs:
        print("Costs INR {:,.2f} round trip (STT, exchange, SEBI, stamp, GST, brokerage cap INR {:,.0f})."
              .format(costs["total"], cfg["costs"].get("brokerage_cap", 20)))
        print("Breakeven move: {:.2f}%.".format(costs["breakeven_pct"]))
    print("Structure: 5-session swing low {}; OR low {}; ATR14 {}.".format(
        "{:,.2f}".format(swing_low) if swing_low else "n/a",
        "{:,.2f}".format(orr) if orr else "n/a",
        "INR {:,.2f}".format(atr) if atr else "n/a"))
    print("MIS position must be flat by {} IST or the broker will do it for you.".format(
        plan["flat_by"]))
    for w in warnings:
        print("  note: " + w)
    print("Gap risk: a stop is not a guarantee; overnight and gap-downs can fill worse.")
    print("This is a plan, not an order. Confirm the chart before you send it.")

    if not args.no_write:
        out = C.write_json(C.trader_dir() / "plan.json", plan)
        print("Wrote {}".format(out))
    if args.json:
        print(json.dumps(plan, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
