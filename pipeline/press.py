"""Press fallback: used only for dates the ministry does not cover.

Articles come from the public RSS feeds of a fixed list of Lebanese outlets. Prices are read from
each article; when outlets disagree, the value most of them agree on wins. Outlets that refuse
automated requests (Elnashra, L'Orient Today) are respected and not used.
"""
import re
import urllib.parse
from collections import Counter
from datetime import datetime
from email.utils import parsedate_to_datetime

from .common import fetch, latin_digits, strip_html
from .validate import fuel_ok, in_range

# Outlets we trust and their public feeds, in priority order (used only to break ties).
FEEDS = [
    ("nna-leb.gov.lb", "https://www.nna-leb.gov.lb/ar/rss"),        # National News Agency (state)
    ("lbcgroup.tv", "https://www.lbcgroup.tv/Rss/latest-news/ar"),  # LBCI
    ("annahar.com", "https://www.annahar.com/rss"),
    ("almodon.com", "https://www.almodon.com/rss"),
]
TRUSTED = [d for d, _ in FEEDS]

# Sitemaps reach back about a month, so a missed table can still be picked up later.
SITEMAPS = [
    ("almodon.com", "https://www.almodon.com/sitemap.xml?page=1"),
]

TOPIC_WORDS = {
    "fuel": r"المحروقات|البنزين|المازوت|fuel prices|gasoline",
    "tariff": r"تسعيرة|المولدات|الكيلوواط|generator",
}

# Labels that introduce each price. The price is the first in-range number after the label.
LABELS = {
    "g95": [r"95\s*(?:أ|ا)وكتان", r"(?:أ|ا)وكتان\s*95", r"بنزين\s*95", r"95[\s-]*octane", r"octane\s*95",
            r"(?:petrol|gasoline)\s*95", r"95\s*RON"],
    "g98": [r"98\s*(?:أ|ا)وكتان", r"(?:أ|ا)وكتان\s*98", r"بنزين\s*98", r"98[\s-]*octane", r"octane\s*98",
            r"(?:petrol|gasoline)\s*98", r"98\s*RON"],
    "diesel": [r"المازوت", r"مازوت", r"ديزل", r"diesel"],
    "gas": [r"قارورة\s*(?:ال)?غاز", r"(?:ال)?غاز\s*المنزلي", r"(?<![\w])(?:ال)?غاز(?![\w])", r"gas\s*cylinder",
            r"cooking\s*gas", r"(?<![\w])gas(?![\w])"],
}
WINDOW = 90  # characters to look ahead after a label

_NUM = re.compile(r"\d{1,3}(?:[,.٬، ]\d{3})+|\d{5,}")
# Amounts written with words: "مليونين و445 ألف", "2 مليون و608 آلاف", "مليون و640 ألفاً",
# "3 ملايين و20 ألف", "2.445 million".
_ARABIC_COUNT = {"مليون": 1, "مليوناً": 1, "مليونا": 1, "مليونان": 2, "مليونين": 2}
_SMALL_WORDS = {"ثلاثة": 3, "ثلاث": 3, "أربعة": 4, "اربعة": 4, "خمسة": 5}
_MILLION = re.compile(
    r"(?:(\d+(?:[.,]\d+)?|ثلاثة|ثلاث|أربعة|اربعة|خمسة)\s*)?"
    r"(مليونين|مليونان|مليوناً|مليونا|مليون|ملايين|million)"
    r"(?:\s*و\s*(\d{1,3})\s*(?:ألف|الف|آلاف|الاف|thousand)(?:اً|ا)?)?", re.I)


