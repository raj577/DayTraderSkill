#!/usr/bin/env python3
"""Time Machine: replay one NSE session on 5-minute bars.

Answers one question: if the opening-range break had been taken mechanically,
what would have happened? It walks the bars in order, marks the first break,
the failure, the best and worst points after entry, and where a 1:2 plan
would have ended.

A replay is evidence about prices, not a promise about tomorrow.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common as C  # noqa: E402


def load_bars(symbol, now, offline=None, interval=5):
    if offline:
        p = Path(offline)
        for name in ("{}.json".format(symbol), "{}.NS.json".format(symbol)):
            f = p / name
            if f.exists():
                return C.bars_from_chart(C.read_json(f), interval, now=now)
        return []
    payload = C.yahoo_chart(symbol, "1mo", "5m")
    return C.bars_from_chart(payload, interval, now=now)


def main(argv=None):
    p = argparse.ArgumentParser(prog="timemachine.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("symbol")
    p.add_argument("--date", help="YYYY-MM-DD (default: last trading day with bars)")
    p.add_argument("--or", dest="or_minutes", type=int, default=15)
    p.add_argument("--fail-window", type=int, default=15,
                   help="minutes after the break that a close back under the range counts as failure")
    p.add_argument("--target-r", type=float, default=2.0)
    p.add_argument("--budget", type=float)
    p.add_argument("--risk", type=float)
    p.add_argument("--offline", help="folder of saved Yahoo chart JSON")
    p.add_argument("--now")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    symbol = C.bare_symbol(args.symbol)
    cfg = C.load_config()
    now = C.now_ist(args.now)
    budget = float(args.budget if args.budget is not None else cfg["budget"])
    risk_cap = float(args.risk if args.risk is not None else cfg["max_loss_per_trade"])

    try:
        bars = load_bars(symbol, now, args.offline)
    except C.TraderError as exc:
        print("No bars for {}: {}".format(symbol, exc), file=sys.stderr)
        return 1
    if not bars:
        print("No 5-minute bars for {}. Yahoo serves roughly the last two months."
              .format(symbol))
        return 1

    wanted_day = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else bars[-1]["t"].date()
    bars = [b for b in bars if b["t"].date() == wanted_day]
    if not bars:
        print("Yahoo has no bars for {} on {}. It keeps about 60 days of 5-minute data."
              .format(symbol, wanted_day))
        return 1
    day = wanted_day

    orr = C.opening_range(bars, args.or_minutes, day=day)
    if not orr:
        print("The opening range is incomplete in the saved bars; nothing to replay.")
        return 1

    print("Time machine: {} on {}, 5-minute bars, opening range 09:15-{:02d}:{:02d} IST."
          .format(symbol, day.strftime("%a %d %b %Y"), orr["end"].hour, orr["end"].minute))
    print("OR high INR {:,.2f}, OR low INR {:,.2f}, range {:.2f}%.".format(
        orr["high"], orr["low"], (orr["high"] / orr["low"] - 1.0) * 100.0))

    entry_bar = C.first_close_above(bars, orr["high"], orr["end"])
    if not entry_bar:
        last = bars[-1]
        print("No completed bar closed above the OR high. The session closed at INR {:,.2f} "
              "({:+.2f}% from the OR high). The rule stood aside; that is a result too."
              .format(last["c"], (last["c"] / orr["high"] - 1.0) * 100.0))
        return 0

    entry = entry_bar["c"]
    stop = orr["low"]
    risk = entry - stop
    target = entry + args.target_r * risk
    qty = int(risk_cap // risk) if risk > 0 else 0
    qty = min(qty, int(budget * float(cfg["max_position_pct"]) / 100.0 // entry))

    after = [b for b in bars if b["t"] >= entry_bar["t"]]
    window_end = entry_bar["t"] + timedelta(minutes=args.fail_window)
    failed_at = next((b for b in after if b["t"] <= window_end and b["c"] < orr["high"]), None)

    hit_stop = next((b for b in after if b["l"] <= stop), None)
    hit_target = next((b for b in after if b["h"] >= target), None)
    mfe = max(after, key=lambda b: b["h"])
    mae = min(after, key=lambda b: b["l"])

    print("First close above the range: {:02d}:{:02d}-{:02d}:{:02d} at INR {:,.2f} "
          "({:+.2f}% over the OR high).".format(
              entry_bar["t"].hour, entry_bar["t"].minute,
              (entry_bar["t"] + timedelta(minutes=5)).hour,
              (entry_bar["t"] + timedelta(minutes=5)).minute,
              entry, (entry / orr["high"] - 1.0) * 100.0))
    if qty > 0:
        costs = C.round_trip_costs(entry, target, qty, C.costs_config(cfg))
        print("Mechanical size: {} shares (risk INR {:,.2f} at stop {:,.2f}, {}R target {:,.2f}). "
              "Round trip costs about INR {:,.2f}.".format(qty, qty * risk, stop,
                                                           args.target_r, target, costs["total"]))
    else:
        print("Mechanical size is zero shares: one share risks more than the cap.")

    if hit_target and (not hit_stop or hit_target["t"] <= hit_stop["t"]):
        print("Target hit at {:02d}:{:02d} (INR {:,.2f}). Plan result: {:+.2f}R before costs."
              .format(hit_target["t"].hour, hit_target["t"].minute, hit_target["h"],
                      args.target_r))
    elif hit_stop:
        print("Stop hit at {:02d}:{:02d} (low INR {:,.2f}). Plan result: -1.00R before costs."
              .format(hit_stop["t"].hour, hit_stop["t"].minute, hit_stop["l"]))
    else:
        close_bar = bars[-1]
        print("Neither stop nor target. Close at INR {:,.2f}, {:+.2f}R before costs.".format(
            close_bar["c"], (close_bar["c"] - entry) / risk))

    print("After the entry: best INR {:,.2f} at {:02d}:{:02d}, worst INR {:,.2f} at {:02d}:{:02d}."
          .format(mfe["h"], mfe["t"].hour, mfe["t"].minute,
                  mae["l"], mae["t"].hour, mae["t"].minute))
    if failed_at:
        print("The break failed within {} minutes: close {:02d}:{:02d} back at INR {:,.2f}, "
              "under the OR high. A rule that waits for a second close would have been out early."
              .format(args.fail_window, failed_at["t"].hour, failed_at["t"].minute, failed_at["c"]))
    else:
        print("The break never closed back under the OR high in the first {} minutes; it held."
              .format(args.fail_window))

    print("A replay shows one path prices took. It cannot show the ones they did not.")

    payload = {
        "symbol": symbol, "date": day.isoformat(), "interval": 5,
        "or_minutes": args.or_minutes, "or_high": round(orr["high"], 2),
        "or_low": round(orr["low"], 2), "entry": round(entry, 2),
        "entry_time": entry_bar["t"].strftime("%H:%M"), "stop": round(stop, 2),
        "target": round(target, 2), "qty": qty,
        "failed_at": failed_at["t"].strftime("%H:%M") if failed_at else None,
        "hit_stop": hit_stop["t"].strftime("%H:%M") if hit_stop else None,
        "hit_target": hit_target["t"].strftime("%H:%M") if hit_target else None,
        "mfe": round(mfe["h"], 2), "mae": round(mae["l"], 2),
        "bars": len(bars),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
