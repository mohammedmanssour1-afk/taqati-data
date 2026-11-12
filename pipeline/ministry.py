"""Ministry of Energy and Water: the official price lists.

The ministry's price pages (energyandwater.gov.lb/ar/prices?type=N) load their tables from a
public endpoint. Types: 1 = fuel (95, 98, kerosene, mazout, diesel), 2 = generator tariff,
5 = gas cylinder (10 kg).
"""
import json
import re
from datetime import date, timedelta

from .common import fetch, parse_ar_date, parse_int

ENDPOINT = "https://www.energyandwater.gov.lb/ajax/pricesCtrl.php"
FUEL, TARIFF, GAS = 1, 2, 5


def _rows(price_type, start, end):
    """All table rows (lists of cell text) for a type and date range, across pages."""
    rows, page, pages = [], 0, 1
    while page < pages and page < 50:
        body = (f"action=RenderPrices&priceTypeId={price_type}&page={page}"
                f"&fromDate={start.isoformat()}&toDate={end.isoformat()}")
        text = fetch(ENDPOINT, data=body)
        if not text:
            return None
        try:
            html = json.loads(text).get("html", "")
        except ValueError:
            return None
        found = [int(p) for p in re.findall(r"loadPrices\((\d+)", html)]
        pages = max([pages] + [p + 1 for p in found])
        for tr in re.findall(r"(?s)<tr.*?</tr>", html):
            cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip()
                     for c in re.findall(r"(?s)<td.*?</td>", tr)]
            if cells:
                rows.append(cells)
        page += 1
    return rows


def fuel(start, end):
    """[{date, g95, g98, diesel, decision}] or None when the ministry is unreachable."""
    rows = _rows(FUEL, start, end)
    if rows is None:
        return None
    out = []
    for c in rows:
        d = parse_ar_date(c[0]) if c else None
        if not d or len(c) < 7:
            continue
        g95, g98 = parse_int(c[2]), parse_int(c[3])
        mazout, diesel = parse_int(c[5]) or 0, parse_int(c[6]) or 0
        # Older tables put road diesel under "mazout"; newer ones under "diesel oil".
        out.append({"date": d.isoformat(), "g95": g95, "g98": g98,
                    "diesel": diesel or mazout, "decision": c[1] or None})
    return out


def gas(start, end):
    """{date: price} or None."""
    rows = _rows(GAS, start, end)
    if rows is None:
        return None
    out = {}
    for c in rows:
        d = parse_ar_date(c[0]) if c else None
        if d and len(c) >= 3:
            out[d.isoformat()] = parse_int(c[2])
    return out


def tariff_month(published):
    """Tariffs are issued at the end of the month they cover, sometimes on the 1st of the next."""
    d = published if published.day > 7 else published.replace(day=1) - timedelta(days=1)
    return f"{d.year:04d}-{d.month:02d}"


def tariffs(start, end):
    """[{month, urban, canister, published}] or None."""
    rows = _rows(TARIFF, start, end)
    if rows is None:
        return None
    out = []
    for c in rows:
        d = parse_ar_date(c[0]) if c else None
        if not d:
            continue
        # Columns: 5A, 10A, diesel canister, kWh. Some rows have an empty cell in between,
        # so pick values by size rather than position.
        nums = [n for n in (parse_int(x) for x in c[2:]) if n]
        urban = next((n for n in reversed(nums) if 10_000 <= n <= 200_000), None)
        canister = next((n for n in nums if 700_000 <= n <= 8_000_000), None)
        if urban:
            out.append({"month": tariff_month(d), "urban": urban, "canister": canister,
                        "published": d.isoformat()})
    return out


if __name__ == "__main__":
    today = date.today()
    print(fuel(today - timedelta(days=30), today))
    print(gas(today - timedelta(days=60), today))
    print(tariffs(today - timedelta(days=90), today))