def _numbers(text):
    """Numbers in reading order, including '2 مليون و445 ألف' style amounts."""
    found = []
    taken = []
    for m in _MILLION.finditer(text):
        count, word, thousands = m.group(1), m.group(2), m.group(3)
        if count and count[0].isdigit():
            whole = float(count.replace(",", "."))
        elif count:
            whole = _SMALL_WORDS[count]
        else:
            whole = _ARABIC_COUNT.get(word, 1)
        found.append((m.start(), int(round(whole * 1_000_000)) + int(thousands or 0) * 1000))
        taken.append((m.start(), m.end()))
    for m in _NUM.finditer(text):
        if not any(a <= m.start() < b for a, b in taken):
            found.append((m.start(), int(re.sub(r"\D", "", m.group(0)))))
    return [v for _, v in sorted(found)]


def extract_fuel(text):
    """Read one fuel table from article text. Returns {g95, g98, diesel, gas} (values may be None)."""
    text = latin_digits(text)
    out = {}
    for key, patterns in LABELS.items():
        out[key] = None
        for p in patterns:
            for m in re.finditer(p, text, re.I):
                window = text[m.end(): m.end() + WINDOW]
                # Stop at the next product label so we never borrow its price.
                cut = re.search(r"\n|بنزين|مازوت|المازوت|غاز|ديزل|octane|diesel|gas\b", window[3:], re.I)
                if cut:
                    window = window[: cut.start() + 3]
                for n in _numbers(window):
                    if in_range(key, n):
                        out[key] = n
                        break
                if out[key]:
                    break
            if out[key]:
                break
    return out


def extract_tariff(text):
    """Urban kWh price from an article about the monthly generator tariff, or None."""
    text = latin_digits(text)
    for m in re.finditer(r"كيلو\s*وات|كيلوواط|الكيلوات|kwh|kilowatt", text, re.I):
        around = text[max(0, m.start() - 80): m.end() + 80]
        for n in _numbers(around):
            if in_range("urban", n):
                return n
    return None


# The dollar rate the ministry used for the fuel table, which outlets print with each table
# ("ومع احتساب سعر الدولار بـ89700 ليرة", "سعر صرف الدولار في المحطات ... 89700").
_USD = re.compile(r"(?:سعر\s*(?:صرف\s*)?الدولار|الدولار\s*(?:الأميركي\s*)?(?:في المحطات)?|dollar\s*(?:rate|exchange rate)|exchange rate)"
                  r"[^\d\n]{0,60}?(\d{2}[,.\s]?\d{3})", re.I)
USD_RANGE = (80_000, 100_000)


def extract_usd_rate(text):
    """The fuel-table dollar rate in LBP, or None."""
    for m in _USD.finditer(latin_digits(text)):
        v = int(re.sub(r"\D", "", m.group(1)))
        if USD_RANGE[0] <= v <= USD_RANGE[1]:
            return v
    return None


def usd_rate_for_window(start, end, max_articles=6):
    """(rate, date, sources, agreeing) from the newest fuel articles in the window, or None."""
    arts = sorted(find_articles(start, end, "fuel"), key=lambda a: a[0], reverse=True)[:max_articles]
    readings, latest = [], None
    for d, url, domain in arts:
        html = fetch(url, timeout=30, retries=0)
        v = extract_usd_rate(strip_html(html)) if html else None
        if v:
            readings.append((domain, v))
            latest = latest or d
    if not readings:
        return None
    v, n = _vote(readings)
    return v, latest, sorted({d for d, x in readings if x == v}), n


def _domain_ok(url):
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in TRUSTED)


def _feed_date(text):
    """RFC 822 ('Tue, 10 Nov 2026 08:00:00 +0200') or ISO ('2026-11-10T08:00:00+02:00')."""
    try:
        return parsedate_to_datetime(text).date()
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.fromisoformat(text[:19]).date()
    except ValueError:
        return None


