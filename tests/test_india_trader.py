"""Offline tests for the India day-trader pack. No network, no real clock."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import common as C  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
SCRIPTS = {
    "scan": ROOT / "in-scanner" / "scan.py",
    "news": ROOT / "in-news" / "news.py",
    "entry": ROOT / "in-entry" / "entry.py",
    "exit": ROOT / "in-exit" / "exit.py",
    "timemachine": ROOT / "in-timemachine" / "timemachine.py",
    "size": ROOT / "in-budget" / "size.py",
    "check": ROOT / "in-risk" / "check.py",
    "ticket": ROOT / "in-broker" / "ticket.py",
    "alerts": ROOT / "in-alerts" / "alerts.py",
}

BHAV_HEADER = ("SYMBOL,SERIES,DATE1,PREV_CLOSE,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,"
               "CLOSE_PRICE,TTL_TRD_QNTY,TURNOVER_LACS\n")


def business_days(end, count):
    days, day = [], end
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return list(reversed(days))


def bhav_row(sym, day, prev, o, h, l, c, turnover=10000.0):
    return "{},{},{},{:.2f},{:.2f},{:.2f},{:.2f},{:.2f},100000,{:.2f}\n".format(
        sym, "EQ", day.strftime("%d-%b-%Y"), prev, o, h, l, c, turnover)


def chart_payload(bars, prev_close=None):
    return {
        "chart": {
            "result": [{
                "meta": {"regularMarketPrice": bars[-1]["c"],
                         "chartPreviousClose": prev_close or bars[0]["o"],
                         "currency": "INR"},
                "timestamp": [int(b["t"].timestamp()) for b in bars],
                "indicators": {"quote": [{
                    "open": [b["o"] for b in bars],
                    "high": [b["h"] for b in bars],
                    "low": [b["l"] for b in bars],
                    "close": [b["c"] for b in bars],
                    "volume": [b.get("v", 1000) for b in bars],
                }]},
            }],
            "error": None,
        }
    }


def at(day, hour, minute):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.trader = self.tmp / "trader"
        self.trader.mkdir()
        (self.trader / "state").mkdir()
        now = datetime.now(tz=IST)
        (self.trader / "state" / "nse-holidays.json").write_text(json.dumps({
            "fetched": now.isoformat(timespec="seconds"),
            "source": "test",
            "holidays": {"1900-01-01": {"desc": "sentinel", "morning": None,
                                        "evening": None}},
        }), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def run_tool(self, name, *args, expect=0):
        env = os.environ.copy()
        env["TRADER_DIR"] = str(self.trader)
        env["TRADER_OFFLINE"] = "1"
        env["PYTHONPATH"] = str(ROOT)
        cp = subprocess.run([sys.executable, str(SCRIPTS[name]), *args],
                            cwd=str(ROOT), env=env, capture_output=True, text=True,
                            timeout=120)
        self.assertEqual(cp.returncode, expect,
                         "{} exited {}\nSTDOUT:\n{}\nSTDERR:\n{}".format(
                             name, cp.returncode, cp.stdout, cp.stderr))
        return cp.stdout + cp.stderr

    def write_universe(self, *symbols):
        (self.trader / "universe.txt").write_text(
            "".join("{}|{} Ltd\n".format(s, s.title()) for s in symbols), encoding="utf-8")

    def read(self, name):
        return json.loads((self.trader / name).read_text(encoding="utf-8"))


class TestMarketClock(unittest.TestCase):
    def test_holiday_and_session(self):
        holidays = {"2026-11-10": {"desc": "Diwali-Balipratipada", "morning": None,
                                   "evening": None}}
        t = datetime(2026, 11, 10, 11, 0, tzinfo=IST)
        self.assertEqual(C.market_state(t, holidays)["status"], "closed")
        t = datetime(2026, 11, 11, 9, 0, tzinfo=IST)
        self.assertEqual(C.market_state(t, holidays)["status"], "pre_open")
        t = datetime(2026, 11, 11, 10, 0, tzinfo=IST)
        self.assertEqual(C.market_state(t, holidays)["status"], "open")
        t = datetime(2026, 11, 11, 15, 45, tzinfo=IST)
        self.assertEqual(C.market_state(t, holidays)["status"], "closed")
        t = datetime(2026, 11, 14, 10, 0, tzinfo=IST)  # Saturday
        self.assertEqual(C.market_state(t, {})["status"], "closed")


class TestPieces(unittest.TestCase):
    def test_symbol_encoding(self):
        self.assertEqual(C.yahoo_symbol("M&M"), "M%26M.NS")
        self.assertEqual(C.bare_symbol("RELIANCE.NS"), "RELIANCE")
        self.assertEqual(C.bare_symbol("reliance"), "RELIANCE")

    def test_costs_are_rupee_charges(self):
        c = C.round_trip_costs(100.0, 102.0, 100, C.DEFAULT_COSTS)
        self.assertGreater(c["total"], 8.0)
        self.assertLess(c["total"], 15.0)
        self.assertIn("stt", c["breakdown"])
        self.assertAlmostEqual(c["breakdown"]["brokerage"], 6.06, places=2)

    def test_bars_skip_incomplete(self):
        day = date(2026, 11, 11)
        bars = [{"t": at(day, 9, 15), "o": 100, "h": 101, "l": 99, "c": 100.5, "v": 1},
                {"t": at(day, 9, 20), "o": 100, "h": 101, "l": 99, "c": 100.8, "v": 1}]
        payload = chart_payload(bars)
        got = C.bars_from_chart(payload, 5, now=at(day, 9, 22))
        self.assertEqual(len(got), 1)  # 09:20 bar not complete at 09:22
        got = C.bars_from_chart(payload, 5, now=at(day, 9, 26))
        self.assertEqual(len(got), 2)

    def test_daily_bars_keep_yahoo_open_time(self):
        day = date(2026, 11, 9)
        bar = {"t": at(day, 9, 15), "o": 100, "h": 101, "l": 99, "c": 100.5, "v": 1}
        got = C.bars_from_chart(chart_payload([bar]), 1440,
                                now=at(date(2026, 11, 11), 10, 0))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["t"], at(day, 0, 0))

    def test_opening_range(self):
        day = date(2026, 11, 11)
        bars = []
        for i, (o, h, l, c) in enumerate([(100, 101, 99, 100.5), (100.5, 102, 100, 101.5),
                                          (101.5, 102.5, 101, 102)]):
            bars.append({"t": at(day, 9, 15 + 5 * i), "o": o, "h": h, "l": l, "c": c,
                         "v": 1000, "interval": 5})
        orr = C.opening_range(bars, 15, day=day)
        self.assertEqual(orr["high"], 102.5)
        self.assertEqual(orr["low"], 99)
        self.assertEqual(orr["end"], at(day, 9, 30))


class TestDailyScan(Base):
    def _fixture(self):
        days = business_days(date(2026, 11, 9), 30)
        folder = self.tmp / "bhav"
        folder.mkdir()
        prev = {"AAA": 100.0, "BBB": 100.0}
        for i, day in enumerate(days):
            block = ""
            close = 100.0 + i * 0.3 if i < 29 else 112.0
            block += bhav_row("AAA", day, prev["AAA"], prev["AAA"], close + 0.5,
                              close - 0.5, close)
            prev["AAA"] = close
            block += bhav_row("BBB", day, 100.0, 100.0, 100.6, 99.4, 100.0)
            (folder / "{}.csv".format(day.isoformat())).write_text(
                BHAV_HEADER + block, encoding="utf-8")
        self.write_universe("AAA", "BBB")
        return folder

    def test_daily_finds_breakout(self):
        folder = self._fixture()
        out = self.run_tool("scan", "daily", "--offline", str(folder),
                            "--now", "2026-11-11 10:00")
        self.assertIn("charts matched a setup", out)
        data = self.read("watchlist.json")
        self.assertEqual(data["mode"], "daily")
        symbols = [h["symbol"] for h in data["hits"]]
        self.assertIn("AAA", symbols)
        self.assertNotIn("BBB", symbols)
        hit = next(h for h in data["hits"] if h["symbol"] == "AAA")
        self.assertIn("breakout_20d", hit["setup"])
        self.assertEqual(data["sessions_through"], "2026-11-09")

    def test_daily_staleness_is_reported(self):
        folder = self._fixture()
        out = self.run_tool("scan", "daily", "--offline", str(folder),
                            "--now", "2026-11-11 10:00")
        self.assertIn("sessions through 2026-11-09", out)


class TestOrbAndTimeMachine(Base):
    def _orb_chart(self):
        day = date(2026, 11, 11)
        spec = [
            (9, 15, 100.0, 101.0, 99.5, 100.5),
            (9, 20, 100.5, 101.0, 100.0, 100.8),
            (9, 25, 100.8, 101.0, 100.4, 100.9),
            (9, 30, 100.9, 101.3, 100.8, 101.2),
            (9, 35, 101.2, 101.6, 101.0, 101.4),
            (9, 40, 101.4, 101.5, 100.9, 101.0),
            (9, 45, 101.0, 101.1, 100.2, 100.4),
            (9, 50, 100.4, 100.6, 99.8, 100.0),
        ]
        bars = [{"t": at(day, h, m), "o": o, "h": hi, "l": lo, "c": c, "v": 5000}
                for h, m, o, hi, lo, c in spec]
        folder = self.tmp / "charts"
        folder.mkdir()
        (folder / "ORB1.json").write_text(
            json.dumps(chart_payload(bars, prev_close=100.0)), encoding="utf-8")
        self.write_universe("ORB1")
        return folder

    def test_orb_confirms_break(self):
        folder = self._orb_chart()
        out = self.run_tool("scan", "orb", "--offline", str(folder),
                            "--symbols", "ORB1", "--at", "09:50",
                            "--now", "2026-11-11 09:50")
        self.assertIn("101.20", out)
        data = self.read("watchlist.json")
        self.assertEqual(data["mode"], "orb")
        self.assertEqual(data["hits"][0]["symbol"], "ORB1")
        self.assertEqual(data["hits"][0]["or_high"], 101.0)

    def test_orb_refuses_before_range(self):
        folder = self._orb_chart()
        out = self.run_tool("scan", "orb", "--offline", str(folder),
                            "--symbols", "ORB1", "--now", "2026-11-11 09:25",
                            expect=1)
        self.assertIn("still forming", out)

    def test_timemachine_marks_failed_break(self):
        folder = self._orb_chart()
        out = self.run_tool("timemachine", "ORB1", "--date", "2026-11-11",
                            "--offline", str(folder), "--now", "2026-11-11 10:00")
        self.assertIn("First close above the range", out)
        self.assertIn("break failed", out)


class TestFullFlow(Base):
    def _daily_chart(self):
        days = business_days(date(2026, 11, 10), 30)
        bars = []
        for i, day in enumerate(days):
            close = 110.0 + i * 0.05
            bars.append({"t": at(day, 9, 15), "o": close - 0.2, "h": close + 1.5,
                         "l": close - 1.5, "c": close, "v": 100000})
        folder = self.tmp / "daily"
        folder.mkdir()
        (folder / "AAA.json").write_text(
            json.dumps(chart_payload(bars, prev_close=100.0)), encoding="utf-8")
        return folder

    def test_plan_gate_ticket_exit(self):
        charts = self._daily_chart()
        out = self.run_tool("entry", "AAA", "--entry", "110", "--stop", "105",
                            "--target", "122", "--offline", str(charts),
                            "--now", "2026-11-11 10:00")
        self.assertIn("Plan for AAA", out)
        plan = self.read("plan.json")
        self.assertEqual(plan["shares"], 200)
        self.assertAlmostEqual(plan["risk_inr"], 1000.0, places=2)
        self.assertEqual(plan["reward_risk"], 2.4)

        out = self.run_tool("check", "--now", "2026-11-11 10:00")
        self.assertIn("Verdict: PASS", out)

        out = self.run_tool("ticket", "--now", "2026-11-11 10:00")
        self.assertIn("BUY", out)
        orders = self.read("orders.json")["orders"]
        self.assertEqual(orders[0]["trigger_price"], 105.0)
        self.assertEqual(orders[0]["product"], "MIS")

        self.run_tool("ticket", "fill", "--price", "110.55",
                      "--now", "2026-11-11 10:05")
        pf = self.read("portfolio.json")
        self.assertEqual(pf["positions"]["AAA"]["shares"], 200)
        self.assertLess(pf["cash"], 100000)

        prices = self.tmp / "prices.json"
        prices.write_text(json.dumps({"AAA": 115.0}), encoding="utf-8")
        out = self.run_tool("exit", "--offline", str(prices),
                            "--now", "2026-11-11 11:00")
        self.assertIn("HOLD", out)

        self.run_tool("ticket", "close", "--price", "120",
                      "--now", "2026-11-11 11:30")
        trades = self.read("trades.json")["trades"]
        self.assertEqual(len(trades), 1)
        self.assertGreater(trades[0]["pnl"], 1700.0)
        self.assertEqual(trades[0]["date"], "2026-11-11")

    def test_plan_refuses_stop_above_structure(self):
        charts = self._daily_chart()
        out = self.run_tool("entry", "AAA", "--entry", "110", "--stop", "109.9",
                            "--target", "122", "--offline", str(charts),
                            "--now", "2026-11-11 10:00", expect=1)
        self.assertIn("above real structure", out)

    def test_gate_fails_without_plan(self):
        out = self.run_tool("check", "--now", "2026-11-11 10:00", expect=1)
        self.assertIn("no plan", out)


class TestSizer(Base):
    def test_size_respects_risk(self):
        out = self.run_tool("size", "--entry", "100", "--stop", "98",
                            "--risk", "1000", "--max-pct", "100")
        self.assertIn("500 shares", out)

    def test_size_refuses_bad_stop(self):
        self.run_tool("size", "--entry", "100", "--stop", "101", expect=1)


if __name__ == "__main__":
    unittest.main()
