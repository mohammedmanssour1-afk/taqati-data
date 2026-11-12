# Taqati Data

Lebanese fuel prices and the monthly generator tariff, updated automatically for the Taqati app.

## Files the App Reads

| File | What it holds |
| --- | --- |
| `data/fuel.json` | Fuel tables: date, 95 and 98 octane, diesel (per 20 L), gas cylinder, in LBP |
| `data/tariffs.json` | Generator tariff per month: urban price per kWh and the diesel canister average |
| `data/rates.json` | Dollar rate (LBP per USD) used in the latest ministry fuel table |
| `data/meta.json` | Last update time and the latest dates |
| `data/review.json` | Readings held back for a person to check (not used by the app) |

Raw links for the app:

- `https://raw.githubusercontent.com/mohammedmanssour1-afk/taqati-data/main/data/fuel.json`
- `https://raw.githubusercontent.com/mohammedmanssour1-afk/taqati-data/main/data/tariffs.json`

## Where the Numbers Come From

1. **Ministry of Energy and Water first.** The ministry's official price lists (fuel, gas and generator tariff). They are the source of truth and replace any other value for the same date.
2. **Press fallback.** For dates the ministry leaves out, articles from a fixed list of Lebanese outlets: NNA, LBCI, Annahar, Almodon. Their public feeds and sitemaps reach back about a month. A table is stored when two outlets agree. A single outlet's table is stored only when every price is within 10% of the nearest known table; otherwise it goes to `review.json`.
3. **Seed.** The tables collected by hand for the first version of the app. Older gaps that neither source covers keep these.

**Dollar rate.** Banque du Liban's website refuses automated requests, so the rate comes from the ministry fuel table itself: outlets print the dollar rate the ministry used with every table (89,700 LL in November 2026). It is re-read when more than 3 days old; a changed rate needs two outlets agreeing.

Outlets that refuse automated requests (Elnashra, L'Orient Today) are not used.

## Safety Checks

Every row is checked before it is stored (`pipeline/validate.py`):

- Prices must fall in plausible ranges. This catches typos in the ministry's own list, such as 143,300 instead of 1,433,000.
- 98 octane must be just above 95 octane.
- The generator tariff must agree with the ministry formula (urban kWh = 5,476 + 0.017705 × diesel canister) within 3,000 LL.

## Running It

```bash
python -m unittest discover -s tests -v
python -m pipeline.update                       # normal run
python -m pipeline.update --backfill 2024-12-01 # walk back and fill gaps
```

GitHub Actions runs the normal update every 3 hours and commits only when there is new data. A backfill can be started from the Actions tab (Run workflow, then enter a date).
