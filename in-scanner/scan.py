#!/usr/bin/env python3
"""Chart Scanner for the Indian cash market (NSE).

    scan.py universe                 # NSE EQ symbol list -> trader/universe.txt
    scan.py daily                    # pre-open setups, whole universe
    scan.py orb --at 10:15           # opening-range breakouts so far

Free public data: NSE bhavcopy for daily bars, Yahoo for 5-minute bars.
A scan is a list of charts that match a rule, never a list of buys.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common as C  # noqa: E402


# ------------------------------------------------------------------ helpers

def _log(msg):
    print(msg, flush=True)


def _universe_map(rows):
    return {sym: name for sym, name in rows}


def _resolve_symbols(args, fallback):
    if args.symbols:
        return [s.strip().upper() for s in args.symbols.replace(" ", ",").split(",") if s.strip()]
    if args.watchlist:
        data = C.read_json(args.watchlist, {}) or {}
        hits = data.get("hits") or data.get("symbols") or []
        out = []
        for h in hits:
            out.append(h.get("symbol") if isinstance(h, dict) else str(h))
        return [s for s in out if s]
    return list(fallback)


def _setup_tags(rows, cfg):
    closes = [r["close"] for r in rows]
    price = closes[-1]
    tags = []

    d20_high = max(r["high"] for r in rows[-21:-1])
    if price > d20_high:
        tags.append("breakout_20d")

    if len(closes) >= 15:
        base = closes[-15]
        last5 = closes[-5:]
        if base > 0 and (closes[-5] / base - 1.0) > 0.10:
            if (max(last5) - min(last5)) / min(last5) <= 0.05:
                tags.append("bull_flag")

    if len(closes) >= 21:
        ma_prev = C.sma(closes[:-1], 20)
        ma_now = C.sma(closes, 20)
        if ma_prev and ma_now and closes[-2] < ma_prev <= closes[-1]:
            tags.append("reclaim_ma20")

    r = C.rsi(closes)
    r_prev = C.rsi(closes[:-1])
    if r is not None and r < 30:
        tags.append("oversold")
    if r_prev is not None and r_prev < 30 and closes[-1] > closes[-2]:
        if "oversold" not in tags:
            tags.append("oversold")

    return tags, r


# ----------------------------------------------------------------- universe

def cmd_universe(args):
    rows = C.fetch_universe(refresh=True)
    out = C.trader_dir() / "universe.txt"
    out.write_text("".join("{}|{}\n".format(s, n) for s, n in rows), encoding="utf-8")
    _log("NSE EQ symbol list: {} plain common-stock symbols.".format(len(rows)))
    _log("Wrote {}".format(out))
    return 0


# -------------------------------------------------------------------- daily

def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, "year") and not isinstance(value, str):
        return value
    text = str(value)
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise C.TraderError("cannot read a date from {!r}".format(value))


def _offline_daily(path):
    """Load {symbol: [rows]} from a folder of bhavcopy CSVs or one JSON."""
    p = Path(path)
    if p.is_file():
        data = C.read_json(p, {}) or {}
        out = {}
        for sym, rows in data.items():
            clean = []
            for r in rows:
                r = dict(r)
                r["date"] = _as_date(r.get("date"))
                r["symbol"] = C.bare_symbol(r.get("symbol") or sym)
                clean.append(r)
            clean.sort(key=lambda r: r["date"])
            out[C.bare_symbol(sym)] = clean
        return out
    if p.is_dir():
        by_symbol = {}
        for f in sorted(p.glob("*.csv")):
            raw = C.read_text(f)
            rows = _parse_bhav_text(raw)
            for r in rows:
                by_symbol.setdefault(r["symbol"], []).append(r)
        for sym, rows in by_symbol.items():
            seen, clean = set(), []
            for r in sorted(rows, key=lambda r: r["date"]):
                if r["date"] in seen:
                    continue
                seen.add(r["date"])
                clean.append(r)
            by_symbol[sym] = clean
        return by_symbol
    raise C.TraderError("offline path not found: {}".format(path))


def _parse_bhav_text(raw):
    import csv
    import io
    reader = csv.reader(io.StringIO(raw))
    header = [h.strip() for h in next(reader, [])]
    idx = {name: i for i, name in enumerate(header)}
    out = []
    for row in reader:
        if not row or len(row) < len(header):
            continue
        try:
            sym = row[idx["SYMBOL"]].strip().upper()
            series = row[idx["SERIES"]].strip().upper()
            day = datetime.strptime(row[idx["DATE1"]].strip(), "%d-%b-%Y").date()
            close = float(row[idx["CLOSE_PRICE"]])
            prev = float(row[idx["PREV_CLOSE"]])
            o = float(row[idx["OPEN_PRICE"]])
            h = float(row[idx["HIGH_PRICE"]])
            l = float(row[idx["LOW_PRICE"]])
            turn = float(row[idx["TURNOVER_LACS"]] or 0)
        except (KeyError, IndexError, ValueError):
            continue
        if series != "EQ" or close <= 0:
            continue
        out.append({"symbol": sym, "date": day, "prev_close": prev, "open": o,
                    "high": h, "low": l, "close": close, "turnover_lacs": turn})
    return out


def cmd_daily(args):
    cfg = C.load_config()
    now = C.now_ist(args.now)
    state = C.market_state(now)
    if state["status"] == "open":
        note = "market open: today's bhavcopy is not out yet, so closes run through the last session"
    else:
        note = "closes run through the last published session"

    if args.offline:
        by_symbol = _offline_daily(args.offline)
        sessions = max((len(v) for v in by_symbol.values()), default=0)
        through = max((r["date"] for v in by_symbol.values() for r in v[-1:]), default=None)
    else:
        _log("Reading NSE bhavcopy for {} sessions (cached under trader/cache/bhav)...".format(args.sessions))
        by_symbol = C.bhavcopy_history(sessions=args.sessions, refresh=args.refresh,
                                       now=now, log=_log)
        sessions = max((len(v) for v in by_symbol.values()), default=0)
        through = max((v[-1]["date"] for v in by_symbol.values() if v), default=None)

    if not by_symbol:
        _log("No bhavcopy data available. Run again after 18:00 IST on a trading day, "
             "or pass --offline with saved bhavcopy CSVs.")
        return 1

    universe_rows = C.load_universe_file()
    names = _universe_map(universe_rows) if universe_rows else {}
    wanted = _resolve_symbols(args, [s for s, _ in universe_rows] or list(by_symbol))

    min_price = args.min_price if args.min_price is not None else cfg["min_price"]
    min_turn = args.min_turnover_lacs if args.min_turnover_lacs is not None else cfg["min_turnover_lacs"]
    last_session = through

    scanned = liquid = stale = 0
    hits = []
    for sym in wanted:
        rows = by_symbol.get(sym)
        if not rows or len(rows) < 25:
            continue
        scanned += 1
        if rows[-1]["date"] != last_session:
            stale += 1
            continue
        price = rows[-1]["close"]
        avg_turn = sum(r["turnover_lacs"] for r in rows[-20:]) / 20.0
        if price < min_price or avg_turn < min_turn:
            continue
        liquid += 1
        tags, r = _setup_tags(rows, cfg)
        if not tags:
            continue
        prev = rows[-1]["prev_close"] or price
        hits.append({
            "symbol": sym,
            "name": names.get(sym, ""),
            "price": round(price, 2),
            "change_pct": round((price / prev - 1.0) * 100.0, 2) if prev else None,
            "setup": tags,
            "rsi14": round(r, 1) if r is not None else None,
            "avg_turnover_lacs": round(avg_turn, 1),
            "setup_score": len(tags),
        })

    hits.sort(key=lambda h: (h["setup_score"], h["avg_turnover_lacs"]), reverse=True)
    counts = {}
    for h in hits:
        for tag in h["setup"]:
            counts[tag] = counts.get(tag, 0) + 1

    if args.limit:
        shown_pool = hits[: args.limit]
    else:
        shown_pool = hits

    header = ("Daily scan, {} IST, sessions through {} ({} sessions, NSE bhavcopy).\n"
              "{}.\n"
              "{} EQ symbols, {} passed liquidity (INR {}+ and INR {:.1f} lakh+ average "
              "turnover), {} scanned{}.".format(
                  now.strftime("%a %d %b %Y %H:%M"), last_session, sessions, note,
                  len(wanted), liquid, C.num(min_price, 0), min_turn, scanned,
                  ", {} stale".format(stale) if stale else ""))

    if not shown_pool:
        _log(header)
        _log("No chart matched a rule. Nothing written.")
        return 1

    summary = ", ".join("{} {}".format(v, k) for k, v in
                        sorted(counts.items(), key=lambda kv: kv[1], reverse=True))
    _log(header)
    _log("{} charts matched a setup: {}.".format(len(hits), summary))
    _log("Most traded first (price x 20-day average turnover):")
    for h in shown_pool[: args.show]:
        tags = ", ".join(h["setup"])
        rsi = "RSI {:>5.1f}".format(h["rsi14"]) if h["rsi14"] is not None else "RSI   n/a"
        _log("  {:12s} INR {:>10,.2f}  {}  {}".format(h["symbol"], h["price"], rsi, tags))
    if len(shown_pool) > args.show:
        _log("  ... and {} more in the watchlist (--show N for more).".format(
            len(shown_pool) - args.show))

    payload = {
        "mode": "daily",
        "as_of": now.isoformat(timespec="seconds"),
        "source": "NSE bhavcopy (EQ series)",
        "sessions": sessions,
        "sessions_through": last_session.isoformat() if last_session else None,
        "universe": len(wanted),
        "scanned": scanned,
        "liquid": liquid,
        "failed": 0,
        "stale": stale,
        "hits": hits,
    }
    if not args.no_write:
        out = C.write_json(C.trader_dir() / "watchlist.json", payload)
        _log("Wrote {} (mode daily, {} hits).".format(out, len(hits)))
    if args.json:
        print(json.dumps(payload, indent=2))
    _log("A scan is a list of charts that match a rule, not a list of stocks to buy.")
    return 0


# ---------------------------------------------------------------------- orb

def _offline_chart(folder, symbol):
    p = Path(folder)
    for name in ("{}.json".format(symbol), "{}.NS.json".format(symbol),
                 "{}.json.gz".format(symbol)):
        f = p / name
        if f.exists():
            if f.suffix == ".gz":
                import gzip
                with gzip.open(f, "rt", encoding="utf-8") as fh:
                    return json.load(fh)
            return C.read_json(f)
    return None


def _spark_bars(entry, now):
    """Build closable (t, close) pairs from a spark entry for stage 1."""
    out = []
    for stamp, close in zip(entry.get("timestamps") or [], entry.get("closes") or []):
        if close is None:
            continue
        try:
            t = datetime.fromtimestamp(stamp, tz=C.IST)
        except (OverflowError, OSError, ValueError):
            continue
        if t.second or t.minute % 5:
            continue
        if t + timedelta(minutes=5) > now:
            continue
        out.append((t, float(close)))
    out.sort()
    return out


def cmd_orb(args):
    cfg = C.load_config()
    now = C.now_ist(args.now)
    state = C.market_state(now)
    day = now.date()
    or_minutes = args.or_minutes
    open_dt = C.at_time(day, C.SESSION_OPEN)
    or_end = open_dt + timedelta(minutes=or_minutes)
    cutoff = C.parse_hhmm(args.at) if args.at else None
    if cutoff:
        cutoff_dt = C.at_time(day, cutoff)
    else:
        cutoff_dt = now

    if state["status"] == "closed":
        _log("NSE is closed ({}). The opening-range scan runs after 09:31 IST on a trading day."
             .format(state["reason"]))
        return 1
    if state["status"] == "pre_open":
        _log("NSE has not opened yet ({}) IST. The opening range starts at 09:15.".format(
            now.strftime("%H:%M")))
        return 1
    if now < or_end:
        _log("The opening range is still forming: it runs 09:15-{:02d}:{:02d} IST.".format(
            or_end.hour, or_end.minute))
        return 1
    no_after = C.parse_hhmm(cfg.get("no_entries_after"), C.dtime(14, 45))
    if cutoff_dt.time() > no_after and not args.allow_late:
        _log("Past no_entries_after ({:%H:%M} IST): the setup is expired and no new entry is allowed."
             .format(no_after))
        return 1

    universe_rows = C.load_universe_file()
    names = _universe_map(universe_rows)
    all_symbols = [s for s, _ in universe_rows] if universe_rows else []
    if not all_symbols:
        _log("No trader/universe.txt. Run `scan.py universe` first, or pass --symbols.")
        return 1
    wanted = _resolve_symbols(args, all_symbols)
    if args.limit:
        wanted = wanted[: args.limit]

    # Stage 1: spark closes only, 20 symbols per request.
    stage1 = {}
    failed = 0
    batch = 20
    _log("Fetching 5-minute closes for {} symbols ({} spark requests)...".format(
        len(wanted), int(math.ceil(len(wanted) / batch))))
    for i in range(0, len(wanted), batch):
        chunk = wanted[i:i + batch]
        try:
            if args.offline:
                payload = {}
                for sym in chunk:
                    raw = _offline_chart(args.offline, sym)
                    if raw:
                        payload[sym] = raw
            else:
                payload = C.yahoo_spark(chunk, range_="1d", interval="5m")
        except C.TraderError:
            failed += len(chunk)
            continue
        for sym in chunk:
            if args.offline:
                raw = payload.get(sym)
                if not raw:
                    failed += 1
                    continue
                bars = C.bars_from_chart(raw, 5, now=now, session_day=day)
                orr = C.opening_range(bars, or_minutes, day=day)
                if not orr:
                    failed += 1
                    continue
                stage1[sym] = {"chart": raw, "bars": bars, "or": orr}
            else:
                entry = payload.get(sym)
                if not entry:
                    failed += 1
                    continue
                pairs = _spark_bars(entry, now)
                range_closes = [c for t, c in pairs if open_dt <= t < or_end]
                if len(range_closes) < or_minutes // 5:
                    failed += 1
                    continue
                or_high = max(range_closes)
                broke = any(c >= or_high for t, c in pairs if t >= or_end and t < cutoff_dt)
                if broke:
                    stage1[sym] = {"or_close_high": or_high,
                                   "last": pairs[-1][1] if pairs else None}
        if not args.offline and (i // batch) % 5 == 0:
            _log("  spark {} of {} requests".format(min(i + batch, len(wanted)), len(wanted)))
        if not args.offline:
            C.time.sleep(0.15)

    candidates = list(stage1)
    skipped = 0
    if args.max_confirm and len(candidates) > args.max_confirm:
        skipped = len(candidates) - args.max_confirm
        candidates = candidates[: args.max_confirm]
        partial = "PARTIAL: {} candidates, checking the first {} (--max-confirm 0 checks all).".format(
            len(stage1), args.max_confirm)
    else:
        partial = None

    hits = []
    confirmed = 0
    for sym in candidates:
        try:
            if args.offline:
                bars = stage1[sym].get("bars") or []
            else:
                payload = C.yahoo_chart(sym, "1d", "5m")
                bars = C.bars_from_chart(payload, 5, now=now, session_day=day)
        except C.TraderError:
            failed += 1
            continue
        orr = C.opening_range(bars, or_minutes, day=day)
        if not orr:
            failed += 1
            continue
        bar = C.first_close_above(bars, orr["high"], orr["end"], until=cutoff_dt)
        if not bar:
            continue
        confirmed += 1
        over = (bar["c"] / orr["high"] - 1.0) * 100.0
        locked = abs(bar["h"] - bar["l"]) < 1e-9
        hits.append({
            "symbol": sym,
            "name": names.get(sym, ""),
            "time": (bar["t"] + timedelta(minutes=5)).strftime("%H:%M"),
            "bar_time": bar["t"].strftime("%H:%M"),
            "price": round(bar["c"], 2),
            "or_high": round(orr["high"], 2),
            "or_low": round(orr["low"], 2),
            "over_pct": round(over, 2),
            "setup": ["orb"],
            "circuit_locked": locked,
        })

    hits.sort(key=lambda h: h["time"])
    header = ("Opening-range scan, {} IST (range 09:15-{:02d}:{:02d}, 5-minute bars, Yahoo, "
              "free and unofficial).".format(now.strftime("%a %d %b %Y %H:%M"),
                                             or_end.hour, or_end.minute))
    _log(header)
    _log("Fetched {} of {} charts ({} failed or had no data).".format(
        len(wanted) - failed, len(wanted), failed))
    if partial:
        _log(partial)

    if not hits:
        _log("No usable chart closed above its opening-range high by {} IST.".format(
            cutoff_dt.strftime("%H:%M")))
        return 1

    _log("{} of {} checked charts closed above their opening-range high by {} IST:".format(
        len(hits), len(candidates), cutoff_dt.strftime("%H:%M")))
    for h in hits[: args.show]:
        warn = "  CIRCUIT-LOCKED" if h["circuit_locked"] else ""
        _log("  {:>5s}  {:12s} INR {:>10,.2f}  range {:,.2f} to {:,.2f}  {:+.2f}% over{}".format(
            h["time"], h["symbol"], h["price"], h["or_low"], h["or_high"],
            h["over_pct"], warn))
    if len(hits) > args.show:
        _log("  ... and {} more.".format(len(hits) - args.show))

    payload = {
        "mode": "orb",
        "as_of": now.isoformat(timespec="seconds"),
        "source": "Yahoo 5-minute bars (unofficial)",
        "or_minutes": or_minutes,
        "cutoff": cutoff_dt.strftime("%H:%M"),
        "universe": len(wanted),
        "scanned": len(wanted) - failed,
        "failed": failed,
        "candidates": len(stage1),
        "confirmed": confirmed,
        "skipped": skipped,
        "hits": hits,
    }
    if not args.no_write:
        out = C.write_json(C.trader_dir() / "watchlist.json", payload)
        _log("Wrote {} (mode orb, {} hits).".format(out, len(hits)))
    if args.json:
        print(json.dumps(payload, indent=2))
    _log("A hit is stale once the price has moved on. Re-run before acting on it.")
    return 0


# --------------------------------------------------------------------- main

def build_parser():
    p = argparse.ArgumentParser(prog="scan.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    u = sub.add_parser("universe", help="download the NSE EQ symbol list")
    u.set_defaults(func=cmd_universe)

    d = sub.add_parser("daily", help="pre-market daily scan")
    d.add_argument("--sessions", type=int, default=40)
    d.add_argument("--symbols", help="comma-separated symbols")
    d.add_argument("--watchlist", help="read symbols from a watchlist JSON")
    d.add_argument("--limit", type=int)
    d.add_argument("--min-price", type=float)
    d.add_argument("--min-turnover-lacs", type=float)
    d.add_argument("--refresh", action="store_true", help="redownload bhavcopy files")
    d.add_argument("--offline", help="folder of bhavcopy CSVs, or a JSON of rows")
    d.add_argument("--now", help='pin the clock, e.g. "2026-11-12 07:30"')
    d.add_argument("--no-write", action="store_true")
    d.add_argument("--show", type=int, default=25)
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_daily)

    o = sub.add_parser("orb", help="opening-range breakout scan")
    o.add_argument("--at", help="cut the clock at HH:MM IST")
    o.add_argument("--or-minutes", type=int, default=15)
    o.add_argument("--symbols")
    o.add_argument("--watchlist")
    o.add_argument("--limit", type=int, help="only the first N symbols")
    o.add_argument("--max-confirm", type=int, default=200,
                   help="stage-2 chart checks (0 = all)")
    o.add_argument("--allow-late", action="store_true",
                   help="scan even after no_entries_after")
    o.add_argument("--offline", help="folder of saved Yahoo chart JSON files")
    o.add_argument("--now")
    o.add_argument("--no-write", action="store_true")
    o.add_argument("--show", type=int, default=25)
    o.add_argument("--json", action="store_true")
    o.set_defaults(func=cmd_orb)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except C.TraderError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
