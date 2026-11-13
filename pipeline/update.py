"""Update the Taqati data files.

    python -m pipeline.update                 # regular run (every 3 hours on GitHub)
    python -m pipeline.update --backfill 2024-12-01
    python -m pipeline.update --no-press      # ministry only

Rules
- The ministry is the source of truth. Its rows replace any seed or press row for the same date.
- Press rows are added only for dates the ministry does not cover, only when at least two
  trusted outlets agree, and only when they pass the checks in validate.py.
- Single-source press readings go to data/review.json for a person to check; they are not shipped.
- Files are rewritten only when something changed, so the scheduled job commits only new data.
"""
import argparse
import json
import os
from datetime import date, datetime, timedelta, timezone

from . import ministry, outlook, press
from .validate import fuel_ok, in_range, tariff_ok

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
MIN_AGREEING = 2
GAS_MAX_AGE = 14  # days
PRESS_REACH_DAYS = 40
NEIGHBOUR_TOLERANCE = 0.10  # single-source press rows must be within 10% of the nearest table


def _load(name, default):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save(name, obj):
    with open(os.path.join(DATA, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")


class Store:
    def __init__(self):
        self.fuel = {r["date"]: r for r in _load("fuel.json", {"rows": []})["rows"]}
        self.tariffs = {r["month"]: r for r in _load("tariffs.json", {"rows": []})["rows"]}
        self.review = _load("review.json", {"rows": []})["rows"]
        self.rate = _load("rates.json", {})
        self.outlook = _load("outlook.json", {})
        self.changes = []

    # Fuel ---------------------------------------------------------------

    def _gas_before(self, d):
        prior = [r for k, r in self.fuel.items() if k < d and r.get("gas")]
        return max(prior, key=lambda r: r["date"])["gas"] if prior else None

    def put_fuel(self, row, source):
        d = row["date"]
        old = self.fuel.get(d)
        gas, carried = row.get("gas"), False
        if not gas and old and old.get("gas"):
            gas, carried = old["gas"], bool(old.get("gas_carried"))
        if not gas:
            gas, carried = self._gas_before(d), True
        new = {"date": d, "g95": row["g95"], "g98": row["g98"], "diesel": row["diesel"], "gas": gas,
               "source": source}
        if carried:
            new["gas_carried"] = True
        if row.get("decision"):
            new["decision"] = row["decision"]
        ok, why = fuel_ok(new)
        if not ok:
            print(f"  rejected fuel {d} from {source}: {why}")
            return
        keep = {k: v for k, v in (old or {}).items() if k != "source"}
        if old and keep == {k: v for k, v in new.items() if k != "source"}:
            if old.get("source") == "seed" and source == "ministry":
                old["source"] = "ministry"  # same figures, now confirmed by the ministry
                self.changes.append(f"fuel {d} confirmed by the ministry")
            return
        self.fuel[d] = new
        self.changes.append(f"fuel {d} {'updated' if old else 'added'} ({source}): 95={new['g95']:,}")

    def has_same_table(self, row, days=4):
        """A press article often repeats a table published a day or two earlier."""
        d = date.fromisoformat(row["date"])
        for k, r in self.fuel.items():
            if abs((date.fromisoformat(k) - d).days) <= days and \
                    (r["g95"], r["g98"], r["diesel"]) == (row["g95"], row["g98"], row["diesel"]):
                return True
        return False

    def close_to_neighbours(self, row, tolerance=NEIGHBOUR_TOLERANCE):
        """A single-outlet table is trusted only when every price is near the closest stored
        table (prices move a few percent a week)."""
        if not self.fuel:
            return False
        d = date.fromisoformat(row["date"])
        near = min(self.fuel.values(), key=lambda r: abs((date.fromisoformat(r["date"]) - d).days))
        if abs((date.fromisoformat(near["date"]) - d).days) > 21:
            return False
        return all(abs(row[k] - near[k]) <= tolerance * near[k] for k in ("g95", "g98", "diesel"))

    # Tariffs ------------------------------------------------------------

    def put_tariff(self, row, source):
        m = row["month"]
        old = self.tariffs.get(m)
        canister = row.get("canister") or (old or {}).get("canister")
        new = {"month": m, "urban": row["urban"], "canister": canister, "source": source}
        ok, why = tariff_ok(new)
        if not ok:
            print(f"  rejected tariff {m} from {source}: {why}")
            return
        if old and (old["urban"], old.get("canister")) == (new["urban"], new["canister"]):
            return
        self.tariffs[m] = new
        self.changes.append(f"tariff {m} {'updated' if old else 'added'} ({source}): {new['urban']:,} LL/kWh")

    def add_review(self, kind, row):
        key = (kind, row.get("date") or row.get("month"))
        if any((r["kind"], r.get("date") or r.get("month")) == key for r in self.review):
            return
        self.review.append(dict(row, kind=kind))
        self.changes.append(f"review: single-source {kind} {key[1]}")

    def put_rate(self, value, as_of, source):
        old = self.rate.get("usd_lbp")
        new = {"usd_lbp": value, "as_of": as_of, "source": source,
               "basis": "Dollar rate used in the Ministry of Energy and Water fuel price table"}
        if old == value and self.rate.get("as_of", "") >= as_of:
            return
        self.rate = new
        self.changes.append(f"dollar rate {value:,} LL as of {as_of} ({source})")

    def save(self):
        if not self.changes:
            print("No new data.")
            return False
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        fuel = sorted(self.fuel.values(), key=lambda r: r["date"])
        tariffs = sorted(self.tariffs.values(), key=lambda r: r["month"])
        _save("fuel.json", {"updated": now, "rows": fuel})
        _save("tariffs.json", {"updated": now, "rows": tariffs})
        _save("review.json", {"rows": self.review})
        if self.rate:
            _save("rates.json", self.rate)
        if self.outlook:
            _save("outlook.json", self.outlook)
        _save("meta.json", {"updated": now, "fuel_rows": len(fuel), "tariff_rows": len(tariffs),
                            "latest_fuel": fuel[-1]["date"] if fuel else None,
                            "latest_tariff": tariffs[-1]["month"] if tariffs else None})
        print("\n".join(self.changes))
        return True


def from_ministry(store, start, end):
    """Returns True when the ministry answered."""
    rows = ministry.fuel(start, end)
    if rows is None:
        print("Ministry unreachable for fuel.")
        return False
    gas = ministry.gas(start - timedelta(days=120), end) or {}
    gas = {d: v for d, v in gas.items() if in_range("gas", v)}
    gas_dates = sorted(gas)
    # Oldest first, so a table without its own gas price can take the one before it.
    for r in sorted(rows, key=lambda r: r["date"]):
        # Gas is published on its own, sparser list. Use it only when it is recent for this
        # table; otherwise keep the stored value or the previous table's gas.
        prior = [g for g in gas_dates if g <= r["date"]]
        fresh = prior and (date.fromisoformat(r["date"]) - date.fromisoformat(prior[-1])).days <= GAS_MAX_AGE
        r["gas"] = gas[prior[-1]] if fresh else None
        store.put_fuel(r, "ministry")
    for t in ministry.tariffs(start - timedelta(days=40), end) or []:
        store.put_tariff(t, "ministry")
    return True


def from_press(store, start, end):
    for r in press.fuel_for_window(start, end):
        if store.has_same_table(r):
            continue
        source = "press: " + ", ".join(r["sources"])
        if r["agreeing"] >= MIN_AGREEING:
            store.put_fuel(r, source)
        elif store.close_to_neighbours(r):
            store.put_fuel(r, source + " (single source, matches nearby tables)")
        else:
            store.add_review("fuel", r)


def tariff_from_press(store, start, end):
    for t in press.tariff_for_window(start, end):
        if t["month"] in store.tariffs:
            continue
        if t["agreeing"] >= MIN_AGREEING:
            store.put_tariff(t, "press: " + ", ".join(t["sources"]))
        else:
            store.add_review("tariff", t)


def update_rate(store, today):
    """Re-read the fuel-table dollar rate when the stored one is more than 3 days old.
    A changed rate needs two outlets agreeing; a confirmed unchanged rate needs one."""
    as_of = store.rate.get("as_of", "0000")
    if as_of >= (today - timedelta(days=3)).isoformat():
        return
    found = press.usd_rate_for_window(today - timedelta(days=PRESS_REACH_DAYS), today)
    if not found:
        return
    value, when, sources, agreeing = found
    if value != store.rate.get("usd_lbp") and agreeing < MIN_AGREEING and store.rate:
        store.add_review("usd_rate", {"date": when.isoformat(), "usd_lbp": value, "sources": sources})
        return
    store.put_rate(value, when.isoformat(), "press: " + ", ".join(sources))


def weeks(start, end):
    d = start
    while d <= end:
        yield d, min(d + timedelta(days=6), end)
        d += timedelta(days=7)


def months_between(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield f"{y:04d}-{m:02d}"
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def update_outlook(store, today):
    """Recompute the next-table estimate. Saved only when the call or its record changes."""
    new = outlook.update(list(store.fuel.values()), today)
    if not new:
        print("  outlook: wholesale prices unavailable; keeping the previous estimate")
        return
    if new != store.outlook:
        store.outlook = new
        store.changes.append(f"outlook after {new['after_table']}: 95 {new['g95']['call']}, diesel {new['diesel']['call']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", help="walk back to this date (YYYY-MM-DD) and fill gaps")
    ap.add_argument("--no-press", action="store_true", help="ministry only")
    args = ap.parse_args()

    store = Store()
    today = date.today()

    if args.backfill:
        start = date.fromisoformat(args.backfill)
        print(f"Backfill from {start}: ministry")
        from_ministry(store, start, today)
        if not args.no_press:
            # Feeds and sitemaps reach back about a month; older gaps keep the bundled tables.
            reach = max(start, today - timedelta(days=PRESS_REACH_DAYS))
            print(f"Press: fuel tables since {reach}")
            from_press(store, reach, today)
            for m in months_between(start, today.replace(day=1) - timedelta(days=1)):
                if m not in store.tariffs:
                    y, mo = map(int, m.split("-"))
                    first = date(y, mo, 20)
                    last = (date(y + (mo == 12), mo % 12 + 1, 8))
                    print(f"Press: tariff {m}")
                    tariff_from_press(store, first, last)
    else:
        ministry_ok = from_ministry(store, today - timedelta(days=45), today)
        latest = max(store.fuel) if store.fuel else "0000"
        stale = latest < (today - timedelta(days=5)).isoformat()
        recent = [k for k in store.fuel if k >= (today - timedelta(days=PRESS_REACH_DAYS)).isoformat()]
        gappy = len(recent) < PRESS_REACH_DAYS // 5  # tables come out about twice a week
        if not args.no_press and (not ministry_ok or stale or gappy):
            print("Ministry has gaps or nothing recent; checking the press.")
            from_press(store, today - timedelta(days=PRESS_REACH_DAYS), today)
        last_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        if not args.no_press and today.day > 3 and last_month not in store.tariffs:
            print(f"Tariff {last_month} missing; trying the press.")
            tariff_from_press(store, today.replace(day=1) - timedelta(days=12), today)

    if not args.no_press:
        update_rate(store, today)
    update_outlook(store, today)
    store.save()


if __name__ == "__main__":
    main()
