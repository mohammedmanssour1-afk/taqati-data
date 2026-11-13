"""Shared helpers: HTTP, digits, Arabic dates and number parsing."""
import re
import time
import urllib.parse
import urllib.request
from datetime import date

USER_AGENT = "taqati-data/1.0 (+https://github.com/mohammedmanssour1-afk/taqati-data)"

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

# Levantine and standard Arabic month names, plus English.
MONTHS = {
    "كانون الثاني": 1, "كانون ثاني": 1, "يناير": 1,
    "شباط": 2, "فبراير": 2,
    "آذار": 3, "اذار": 3, "مارس": 3,
    "نيسان": 4, "أبريل": 4, "ابريل": 4,
    "أيار": 5, "ايار": 5, "مايو": 5,
    "حزيران": 6, "يونيو": 6,
    "تموز": 7, "يوليو": 7,
    "آب": 8, "اب": 8, "أغسطس": 8, "اغسطس": 8,
    "أيلول": 9, "ايلول": 9, "سبتمبر": 9,
    "تشرين الأول": 10, "تشرين أول": 10, "تشرين اول": 10, "أكتوبر": 10, "اكتوبر": 10,
    "تشرين الثاني": 11, "تشرين ثاني": 11, "نوفمبر": 11,
    "كانون الأول": 12, "كانون أول": 12, "كانون اول": 12, "ديسمبر": 12,
}


def fetch(url, data=None, timeout=40, retries=2):
    """GET (or POST when data is given) with an honest user agent. Returns text or None."""
    body = data.encode() if isinstance(data, str) else data
    for attempt in range(retries + 1):
        try:
            # Some outlets use Arabic letters in links; send them percent-encoded.
            url = urllib.parse.quote(url, safe=":/?&=%#+,;@!$'()*[]~")
            req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8-sig", errors="replace")
        except Exception as e:  # network errors are expected; caller falls back
            if attempt == retries:
                print(f"  fetch failed: {url[:90]} ({e})")
                return None
            time.sleep(3 * (attempt + 1))


def latin_digits(s):
    return s.translate(_ARABIC_DIGITS)


def parse_int(s):
    """'2,445,000', '2.445.000', '2 445 000', '٢٬٤٤٥٬٠٠٠' -> 2445000. Returns None when empty."""
    s = latin_digits(s or "")
    digits = re.sub(r"[^\d]", "", s)
    return int(digits) if digits else None


def parse_ar_date(s):
    """'الثلاثاء, 10 تشرين ثاني 2026' -> date(2026, 11, 10)."""
    s = latin_digits(s)
    m = re.search(r"(\d{1,2})\s+([^\d,]+?)\s+(\d{4})", s)
    if not m:
        return None
    month = MONTHS.get(m.group(2).strip())
    if not month:
        return None
    return date(int(m.group(3)), month, int(m.group(1)))


def strip_html(h):
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h)
    h = re.sub(r"(?i)<br\s*/?>|</p>|</tr>|</li>|</h\d>", "\n", h)
    h = re.sub(r"<[^>]+>", " ", h)
    h = h.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"[ \t ]+", " ", h)
