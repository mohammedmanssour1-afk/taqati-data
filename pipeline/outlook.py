"""Estimate whether the next fuel table is likely to go up or down.

Two signals, both public:
- The trend: the direction of the last change in the ministry tables.
- Wholesale product prices: New York Harbor conventional gasoline (for petrol) and ultra-low
  sulfur diesel (for diesel), daily spot prices from the U.S. Energy Information Administration,
  read from FRED (Federal Reserve Bank of St. Louis). The average of the last 7 days is compared
  with the 7 days that ended 2 days before the latest table.

A call ("up" or "down") is made only when both signals agree; otherwise the outlook is "unclear".
Backtested on the 2024-2026 tables, agreeing signals were right about 90% of the time and agreed
on roughly half of the tables. The accuracy shipped in outlook.json is recomputed on every run.
"""
import csv
import io
from datetime import date, timedelta

from .common import fetch

FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}&cosd={start}"
SERIES = {"g95": "DGASNYH", "diesel": "DDFUELNYH"}
WINDOW = 7
LAG = 2


def parse_fred(text, series_id):
    out = {}
    for r in csv.DictReader(io.StringIO(text or "")):
        v = r.get(series_id)
        if v and v != ".":
            try:
                out[date.fromisoformat(r["observation_date"])] = float(v)
            except ValueError:
                pass
    return out


def fetch_series(series_id, start):
    return parse_fred(fetch(FRED.format(id=series_id, start=start.isoformat())), series_id)


def _avg(prices, after, upto):
    xs = [v for d, v in prices.items() if after < d <= upto]
    return sum(xs) / len(xs) if xs else None


def product_move(prices, table_day, now):
    """Percent change of the recent average against the average just before the latest table."""
    before = _avg(prices, table_day - timedelta(days=WINDOW + LAG), table_day - timedelta(days=LAG))
    recent = _avg(prices, now - timedelta(days=WINDOW), now)
    if not before or not recent:
        return None
    return (recent - before) / before * 100


def call(trend, move):
    """trend: last table change (LL). move: product price change (%). Returns up/down/unclear."""
    if not trend or move is None or move == 0:
        return "unclear"
    if (trend > 0) == (move > 0):
        return "up" if trend > 0 else "down"
    return "unclear"


def backtest(rows, key, prices):
    """How often a call matched the next table, over the history. Returns (right, made)."""
    right = made = 0
    for i in range(1, len(rows) - 1):
        t0 = date.fromisoformat(rows[i]["date"])
        t1 = date.fromisoformat(rows[i + 1]["date"])
        c = call(rows[i][key] - rows[i - 1][key], product_move(prices, t0, t1 - timedelta(days=1)))
        actual = rows[i + 1][key] - rows[i][key]
        if c == "unclear" or actual == 0:
            continue
        made += 1
        right += (c == "up") == (actual > 0)
    return right, made


def build(rows, prices_by_key, today):
    """rows: fuel rows sorted by date. Returns the outlook.json object, or None."""
    if len(rows) < 3:
        return None
    last, prev = rows[-1], rows[-2]
    out = {"after_table": last["date"], "method": "trend + NY Harbor wholesale prices (EIA via FRED)"}
    for key, prices in prices_by_key.items():
        if not prices:
            return None
        c = call(last[key] - prev[key], product_move(prices, date.fromisoformat(last["date"]), today))
        right, made = backtest(rows, key, prices)
        out[key] = {"call": c, "right": right, "tested": made}
    return out


def update(fuel_rows, today):
    rows = sorted(fuel_rows, key=lambda r: r["date"])
    start = date.fromisoformat(rows[0]["date"]) - timedelta(days=30) if rows else today
    prices = {k: fetch_series(sid, start) for k, sid in SERIES.items()}
    return build(rows, prices, today)