def find_articles(start, end, topic="fuel"):
    """[(date, url, domain)] from the trusted feeds, published between start and end (dates),
    whose title is about the topic."""
    words = re.compile(TOPIC_WORDS[topic], re.I)
    out = []
    for domain, feed in FEEDS:
        xml = fetch(feed, timeout=30, retries=1)
        if not xml:
            continue
        for item in re.findall(r"(?s)<item>(.*?)</item>", xml):
            title = re.search(r"(?s)<title>(.*?)</title>", item)
            link = re.search(r"(?s)<link>(.*?)</link>", item)
            when = re.search(r"<pubDate>(.*?)</pubDate>", item)
            if not (title and link and when):
                continue
            title = re.sub(r"<!\[CDATA\[|\]\]>", "", title.group(1))
            url = re.sub(r"<!\[CDATA\[|\]\]>", "", link.group(1)).strip()
            d = _feed_date(when.group(1).strip())
            if not d:
                continue
            if start <= d <= end and words.search(title) and _domain_ok(url):
                out.append((d, url, domain))
    for domain, sitemap in SITEMAPS:
        xml = fetch(sitemap, timeout=60, retries=1)
        for loc in re.findall(r"<loc>([^<]+)</loc>", xml or ""):
            slug = urllib.parse.unquote(loc)
            m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", slug)
            if not m or not words.search(slug) or "/opinion/" in slug:
                continue
            d = datetime(*map(int, m.groups())).date()
            if start <= d <= end and _domain_ok(loc):
                out.append((d, loc, domain))
    seen, unique = set(), []
    for item in out:
        if item[1] not in seen:
            seen.add(item[1])
            unique.append(item)
    return unique


def _vote(readings):
    """readings: [(domain, value)] -> (value, agreeing_outlets). Most common wins; ties go to
    the higher-priority outlet."""
    readings = [(d, v) for d, v in readings if v is not None]
    if not readings:
        return None, 0
    counts = Counter(v for _, v in readings)
    best = max(counts.values())
    candidates = {v for v, c in counts.items() if c == best}

    def rank(domain):
        for i, t in enumerate(TRUSTED):
            if t in domain:
                return i
        return len(TRUSTED)

    for d, v in sorted(readings, key=lambda r: rank(r[0])):
        if v in candidates:
            return v, best


def fuel_for_window(start, end, max_articles=20):
    """Fuel tables from the press for a date window: [{date, g95, g98, diesel, gas, sources}]."""
    arts = find_articles(start, end, "fuel")[:max_articles]
    by_date = {}
    for d, url, domain in arts:
        html = fetch(url, timeout=30, retries=0)
        if not html:
            continue
        row = extract_fuel(strip_html(html))
        if fuel_ok(row)[0]:
            by_date.setdefault(d.isoformat(), []).append((domain, row))
    out = []
    for d, items in sorted(by_date.items()):
        triple, n = _vote([(dom, (r["g95"], r["g98"], r["diesel"])) for dom, r in items])
        gas, _ = _vote([(dom, r["gas"]) for dom, r in items])
        sources = sorted({dom for dom, r in items if (r["g95"], r["g98"], r["diesel"]) == triple})
        out.append({"date": d, "g95": triple[0], "g98": triple[1], "diesel": triple[2], "gas": gas,
                    "sources": sources, "agreeing": n})
    return out


def tariff_for_window(start, end, max_articles=8):
    """[(month, urban, sources)] read from press articles in the window."""
    arts = find_articles(start, end, "tariff")[:max_articles]
    readings = {}
    for d, url, domain in arts:
        html = fetch(url, timeout=30, retries=0)
        v = extract_tariff(strip_html(html)) if html else None
        if v:
            month_date = d if d.day > 7 else d.replace(day=1)
            if d.day <= 7:
                prev = month_date.toordinal() - 1
                month_date = datetime.fromordinal(prev).date()
            readings.setdefault(f"{month_date.year:04d}-{month_date.month:02d}", []).append((domain, v))
    out = []
    for month, items in sorted(readings.items()):
        v, n = _vote(items)
        out.append({"month": month, "urban": v, "sources": sorted({d for d, x in items if x == v}),
                    "agreeing": n})
    return out
