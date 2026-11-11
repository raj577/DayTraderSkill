#!/usr/bin/env python3
"""Alert Levels for NSE symbols.

Prints the price levels worth a phone alert: the opening-range high and low,
the previous close, the 20-day high and 20-day average, and the nearest round
number. Read them onto your broker app's alert screen; nothing is pushed here.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common as C  # noqa: E402


def round_levels(price):
    step = 5.0 if price < 500 else (10.0 if price < 2000 else (25.0 if price < 5000 else 50.0))
    below = math.floor(price / step) * step
    above = below + step
    return below, above


def levels_for(symbol, now, or_minutes, offline=None):
    if offline:
        p = Path(offline)
        intra = daily = None
        for name in ("{}_5m.json".format(symbol), "{}.json".format(symbol)):
            f = p / name
            if f.exists() and intra is None:
                intra = C.read_json(f)
        for name in ("{}_1d.json".format(symbol),):
            f = p / name
            if f.exists():
                daily = C.read_json(f)
        if intra is None:
            return None
    else:
        intra = C.yahoo_chart(symbol, "1d", "5m")
        daily = C.yahoo_chart(symbol, "1mo", "1d")

    bars = C.bars_from_chart(intra, 5, now=now, session_day=now.date())
    if not bars:
        return None
    orr = C.opening_range(bars, or_minutes, day=now.date())
    day_high = max(b["h"] for b in bars)
    day_low = min(b["l"] for b in bars)
    last = bars[-1]["c"]

    meta = (intra.get("chart", {}).get("result") or [{}])[0].get("meta") or {}
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    day_bars = C.bars_from_chart(daily, 1440, now=now) if daily else []
    closes = [b["c"] for b in day_bars]
    ma20 = C.sma(closes, 20) if len(closes) >= 20 else None
    high20 = max((b["h"] for b in day_bars[-20:]), default=None)
    below, above = round_levels(last)
    return {
        "symbol": symbol,
        "price": round(last, 2),
        "prev_close": round(prev, 2) if prev else None,
        "gap_pct": round((last / prev - 1.0) * 100.0, 2) if prev else None,
        "or_high": round(orr["high"], 2) if orr else None,
        "or_low": round(orr["low"], 2) if orr else None,
        "or_ready": bool(orr),
        "day_high": round(day_high, 2),
        "day_low": round(day_low, 2),
        "high_20d": round(high20, 2) if high20 else None,
        "ma20": round(ma20, 2) if ma20 else None,
        "round_below": below,
        "round_above": above,
        "bars": len(bars),
    }


def main(argv=None):
    p = argparse.ArgumentParser(prog="alerts.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("symbols", nargs="*")
    p.add_argument("--watchlist", help="watchlist JSON (default if no symbols)")
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--now")
    p.add_argument("--offline")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    now = C.now_ist(args.now)
    symbols = [C.bare_symbol(s) for s in args.symbols if s.strip()]
    if not symbols:
        path = args.watchlist or (C.trader_dir() / "watchlist.json")
        wl = C.read_json(path, {}) or {}
        symbols = [C.bare_symbol(h["symbol"]) for h in (wl.get("hits") or [])
                   if isinstance(h, dict) and h.get("symbol")]
    if not symbols:
        print("No symbols. Pass symbols or run the scanner to build trader/watchlist.json.")
        return 1

    print("Alert levels, {} IST (5-minute bars now, daily bars for the rest).".format(
        now.strftime("%a %d %b %Y %H:%M")))
    rows = []
    for sym in symbols:
        try:
            row = levels_for(sym, now, args.or_minutes, args.offline)
        except C.TraderError as exc:
            print("  {:12s} no data ({})".format(sym, exc))
            continue
        if not row:
            print("  {:12s} no bars today (holiday, halt, or no Yahoo data)".format(sym))
            continue
        rows.append(row)
        gap = " {} vs prev {:,.2f}".format(C.pct(row["gap_pct"]), row["prev_close"]) \
            if row["prev_close"] else ""
        print("  {:12s} INR {:>10,.2f}{}".format(sym, row["price"], gap))
        if row["or_ready"]:
            print("               OR {:,.2f} - {:,.2f}  (up alert above {:,.2f}, down below {:,.2f})"
                  .format(row["or_low"], row["or_high"], row["or_high"], row["or_low"]))
        else:
            print("               OR still forming")
        bits = []
        if row["high_20d"]:
            bits.append("20-day high {:,.2f}".format(row["high_20d"]))
        if row["ma20"]:
            bits.append("MA20 {:,.2f}".format(row["ma20"]))
        print("               " + "  ".join(bits))
        print("               day {:,.2f} - {:,.2f}  round {:,.2f} / {:,.2f}".format(
            row["day_low"], row["day_high"], row["round_below"], row["round_above"]))

    if not rows:
        return 1
    print("\nSet these in your broker app. A crossed level is a prompt to look, not an order.")
    if args.json:
        print(json.dumps({"as_of": now.isoformat(timespec="seconds"), "levels": rows}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
