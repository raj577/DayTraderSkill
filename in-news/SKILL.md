---
name: in-news
description: Check fresh Yahoo Finance headlines and the price move for NSE symbols. Use when the user asks "any news on my stocks", wants a pre-open news check for an Indian watchlist, or needs to know why a stock is moving today.
---

# News Monitor (NSE)

Headlines are leads, not facts. This skill shows what is new since the last run
and tags it. It never trades on a headline.

`news.py` is next to this file:

```bash
python in-news/news.py
python in-news/news.py --tickers RELIANCE,TCS,INFY
python in-news/news.py --hours 24 --move-pct 5 --all
```

With no `--tickers`, it reads the scanner watchlist, the current plan, and open
positions.

Output per item: time, symbol, tag, headline, link. Then the move from
yesterday's close, with `MOVING` when the move clears `--move-pct` (default 3%).

Tags: earnings, analyst, deal, legal, people, capital, risk, halt, regulatory,
macro.

State: seen headlines are remembered in `trader/state/news.json` for 30 days, so
a second run the same morning prints only new items. `--all` shows everything in
the window, `--dry-run` does not save state. **Exit 1 means nothing new** - that
is an answer, not a failure.

## Reading news on Indian names

- Filings beat headlines. NSE/BSE announcements under "Corporate Announcements"
  are the primary source; Yahoo may lag or mislabel.
- Corporate actions matter to intraday prices: bonus, split, and dividend
  ex-dates move the open. A "sudden drop" on an ex-date is not a sell signal.
- Results season: an earnings headline after 15:30 changes the *next* session,
  not today's.
- Regulator and government items (SEBI, RBI, GST, tariffs, bans) can move a whole
  sector. Mention the sector link if the user holds peers.
- An F&O ban or a circuit filter can make an exit impossible at the planned
  price. Check `python in-risk/check.py` before entering.

## What to tell the user

- What is new, on which symbol, and the tag that applies.
- Whether the move is already large (this is context, not permission).
- Which claim needs the filing opened before acting.
- Never state a headline's implication as fact; quote the headline and link.
