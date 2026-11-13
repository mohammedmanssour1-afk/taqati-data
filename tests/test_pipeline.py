"""Run with:  python -m unittest discover -s tests -v"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.common import parse_ar_date, parse_int  # noqa: E402
from pipeline.ministry import tariff_month  # noqa: E402
from pipeline.press import _numbers, _vote, extract_fuel, extract_tariff, extract_usd_rate  # noqa: E402
from pipeline.validate import fuel_ok, tariff_ok  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


class RealArticles(unittest.TestCase):
    """Short excerpts of real Almodon articles; expected values are the ministry tables."""

    def check(self, name, expected):
        got = extract_fuel(fixture(name))
        self.assertEqual((got["g95"], got["g98"], got["diesel"], got["gas"]), expected)

    def test_10_nov_2026_words_with_dual(self):  # "مليونين و445 ألف"
        self.check("almodon_2026-11-10.txt", (2_445_000, 2_463_000, 2_559_000, 1_640_000))

    def test_30_oct_2026_list_with_alaf(self):  # "2 مليون و608 آلاف"
        self.check("almodon_2026-10-30.txt", (2_611_000, 2_629_000, 2_608_000, 1_545_000))

    def test_06_oct_2026_accusative_million(self):  # "مليوناً و383 ألف"
        self.check("almodon_2026-10-06.txt", (2_825_000, 2_843_000, 2_692_000, 1_383_000))


class NumberFormats(unittest.TestCase):
    def test_formats(self):
        cases = {
            "2,445,000": 2_445_000,
            "2.445.000": 2_445_000,
            "2 445 000": 2_445_000,
            "٢٬٤٤٥٬٠٠٠": 2_445_000,
            "مليونين و445 ألف": 2_445_000,
            "مليون و640 ألفاً": 1_640_000,
            "3 ملايين و20 ألف": 3_020_000,
            "2.445 million": 2_445_000,
        }
        from pipeline.common import latin_digits
        for text, value in cases.items():
            self.assertEqual(_numbers(latin_digits(text)), [value], text)

    def test_english_table(self):
        # Constructed in the style of English-language reports.
        text = ("Fuel prices on Tuesday: 95-octane gasoline LL2,445,000 (down LL47,000); "
                "98-octane gasoline LL2,463,000; diesel LL2,559,000; gas cylinder LL1,640,000.")
        got = extract_fuel(text)
        self.assertEqual((got["g95"], got["g98"], got["diesel"], got["gas"]),
                         (2_445_000, 2_463_000, 2_559_000, 1_640_000))

    def test_change_amount_is_not_the_price(self):
        got = extract_fuel("انخفض سعر البنزين 95 أوكتان 47 ألف ليرة ليبلغ 2,445,000 ليرة")
        self.assertEqual(got["g95"], 2_445_000)

    def test_tariff_article(self):
        text = "حددت وزارة الطاقة تسعيرة الكيلوواط ساعة لشهر تشرين الأول بـ52,622 ليرة للمدن"
        self.assertEqual(extract_tariff(text), 52_622)


class DollarRate(unittest.TestCase):
    def test_real_phrasings(self):
        # Sentences as printed by Almodon with the 10 Nov and 30 Oct 2026 tables.
        self.assertEqual(extract_usd_rate("ومع احتساب سعر الدولار بـ89700 ليرة، أصبحت الأسعار"), 89_700)
        self.assertEqual(extract_usd_rate("ولا يزال سعر صرف الدولار في المحطات ثابتاً عند 89700 ليرة."), 89_700)
        self.assertEqual(extract_usd_rate("The dollar exchange rate at stations stays at LL89,700."), 89_700)

    def test_ignores_other_numbers(self):
        self.assertIsNone(extract_usd_rate("انخفض خام برنت إلى 90.63 دولاراً للبرميل"))


class SafetyChecks(unittest.TestCase):
    """Typos found in the ministry's own list must be rejected."""

    def test_missing_digit(self):  # 15 Dec 2025: 143,300 instead of 1,433,000
        self.assertFalse(fuel_ok({"g95": 143_300, "g98": 147_300, "diesel": 1_324_000})[0])

    def test_98_below_95(self):  # 2 Jan 2026
        self.assertFalse(fuel_ok({"g95": 1_359_000, "g98": 1_320_000, "diesel": 1_250_000})[0])

    def test_98_far_above_95(self):
        self.assertFalse(fuel_ok({"g95": 1_359_000, "g98": 1_600_000, "diesel": 1_250_000})[0])

    def test_good_table(self):
        self.assertTrue(fuel_ok({"g95": 2_445_000, "g98": 2_463_000, "diesel": 2_559_000,
                                 "gas": 1_640_000})[0])

    def test_tariff_matches_formula(self):
        self.assertTrue(tariff_ok({"urban": 52_622, "canister": 2_662_915})[0])
        self.assertFalse(tariff_ok({"urban": 30_000, "canister": 2_662_915})[0])


