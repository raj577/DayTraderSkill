#!/usr/bin/env python3
"""Pre-trade Risk Gate for NSE intraday trades.

Reads trader/plan.json (or --plan FILE) and checks it against the owner's rules
before anything is sent to a broker: budget, daily loss, trade count, time
window, size, reward:risk, and the 15:12 square-off.

Exit 0 means every check passed. Exit 1 means at least one failed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common as C  # noqa: E402


def today_trades(now):
    data = C.read_json(C.trader_dir() / "trades.json", {}) or {}
    rows = data.get("trades") or []
    today = now.date().isoformat()
    return [r for r in rows if str(r.get("date"))[:10] == today]


def pending_orders(now):
    data = C.read_json(C.trader_dir() / "orders.json", {}) or {}
    rows = data.get("orders") or []
    today = now.date().isoformat()
    return [r for r in rows if str(r.get("created_at", ""))[:10] == today]


def main(argv=None):
    p = argparse.ArgumentParser(prog="check.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--plan", help="plan JSON (default trader/plan.json)")
    p.add_argument("--now")
    p.add_argument("--allow-outside-hours", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    cfg = C.load_config()
    now = C.now_ist(args.now)
    state = C.market_state(now)
    plan_path = Path(args.plan) if args.plan else C.trader_dir() / "plan.json"
    plan = C.read_json(plan_path, None)
    pf = C.load_portfolio(cfg)
    results = []

    def check(name, ok, detail, blocking=True):
        results.append({"check": name, "ok": bool(ok), "detail": detail,
                        "blocking": blocking})

    check("market_open", state["status"] != "closed",
          "market is {}".format(state["status"]), blocking=not args.allow_outside_hours)
    if state["status"] == "pre_open":
        check("entry_window", True, "pre-open; plan can be prepared, not filled", blocking=False)

    if not plan:
        check("plan_exists", False, "no plan at {}. Run in-entry first.".format(plan_path))
        plan = {}
    else:
        sym = C.bare_symbol(plan.get("symbol"))
        entry = plan.get("entry")
        stop = plan.get("stop")
        qty = int(plan.get("shares") or 0)
        target = plan.get("target")
        check("plan_identity", bool(sym and entry and stop and qty > 0),
              "{} {} shares entry {} stop {}".format(sym, qty, entry, stop))
        if entry and stop and qty:
            risk = (float(entry) - float(stop)) * qty
            check("risk_per_trade", risk <= float(cfg["max_loss_per_trade"]) + 0.01,
                  "INR {:,.2f} vs cap INR {:,.2f}".format(risk, cfg["max_loss_per_trade"]))
            position = float(entry) * qty
            max_pct = float(cfg["max_position_pct"])
            check("position_pct", position <= float(cfg["budget"]) * max_pct / 100.0 + 0.01,
                  "INR {:,.2f} vs {:.0f}% of INR {:,.2f}".format(
                      position, max_pct, cfg["budget"]))
            check("order_bounds",
                  float(cfg["min_order"]) <= position <= float(cfg["max_order"]) + 0.01,
                  "order INR {:,.2f}, window INR {:,.0f}-{:,.0f}".format(
                      position, cfg["min_order"], cfg["max_order"]))
            if target:
                rr = (float(target) - float(entry)) / (float(entry) - float(stop))
                check("reward_risk", rr >= float(cfg["min_reward_risk"]) - 1e-9,
                      "{:.2f} vs minimum {:.2f}".format(rr, cfg["min_reward_risk"]))
            else:
                check("target_set", False, "no target in the plan")
            blocked = {C.bare_symbol(b) for b in (cfg.get("blocked") or [])}
            check("blocked_list", sym not in blocked, "symbol {}".format(sym))
            try:
                ban = {C.bare_symbol(s) for s in C.fno_ban()}
            except C.TraderError:
                ban = set()
            check("fno_ban", sym not in ban, "symbol {}".format(sym), blocking=False)
        else:
            check("risk_per_trade", False, "plan is incomplete")
            check("position_pct", False, "plan is incomplete")
            check("order_bounds", False, "plan is incomplete")
            check("reward_risk", False, "plan is incomplete")

        committed = sum(v["shares"] * v["avg_cost"] for v in pf["positions"].values())
        cash = float(pf.get("cash") or cfg["budget"]) - committed
        need = float(entry) * qty if entry and qty else 0.0
        check("cash_available", need <= cash + 0.01,
              "need INR {:,.2f}, free INR {:,.2f}".format(need, max(cash, 0)))

    trades = today_trades(now)
    realized = sum(float(t.get("pnl") or 0) for t in trades)
    closed = [t for t in trades if t.get("exit") is not None]
    check("daily_trades", len(closed) < int(cfg["max_trades_per_day"]),
          "{} closed today, limit {}".format(len(closed), cfg["max_trades_per_day"]))
    check("daily_loss", realized > -float(cfg["max_daily_loss"]),
          "realized {:+,.2f}, limit {:,.2f}".format(realized, -cfg["max_daily_loss"]))
    orders = pending_orders(now)
    check("daily_orders", len(orders) < int(cfg["max_orders_per_day"]),
          "{} orders today, limit {}".format(len(orders), cfg["max_orders_per_day"]))

    if state["status"] == "open":
        no_before = C.parse_hhmm(cfg.get("no_entries_before"), C.dtime(9, 20))
        no_after = C.parse_hhmm(cfg.get("no_entries_after"), C.dtime(14, 45))
        flat_by = C.parse_hhmm(cfg.get("flat_by"), C.dtime(15, 12))
        in_window = no_before <= now.time() <= no_after
        check("entry_window", in_window or args.allow_outside_hours,
              "now {:02d}:{:02d}, window {:02d}:{:02d}-{:02d}:{:02d}".format(
                  now.hour, now.minute, no_before.hour, no_before.minute,
                  no_after.hour, no_after.minute))
        check("before_flat_by", now.time() < flat_by,
              "MIS square-off at {:%H:%M}; now {:%H:%M}".format(flat_by, now.time()))

    open_risk = 0.0
    for sym, pos in pf["positions"].items():
        stop = pos.get("stop")
        if stop is None and plan.get("symbol") == sym:
            stop = plan.get("stop")
        if stop:
            open_risk += max(float(pos["avg_cost"]) - float(stop), 0.0) * int(pos["shares"])
    check("open_risk", open_risk <= float(cfg["max_daily_loss"]),
          "INR {:,.2f} open risk vs INR {:,.2f}".format(open_risk, cfg["max_daily_loss"]))

    failed = [r for r in results if r["blocking"] and not r["ok"]]
    print("Risk check for {} at {} IST:".format(
        C.bare_symbol(plan.get("symbol")) if plan.get("symbol") else "the plan",
        now.strftime("%H:%M")))
    for r in results:
        tag = "PASS" if r["ok"] else ("FAIL" if r["blocking"] else "WARN")
        print("  [{}] {:18s} {}".format(tag, r["check"], r["detail"]))
    if failed:
        print("Verdict: DO NOT TRADE. {} rule(s) failed.".format(len(failed)))
        for r in failed:
            print("  - {}: {}".format(r["check"], r["detail"]))
    else:
        print("Verdict: PASS. Rules clear; this is permission to trade, not a reason to.")
    print("A stop is not a guarantee: gaps and circuits can fill far worse than the plan.")

    if args.json:
        print(json.dumps({"as_of": now.isoformat(timespec="seconds"), "results": results,
                          "verdict": "pass" if not failed else "fail"}, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
