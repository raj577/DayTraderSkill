"""Shared helpers for the trader-skill-india pack.

Standard library only. ASCII output on purpose: Windows pipes default to
cp1252 and a stray rupee sign would crash a tool mid-run.

Every skill imports this module by adding the pack root to sys.path:

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from common import ...
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, time as dtime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

# ---------------------------------------------------------------- constants

IST = timezone(timedelta(hours=5, minutes=30), "IST")
SESSION_OPEN = dtime(9, 15)
SESSION_CLOSE = dtime(15, 30)
OR_MINUTES = 15
DEFAULT_EXCHANGE = "NS"

UA = "trader-skill-india/1.0 (personal research; public NSE and Yahoo data)"
NSE_HOME = "https://www.nseindia.com/"
NSE_HOLIDAY_URL = "https://www.nseindia.com/api/holiday-master?type=trading"
NSE_UNIVERSE_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
NSE_BHAV_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"
NSE_BAN_URL = "https://nsearchives.nseindia.com/content/fo/fo_secban.csv"
NSE_NIFTY500_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"

DEFAULT_COSTS = {
    # Cash-equity intraday (MIS) at a discount broker, NSE.
    "brokerage_pct": 0.0003,     # 0.03% per side
    "brokerage_cap": 20.0,       # or INR 20, whichever is lower
    "stt_sell": 0.00025,         # 0.025% on the sell side (intraday)
    "exchange_txn": 0.0000297,   # NSE transaction charge
    "sebi": 0.000001,            # INR 10 per crore
    "stamp_buy": 0.00003,        # 0.003% on the buy side
    "gst": 0.18,                 # on brokerage + txn + sebi
    "dp_per_sell": 0.0,          # no DP charge on intraday square-off
}

DEFAULT_CONFIG = {
    "broker": "none",
    "exchange": "NSE",
    "product": "MIS",
    "paper": True,
    "budget": 100000.0,
    "cash_buffer_pct": 10.0,
    "max_loss_per_trade": 1000.0,
    "max_daily_loss": 3000.0,
    "max_trades_per_day": 3,
    "max_orders_per_day": 10,
    "max_position_pct": 25.0,
    "max_order": 50000.0,
    "min_order": 500.0,
    "min_reward_risk": 1.0,
    "require_stop": True,
    "no_entries_before": "09:20",
    "no_entries_after": "14:45",
    "flat_by": "15:12",
    "min_price": 20.0,
    "min_turnover_lacs": 500.0,
    "allow_short": False,
    "allow_options": False,
    "blocked": [],
    "costs": DEFAULT_COSTS,
}


class TraderError(RuntimeError):
    """A clean, user-facing failure. Print str(exc), exit 1."""


# ------------------------------------------------------------------- files

def _root() -> Path:
    return Path(__file__).resolve().parent


def trader_dir() -> Path:
    p = Path(os.environ.get("TRADER_DIR", "trader"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def state_dir() -> Path:
    p = trader_dir() / "state"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    p = trader_dir() / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError):
        return default
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return default


def write_json(path, obj) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=True, default=str) + "\n",
                   encoding="utf-8")
    tmp.replace(p)
    return p


def read_text(path, default=""):
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return default


# ------------------------------------------------------------------ format

def money(x) -> str:
    try:
        return "INR {:,.2f}".format(float(x))
    except (TypeError, ValueError):
        return "INR n/a"


def num(x, digits=2) -> str:
    try:
        return "{:,.{d}f}".format(float(x), d=digits)
    except (TypeError, ValueError):
        return "n/a"


def pct(x) -> str:
    try:
        return "{:+.2f}%".format(float(x))
    except (TypeError, ValueError):
        return "n/a"


def short_date(d) -> str:
    if isinstance(d, datetime):
        d = d.date()
    return d.strftime("%a %d %b %Y")


# ------------------------------------------------------------------- clock

def now_ist(pin=None) -> datetime:
    """Current time in IST, or the pinned clock. Pin format: YYYY-MM-DD HH:MM."""
    if pin is None:
        return datetime.now(tz=IST)
    if isinstance(pin, datetime):
        return pin.replace(tzinfo=IST) if pin.tzinfo is None else pin.astimezone(IST)
    txt = str(pin).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(txt, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    raise TraderError("bad --now value {!r}; use \"2026-11-12 10:15\"".format(pin))


def parse_hhmm(value, default=None) -> dtime:
    if value in (None, ""):
        return default
    txt = str(value).strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(txt, fmt).time()
        except ValueError:
            continue
    raise TraderError("bad time {!r}; use HH:MM".format(value))


def at_time(day: date, t: dtime) -> datetime:
    return datetime.combine(day, t, IST)


def minutes_into_session(dt: datetime) -> float:
    start = at_time(dt.date(), SESSION_OPEN)
    return (dt - start).total_seconds() / 60.0


# --------------------------------------------------------------- holidays

_HOLIDAY_CACHE = None


def _parse_nse_date(text: str) -> date:
    return datetime.strptime(text.strip(), "%d-%b-%Y").date()


def fetch_holidays(refresh=False, timeout=25) -> dict:
    """NSE trading holidays, cached for 7 days. Falls back to the cache.

    Returns {"YYYY-MM-DD": {"desc": str, "morning": str|None, "evening": str|None}}.
    """
    global _HOLIDAY_CACHE
    cache = state_dir() / "nse-holidays.json"
    saved = read_json(cache)
    if saved and not refresh:
        try:
            age = (datetime.now(tz=IST) - datetime.fromisoformat(saved["fetched"])).days
            if age <= 7 and saved.get("holidays"):
                _HOLIDAY_CACHE = saved["holidays"]
                return _HOLIDAY_CACHE
        except (KeyError, ValueError, TypeError):
            pass
    try:
        raw = http_get(NSE_HOLIDAY_URL, timeout=timeout,
                       headers={"Accept": "application/json", "Referer": NSE_HOME})
        payload = json.loads(raw.decode("utf-8", "replace"))
        out = {}
        for segment in ("CM", "CBM", "CMOT"):
            for row in payload.get(segment) or []:
                try:
                    day = _parse_nse_date(row.get("tradingDate", ""))
                except (ValueError, TypeError):
                    continue
                out[day.isoformat()] = {
                    "desc": (row.get("description") or "holiday").strip(),
                    "morning": row.get("morning_session"),
                    "evening": row.get("evening_session"),
                }
        if not out:
            raise TraderError("no holiday rows returned")
        write_json(cache, {"fetched": datetime.now(tz=IST).isoformat(timespec="seconds"),
                           "source": NSE_HOLIDAY_URL, "holidays": out})
        _HOLIDAY_CACHE = out
        return out
    except Exception as exc:  # network, JSON, NSE changes
        if saved and saved.get("holidays"):
            _HOLIDAY_CACHE = saved["holidays"]
            return _HOLIDAY_CACHE
        _HOLIDAY_CACHE = {}
        return {}


def load_holidays(refresh=False) -> dict:
    if _HOLIDAY_CACHE is not None and not refresh:
        return _HOLIDAY_CACHE
    return fetch_holidays(refresh=refresh)


def holiday_info(day: date, holidays=None):
    h = load_holidays() if holidays is None else holidays
    return h.get(day.isoformat())


def is_trading_day(day: date, holidays=None) -> tuple:
    info = holiday_info(day, holidays)
    if day.weekday() >= 5:
        return False, "weekend"
    if info:
        return False, "holiday: " + info.get("desc", "market holiday")
    return True, "trading day"


def market_state(now=None, holidays=None) -> dict:
    n = now if isinstance(now, datetime) else now_ist(now)
    n = n.replace(tzinfo=IST) if n.tzinfo is None else n.astimezone(IST)
    day = n.date()
    tradeable, why = is_trading_day(day, holidays)
    opened = at_time(day, SESSION_OPEN)
    close_dt = at_time(day, SESSION_CLOSE)
    if not tradeable:
        status = "closed"
    elif n < opened:
        status = "pre_open"
    elif n <= close_dt:
        status = "open"
    else:
        status = "closed"
    return {
        "status": status,
        "reason": why if not tradeable else ("regular session" if status == "open" else
                                             ("before the open" if status == "pre_open" else "after the close")),
        "now": n,
        "session_open": opened,
        "session_close": close_dt,
        "next_open": next_open(n, holidays),
    }


def next_open(from_dt=None, holidays=None, days_ahead=20) -> datetime:
    n = from_dt if isinstance(from_dt, datetime) else now_ist(from_dt)
    n = n.replace(tzinfo=IST) if n.tzinfo is None else n.astimezone(IST)
    day = n.date()
    for _ in range(days_ahead):
        tradeable, _why = is_trading_day(day, holidays)
        candidate = at_time(day, SESSION_OPEN)
        if tradeable and candidate > n:
            return candidate
        day += timedelta(days=1)
    return at_time(day, SESSION_OPEN)


# -------------------------------------------------------------------- http

def http_get(url, timeout=25, retries=2, headers=None, pause=1.0) -> bytes:
    if os.environ.get("TRADER_OFFLINE") == "1":
        raise TraderError("offline mode (TRADER_OFFLINE=1): refusing to fetch {}".format(
            _short_url(url)))
    hdrs = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "en-IN,en;q=0.9",
    }
    if headers:
        hdrs.update(headers)
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(pause * (attempt + 2))
                continue
            raise TraderError("HTTP {} from {}".format(exc.code, _short_url(url))) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(pause * (attempt + 1))
                continue
            raise TraderError("network error for {}: {}".format(_short_url(url), exc)) from exc
    raise TraderError("network error for {}: {}".format(_short_url(url), last))


def _short_url(url: str) -> str:
    return url.split("?")[0][:120]


def http_json(url, timeout=25, retries=2, headers=None):
    return json.loads(http_get(url, timeout=timeout, retries=retries, headers=headers)
                      .decode("utf-8", "replace"))


# ------------------------------------------------------------------ yahoo

def yahoo_symbol(symbol: str, exchange=DEFAULT_EXCHANGE) -> str:
    s = (symbol or "").strip().upper()
    if s in ("^NSEI", "^NSEBANK", "^INDIAVIX"):
        return urllib.parse.quote(s, safe="")
    if s.endswith(".NS") or s.endswith(".BO"):
        return urllib.parse.quote(s, safe="")
    return urllib.parse.quote("{}.{}".format(s, exchange), safe="")


def bare_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    return s[:-3] if s.endswith((".NS", ".BO")) else s


def _yahoo_json(path: str, query: str, timeout=25):
    last = None
    for host in ("query2", "query1"):
        url = "https://{}.finance.yahoo.com{}?{}".format(host, path, query)
        try:
            return http_json(url, timeout=timeout)
        except Exception as exc:  # try the other host
            last = exc
    raise TraderError("Yahoo request failed: {}".format(last))


def yahoo_chart(symbol, range_="1d", interval="5m", exchange=DEFAULT_EXCHANGE):
    q = yahoo_symbol(symbol, exchange)
    payload = _yahoo_json(
        "/v8/finance/chart/{}".format(q),
        "range={}&interval={}&includePrePost=false&events=div%2Csplits".format(range_, interval),
    )
    if not payload.get("chart", {}).get("result"):
        err = payload.get("chart", {}).get("error") or {}
        raise TraderError("no chart data for {} ({})".format(
            bare_symbol(symbol), err.get("description") or "empty result"))
    return payload


def yahoo_spark(symbols, range_="1mo", interval="1d", exchange=DEFAULT_EXCHANGE):
    """One spark request for up to 20 symbols. Returns {bare_symbol: {...}}."""
    syms = list(symbols)
    if not syms:
        return {}
    q = ",".join(yahoo_symbol(s, exchange) for s in syms)
    payload = _yahoo_json(
        "/v8/finance/spark",
        "symbols={}&range={}&interval={}".format(urllib.parse.quote(q, safe=",%^"), range_, interval),
    )
    out = {}
    for key, value in (payload or {}).items():
        if not isinstance(value, dict):
            continue
        out[bare_symbol(key)] = {
            "closes": value.get("close") or [],
            "timestamps": value.get("timestamp") or [],
            "prev_close": value.get("chartPreviousClose") or value.get("previousClose"),
            "price": value.get("fulldayPrice") or (value.get("close") or [None])[-1],
        }
    return out


def yahoo_quote(symbol, exchange=DEFAULT_EXCHANGE) -> dict:
    payload = yahoo_chart(symbol, "1d", "5m", exchange)
    meta = payload["chart"]["result"][0].get("meta") or {}
    price = meta.get("regularMarketPrice")
    if price is None:
        closes = (payload["chart"]["result"][0].get("indicators", {})
                  .get("quote", [{}])[0].get("close") or [])
        price = next((c for c in reversed(closes) if c is not None), None)
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    return {
        "symbol": bare_symbol(symbol),
        "price": price,
        "prev_close": prev,
        "change_pct": ((price - prev) / prev * 100.0) if price and prev else None,
        "quote_time": meta.get("regularMarketTime"),
        "currency": meta.get("currency"),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
    }


def yahoo_news(symbol, exchange=DEFAULT_EXCHANGE, limit=40):
    url = ("https://feeds.finance.yahoo.com/rss/2.0/headline?s={}"
           "&region=US&lang=en-US".format(yahoo_symbol(symbol, exchange)))
    raw = http_get(url, timeout=25)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise TraderError("could not parse the Yahoo headline feed: {}".format(exc))
    items = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        pub = (node.findtext("pubDate") or "").strip()
        desc = (node.findtext("description") or "").strip()
        when = None
        if pub:
            try:
                when = parsedate_to_datetime(pub).astimezone(IST)
            except (TypeError, ValueError):
                when = None
        if title:
            items.append({"title": title, "link": link, "published": when,
                          "description": desc})
    items.sort(key=lambda i: i["published"] or datetime.min.replace(tzinfo=IST), reverse=True)
    return items[:limit]


# ------------------------------------------------------------------- bars

def bars_from_chart(payload, interval_minutes=5, now=None, require_complete=True,
                    session_day=None):
    """Normalize a Yahoo chart payload into [{t, o, h, l, c, v, interval}]."""
    result = (payload.get("chart", {}).get("result") or [{}])[0]
    stamps = result.get("timestamp") or []
    quotes = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quotes.get("open") or []
    highs = quotes.get("high") or []
    lows = quotes.get("low") or []
    closes = quotes.get("close") or []
    vols = quotes.get("volume") or []
    n = now if isinstance(now, datetime) else now_ist(now)
    n = n.replace(tzinfo=IST) if n.tzinfo is None else n.astimezone(IST)
    out = []
    for i, stamp in enumerate(stamps):
        try:
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        except IndexError:
            break
        if None in (o, h, l, c):
            continue
        try:
            t = datetime.fromtimestamp(stamp, tz=IST)
        except (OverflowError, OSError, ValueError):
            continue
        if interval_minutes >= 1440:
            t = t.replace(hour=0, minute=0, second=0, microsecond=0)
            if session_day is not None and t.date() != session_day:
                continue
            complete = True
        else:
            if t.second or t.minute % interval_minutes:
                continue  # a live tick, not a grid bar
            if session_day is not None and t.date() != session_day:
                continue
            complete = (t + timedelta(minutes=interval_minutes)) <= n
            if require_complete and not complete:
                continue
        try:
            v = int(vols[i] or 0)
        except IndexError:
            v = 0
        out.append({"t": t, "o": float(o), "h": float(h), "l": float(l),
                    "c": float(c), "v": v, "interval": interval_minutes,
                    "complete": complete})
    out.sort(key=lambda b: b["t"])
    return out


def opening_range(bars, minutes=OR_MINUTES, day=None):
    """Build the opening range from complete bars starting at 09:15."""
    if not bars:
        return None
    day = day or bars[0]["t"].date()
    start = at_time(day, SESSION_OPEN)
    end = start + timedelta(minutes=minutes)
    interval = bars[0]["interval"]
    wanted = minutes // interval
    picked = [b for b in bars
              if b["t"] >= start and b["t"] < end
              and b["t"] + timedelta(minutes=interval) <= end]
    if len(picked) < wanted:
        return None
    return {"high": max(b["h"] for b in picked),
            "low": min(b["l"] for b in picked),
            "bars": picked,
            "start": picked[0]["t"],
            "end": picked[-1]["t"] + timedelta(minutes=interval),
            "minutes": minutes,
            "complete": True}


def first_close_above(bars, level, after, until=None, min_volume_x=None, range_bars=None):
    """First completed bar after `after` whose close is strictly above `level`."""
    for bar in bars:
        if bar["t"] < after:
            continue
        if until is not None and bar["t"] >= until:
            break
        if bar["c"] <= level:
            continue
        if min_volume_x:
            base = [b["v"] for b in (range_bars or []) if b["v"]]
            avg = sum(base) / len(base) if base else 0
            if not avg or bar["v"] < avg * float(min_volume_x):
                continue
        return bar
    return None


# --------------------------------------------------------------- universe

_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")


def plain_nse_symbol(symbol: str) -> bool:
    s = (symbol or "").strip().upper()
    if not _SYMBOL_RE.match(s):
        return False
    if s.endswith(("-RE", "-BE", "-BZ")):
        return False
    return True


def fetch_universe(refresh=False):
    """NSE EQ series symbols with company names. Returns [(symbol, name)]."""
    cache = cache_dir() / "universe.txt"
    if cache.exists() and not refresh:
        rows = []
        for line in read_text(cache).splitlines():
            if "|" in line:
                sym, name = line.split("|", 1)
                rows.append((sym.strip(), name.strip()))
        if rows:
            return rows
    raw = http_get(NSE_UNIVERSE_URL).decode("utf-8-sig", "replace")
    reader = csv.reader(io.StringIO(raw))
    header = [h.strip() for h in next(reader, [])]
    index = {name: i for i, name in enumerate(header)}
    out = []
    for row in reader:
        if not row:
            continue
        try:
            sym = row[index["SYMBOL"]].strip().upper()
            name = row[index["NAME OF COMPANY"]].strip()
            series = row[index["SERIES"]].strip().upper()
        except (KeyError, IndexError):
            continue
        if series != "EQ" or not plain_nse_symbol(sym):
            continue
        out.append((sym, name))
    if not out:
        raise TraderError("the NSE symbol list came back empty")
    out.sort()
    cache.write_text("".join("{}|{}\n".format(s, n) for s, n in out), encoding="utf-8")
    return out


def load_universe_file():
    path = trader_dir() / "universe.txt"
    rows = []
    for line in read_text(path).splitlines():
        if "|" in line:
            sym, name = line.split("|", 1)
            rows.append((sym.strip(), name.strip()))
        elif line.strip():
            rows.append((line.strip().upper(), ""))
    return rows


def nifty500():
    raw = http_get(NSE_NIFTY500_URL).decode("utf-8-sig", "replace")
    reader = csv.DictReader(io.StringIO(raw))
    return {((r.get("Symbol") or "").strip().upper()) for r in reader if r.get("Symbol")}


def fno_ban():
    """Symbols in the F&O ban period today (entries are risky there)."""
    try:
        raw = http_get(NSE_BAN_URL).decode("utf-8", "replace")
    except TraderError:
        return set()
    out = set()
    for line in raw.splitlines()[1:]:
        parts = [p.strip().upper() for p in line.split(",")]
        if parts and parts[0] and not parts[0].startswith("SECURITIES"):
            out.add(parts[0])
    return out


# --------------------------------------------------------------- bhavcopy

def _bhav_url(day: date) -> str:
    return NSE_BHAV_URL.format(ddmmyyyy=day.strftime("%d%m%Y"))


def fetch_bhavcopy(day: date, refresh=False):
    """Return (actual_session_date, rows) for one file, or None if unavailable.

    NSE republishes the previous session's file at a holiday's URL, so the
    DATE1 column decides the real session and we never trust the URL alone.
    """
    cache = cache_dir() / "bhav" / "{}.csv".format(day.isoformat())
    cache.parent.mkdir(parents=True, exist_ok=True)
    raw = None
    if cache.exists() and not refresh:
        raw = read_text(cache)
    if not raw:
        try:
            raw = http_get(_bhav_url(day), timeout=30).decode("utf-8-sig", "replace")
        except TraderError:
            return None
        cache.write_text(raw, encoding="utf-8")
    reader = csv.reader(io.StringIO(raw))
    header = [h.strip() for h in next(reader, [])]
    idx = {name: i for i, name in enumerate(header)}
    rows = []
    actual = None
    for row in reader:
        if not row or len(row) < len(header):
            continue
        try:
            sym = row[idx["SYMBOL"]].strip().upper()
            series = row[idx["SERIES"]].strip().upper()
            day_text = row[idx["DATE1"]].strip()
        except (KeyError, IndexError):
            continue
        try:
            actual_day = datetime.strptime(day_text, "%d-%b-%Y").date()
        except ValueError:
            continue
        actual = actual or actual_day
        if series != "EQ" or not plain_nse_symbol(sym):
            continue
        try:
            row_close = float(row[idx["CLOSE_PRICE"]])
            row_prev = float(row[idx["PREV_CLOSE"]])
            row_open = float(row[idx["OPEN_PRICE"]])
            row_high = float(row[idx["HIGH_PRICE"]])
            row_low = float(row[idx["LOW_PRICE"]])
            qty = int(float(row[idx["TTL_TRD_QNTY"]] or 0))
            turn = float(row[idx["TURNOVER_LACS"]] or 0)
        except (KeyError, ValueError):
            continue
        if row_close <= 0:
            continue
        rows.append({
            "symbol": sym, "date": actual_day, "prev_close": row_prev,
            "open": row_open, "high": row_high, "low": row_low, "close": row_close,
            "volume": qty, "turnover_lacs": turn,
        })
    if not rows or actual is None:
        return None
    return actual, rows


def bhavcopy_history(sessions=40, refresh=False, now=None, pause=0.25, log=None):
    """Descending walk back from the last session; returns {symbol: [rows asc]}."""
    n = now if isinstance(now, datetime) else now_ist(now)
    n = n.replace(tzinfo=IST) if n.tzinfo is None else n.astimezone(IST)
    day = n.date()
    # Try today's file first: before 18:00 IST it is usually absent (404), which
    # costs one request and then falls back one day. Never trust the URL date;
    # the DATE1 column in fetch_bhavcopy decides the real session.
    by_symbol = {}
    seen = set()
    misses = 0
    holiday_set = load_holidays()
    attempts = 0
    while len(seen) < sessions and attempts < sessions * 3 + 20:
        attempts += 1
        if day.weekday() < 5 and not holiday_info(day, holiday_set):
            got = fetch_bhavcopy(day, refresh=refresh)
            if got:
                actual, rows = got
                if actual not in seen:
                    seen.add(actual)
                    for r in rows:
                        by_symbol.setdefault(r["symbol"], []).append(r)
                    if log:
                        log("  bhavcopy {} ({}/{})".format(actual, len(seen), sessions))
                misses = 0
            else:
                misses += 1
                if misses > 8:
                    break
        day -= timedelta(days=1)
        if pause and not cache_dir().joinpath("bhav").exists():
            time.sleep(pause)
    for rows in by_symbol.values():
        rows.sort(key=lambda r: r["date"])
    return by_symbol


# ------------------------------------------------------------------ costs

def costs_config(cfg=None) -> dict:
    base = dict(DEFAULT_COSTS)
    if cfg:
        base.update({k: v for k, v in (cfg.get("costs") or {}).items() if v is not None})
    return base


def round_trip_costs(entry, exit_price, qty, costs=None) -> dict:
    """Full round-trip charges for a cash-equity intraday trade."""
    c = dict(DEFAULT_COSTS)
    if costs:
        c.update(costs)
    qty = float(qty)
    entry = float(entry)
    exit_price = float(exit_price)
    buy = entry * qty
    sell = exit_price * qty
    turnover = buy + sell
    brokerage = min(c["brokerage_cap"], c["brokerage_pct"] * buy)
    brokerage += min(c["brokerage_cap"], c["brokerage_pct"] * sell)
    txn = c["exchange_txn"] * turnover
    sebi = c["sebi"] * turnover
    stamp = c["stamp_buy"] * buy
    stt = c["stt_sell"] * sell
    gst = c["gst"] * (brokerage + txn + sebi)
    dp = c["dp_per_sell"] if sell else 0.0
    total = brokerage + txn + sebi + stamp + stt + gst + dp
    return {
        "total": round(total, 2),
        "per_share": round(total / qty, 4) if qty else 0.0,
        "breakdown": {
            "brokerage": round(brokerage, 2), "exchange": round(txn, 2),
            "sebi": round(sebi, 2), "stamp": round(stamp, 2),
            "stt": round(stt, 2), "gst": round(gst, 2), "dp": round(dp, 2),
        },
    }


# ----------------------------------------------------------------- config

def deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path=None) -> dict:
    if path is None:
        path = trader_dir() / "config.json"
    cfg = read_json(path, {}) or {}
    return deep_merge(DEFAULT_CONFIG, cfg)


def ensure_config(sample: Path) -> Path:
    dest = trader_dir() / "config.json"
    if not dest.exists() and Path(sample).exists():
        dest.write_text(Path(sample).read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def load_portfolio(cfg) -> dict:
    data = read_json(trader_dir() / "portfolio.json")
    if not data:
        return {"cash": float(cfg.get("budget", 0.0)), "positions": {},
                "source": "fresh paper account funded with the budget"}
    data.setdefault("cash", float(cfg.get("budget", 0.0)))
    data.setdefault("positions", {})
    return data


def load_prices(max_age_minutes=15, now=None) -> dict:
    data = read_json(trader_dir() / "prices.json", {}) or {}
    prices = dict(data.get("prices") or {})
    as_of = None
    if data.get("as_of"):
        try:
            as_of = datetime.fromisoformat(data["as_of"])
            if as_of.tzinfo is None:
                as_of = as_of.replace(tzinfo=IST)
            age = (now_ist(now) - as_of).total_seconds() / 60.0
            if age > max_age_minutes:
                return {"prices": {}, "as_of": as_of, "stale": True, "age_minutes": age}
            return {"prices": {bare_symbol(k): float(v) for k, v in prices.items() if v},
                    "as_of": as_of, "stale": False, "age_minutes": age}
        except (ValueError, TypeError):
            pass
    return {"prices": {bare_symbol(k): float(v) for k, v in prices.items() if v},
            "as_of": as_of, "stale": as_of is None, "age_minutes": None}


def write_prices(prices: dict, now=None) -> Path:
    payload = {"as_of": now_ist(now).isoformat(timespec="seconds"),
               "prices": {bare_symbol(k): round(float(v), 4) for k, v in prices.items()
                          if v is not None}}
    return write_json(trader_dir() / "prices.json", payload)


# ------------------------------------------------------------------ math

def rsi(closes, period=14):
    if len(closes) <= period:
        return None
    gains, losses = [], []
    for prev, cur in zip(closes[:-1], closes[1:]):
        diff = cur - prev
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def atr(bars, period=14):
    if len(bars) < 2:
        return None
    true_ranges = []
    for prev, cur in zip(bars[:-1], bars[1:]):
        true_ranges.append(max(cur["h"] - cur["l"],
                               abs(cur["h"] - prev["c"]),
                               abs(cur["l"] - prev["c"])))
    period = min(period, len(true_ranges))
    return sum(true_ranges[-period:]) / period


# ------------------------------------------------------------- niceties

def candle_span(bar) -> str:
    return "{} {}-{}".format(bar["t"].strftime("%Y-%m-%d"),
                             bar["t"].strftime("%H:%M"),
                             (bar["t"] + timedelta(minutes=bar["interval"])).strftime("%H:%M"))


def fingerprint(obj) -> str:
    import hashlib
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def clamp(value, low, high):
    return max(low, min(high, value))


def read_stdin_json(default=None):
    if sys.stdin.isatty():
        return default
    try:
        return json.loads(sys.stdin.read() or "null")
    except json.JSONDecodeError:
        return default
