#!/usr/bin/env python3
"""News Monitor for Indian stocks.

Reads Yahoo Finance headlines for NSE tickers, shows only what is new since
the last check, tags each item, and prints the move from yesterday's close.
Headlines are leads, not facts. This tool never trades on news.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common as C  # noqa: E402

TAGS = [
    ("earnings", ("earnings", "results", "q1", "q2", "q3", "q4", "profit", "revenue", "margin", "guidance")),
    ("analyst", ("upgrade", "downgrade", "target price", "rating", "brokerage", "analyst")),
    ("deal", ("acquisition", "merger", "stake", "deal", "takeover", "joint venture",
              "order win", "order for", "secures order", "bags order", "wins order",
              "contract")),
    ("legal", ("lawsuit", "probe", "investigation", "sebi", "cbi", "ed ", "fraud", "penalty", "fine")),
    ("people", ("ceo", "cfo", "managing director", "resign", "appoint", "board")),
    ("capital", ("qip", "ipo", "buyback", "dividend", "bonus", "split", "rights issue", "fundraise", "ofs")),
    ("risk", ("default", "downgrade", "insolvency", "nclt", "pledge", "warning")),
    ("halt", ("halt", "circuit", "suspended", "freeze")),
    ("regulatory", ("rbi", "sebi", "government", "ministry", "tariff", "ban", "policy", "gst")),
    ("macro", ("inflation", "cpi", "gdp", "fed", "crude", "rupee", "bond yield", "fii", "dii")),
]


def tag_for(text):
    low = (text or "").lower()
    return [name for name, words in TAGS if any(w in low for w in words)]


def gather_symbols(args):
    out = []
    if args.tickers:
        raw = args.tickers if isinstance(args.tickers, str) else " ".join(args.tickers)
        out = [s.strip().upper() for s in raw.replace(" ", ",").split(",") if s.strip()]
    else:
        wl = C.read_json(C.trader_dir() / "watchlist.json", {}) or {}
        for hit in wl.get("hits") or []:
            if isinstance(hit, dict) and hit.get("symbol"):
                out.append(hit["symbol"])
        plan = C.read_json(C.trader_dir() / "plan.json", {}) or {}
        if plan.get("symbol"):
            out.append(plan["symbol"])
        pf = C.read_json(C.trader_dir() / "portfolio.json", {}) or {}
        out.extend((pf.get("positions") or {}).keys())
    seen, clean = set(), []
    for s in out:
        s = C.bare_symbol(s)
        if s and s not in seen:
            seen.add(s)
            clean.append(s)
    return clean


def main(argv=None):
    p = argparse.ArgumentParser(prog="news.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tickers", nargs="*", help="symbols (default: watchlist, plan, portfolio)")
    p.add_argument("--hours", type=float, default=12.0, help="lookback window (default 12)")
    p.add_argument("--move-pct", type=float, default=3.0)
    p.add_argument("--all", action="store_true", help="show items already seen")
    p.add_argument("--no-quote", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--now", help='pin the clock, e.g. "2026-11-12 09:00"')
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    now = C.now_ist(args.now)
    since = now - timedelta(hours=args.hours)
    symbols = gather_symbols(args)
    if not symbols:
        print("No symbols. Pass --tickers, or run the scanner first to build trader/watchlist.json.")
        return 1

    state_path = C.state_dir() / "news.json"
    state = C.read_json(state_path, {}) or {"seen": {}, "cik": {}}
    seen = state.get("seen") or {}

    items, quotes = [], {}
    failures = []
    for sym in symbols:
        try:
            news = C.yahoo_news(sym)
        except C.TraderError as exc:
            failures.append("{}: {}".format(sym, exc))
            news = []
        for n in news:
            when = n.get("published")
            if when is None or when < since:
                continue
            key = C.fingerprint({"t": n["title"], "s": sym})
            if key in seen and not args.all:
                continue
            items.append({
                "symbol": sym,
                "title": n["title"],
                "link": n["link"],
                "published": when.isoformat(timespec="minutes"),
                "time": when.strftime("%H:%M"),
                "tags": tag_for(n["title"]),
                "key": key,
            })
        if not args.no_quote:
            try:
                quotes[sym] = C.yahoo_quote(sym)
            except C.TraderError:
                pass

    items.sort(key=lambda i: i["published"], reverse=True)

    if not args.no_quote:
        fresh = {s: q["price"] for s, q in quotes.items() if q.get("price")}
        if fresh:
            C.write_prices(fresh, now)

    print("News check, {} IST, last {:.0f} hours, {} ticker(s).".format(
        now.strftime("%a %d %b %Y %H:%M"), args.hours, len(symbols)))
    if failures:
        print("Feed errors: " + "; ".join(failures[:6]))
    if not items:
        print("Nothing new. Exit 1 means nothing to report.")
        return 1

    for item in items:
        tags = ", ".join(item["tags"]) or "untagged"
        print("  {}  {:12s} [{}] {}".format(item["time"], item["symbol"], tags, item["title"]))
        if item["link"]:
            print("           {}".format(item["link"]))

    if quotes:
        print("\nPrice against yesterday's close:")
        for sym in symbols:
            q = quotes.get(sym)
            if not q or q.get("price") is None:
                continue
            change = q.get("change_pct")
            moving = "  MOVING" if change is not None and abs(change) >= args.move_pct else ""
            prev = " prev {:,.2f}".format(q["prev_close"]) if q.get("prev_close") else ""
            print("  {:12s} INR {:>10,.2f}{} ({}){}".format(
                sym, q["price"], prev, C.pct(change) if change is not None else "n/a", moving))

    print("\nHeadlines are leads, not facts. Open the story before acting on it. "
          "Filings beat headlines; check NSE/BSE announcements for the company.")
    print("This tool never places an order and never says what a price will do next.")

    payload = {"as_of": now.isoformat(timespec="seconds"), "hours": args.hours,
               "items": items, "quotes": quotes, "failures": failures}
    if args.json:
        print(json.dumps(payload, indent=2))

    if not args.dry_run:
        cutoff = (now - timedelta(days=30)).date()
        kept = {}
        for key, when in seen.items():
            try:
                if datetime.fromisoformat(when).date() >= cutoff:
                    kept[key] = when
            except (ValueError, TypeError):
                continue
        for item in items:
            kept[item["key"]] = now.isoformat(timespec="seconds")
        C.write_json(state_path, {"seen": kept, "updated": now.isoformat(timespec="seconds")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