class MinistryParsing(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(parse_ar_date("الثلاثاء, 10 تشرين ثاني 2026"), date(2026, 11, 10))
        self.assertEqual(parse_ar_date("الجمعة, 02 تشرين أول 2026"), date(2026, 10, 2))
        self.assertEqual(parse_ar_date("الأربعاء, 31 كانون أول 2025"), date(2025, 12, 31))
        self.assertEqual(parse_ar_date("الجمعة, 28 شباط 2025"), date(2025, 2, 28))

    def test_ints(self):
        self.assertEqual(parse_int("2,445,000"), 2_445_000)
        self.assertIsNone(parse_int(""))

    def test_tariff_month(self):
        # Issued at the end of the month it covers, or on the 1st of the next month.
        self.assertEqual(tariff_month(date(2026, 10, 30)), "2026-10")
        self.assertEqual(tariff_month(date(2026, 10, 1)), "2026-09")


class Voting(unittest.TestCase):
    def test_majority(self):
        v, n = _vote([("almodon.com", 1), ("lbcgroup.tv", 2), ("annahar.com", 2)])
        self.assertEqual((v, n), (2, 2))

    def test_tie_goes_to_priority_outlet(self):
        v, _ = _vote([("almodon.com", 1), ("lbcgroup.tv", 2)])
        self.assertEqual(v, 2)  # LBCI is listed before Almodon


if __name__ == "__main__":
    unittest.main()


class Outlook(unittest.TestCase):
    def test_call_needs_both_signals_to_agree(self):
        from pipeline.outlook import call
        self.assertEqual(call(12_000, 2.5), "up")
        self.assertEqual(call(-9_000, -0.4), "down")
        self.assertEqual(call(12_000, -1.0), "unclear")
        self.assertEqual(call(0, 3.0), "unclear")
        self.assertEqual(call(5_000, None), "unclear")

    def test_parse_fred_skips_missing_days(self):
        from pipeline.outlook import parse_fred
        got = parse_fred("observation_date,DGASNYH\n2026-11-10,2.40\n2026-11-11,.\n2026-11-12,2.45\n", "DGASNYH")
        self.assertEqual(got, {date(2026, 11, 10): 2.40, date(2026, 11, 12): 2.45})

    def test_build_reads_latest_trend_and_prices(self):
        from datetime import timedelta
        from pipeline.outlook import build
        rows = [{"date": "2026-11-03", "g95": 2_400_000, "diesel": 2_500_000},
                {"date": "2026-11-06", "g95": 2_420_000, "diesel": 2_540_000},
                {"date": "2026-11-10", "g95": 2_445_000, "diesel": 2_520_000}]
        start = date(2026, 10, 20)
        rising = {start + timedelta(days=i): 2.0 + i * 0.01 for i in range(30)}
        out = build(rows, {"g95": rising, "diesel": rising}, date(2026, 11, 13))
        self.assertEqual(out["after_table"], "2026-11-10")
        self.assertEqual(out["g95"]["call"], "up")        # rising trend and rising prices
        self.assertEqual(out["diesel"]["call"], "unclear")  # falling trend, rising prices


class ReleaseDays(unittest.TestCase):
    def test_last_release_day(self):
        from pipeline.update import last_release_day
        self.assertEqual(last_release_day(date(2026, 11, 13)), date(2026, 11, 13))  # Friday itself
        self.assertEqual(last_release_day(date(2026, 11, 15)), date(2026, 11, 13))  # Sunday
        self.assertEqual(last_release_day(date(2026, 11, 17)), date(2026, 11, 17))  # Tuesday
        self.assertEqual(last_release_day(date(2026, 11, 16)), date(2026, 11, 13))  # Monday
