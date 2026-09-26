"""Article charts against the committed data (data/published/nta/summary.json).

    python site/tests/test_charts.py
"""

from __future__ import annotations

import collections
import json
import re
import sys
import unittest
from pathlib import Path

SITE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SITE))
import build  # noqa: E402

SUMMARY = json.loads((SITE.parent / "data" / "published" / "nta" / "summary.json").read_text(encoding="utf-8"))
BET_C = build.load_bet_c()
CHART, _USED = build.article_charts(build.load_bet_a(), BET_C)("ja")


def table_counts(html: str) -> dict:
    """{prefecture: count} from the full table inside the chart's <details>."""
    body = html.split("<details>", 1)[1]
    return {
        name: int(n.replace(",", ""))
        for name, n in re.findall(r"<tr><td>([^<]+)</td><td class=\"n\">([\d,]+)</td>", body)
    }


class PrefectureChart(unittest.TestCase):
    def test_whole_archive_equals_summary_prefectures(self):
        counts = table_counts(CHART("nta-prefecture-top10", []))
        self.assertEqual(counts, SUMMARY["prefectures"])
        self.assertEqual(sum(counts.values()), sum(SUMMARY["prefectures"].values()))

    def test_period_equals_the_per_day_sum(self):
        lo, hi = BET_C["days"][0], BET_C["days"][min(39, len(BET_C["days"]) - 1)]
        want = collections.Counter()
        for day, entry in SUMMARY["days"].items():
            if lo <= day <= hi:
                want.update(entry["prefectures"])
        self.assertEqual(table_counts(CHART("nta-prefecture-top10", [lo, hi])), dict(want))

    def test_bad_periods_fail(self):
        for args in (["2026-09-18", "2026-07-24"], ["2020-01-01", "2020-01-31"], ["x", "y"], ["2026-07-24"]):
            with self.assertRaises(build.articles_mod.ArticleError):
                CHART("nta-prefecture-top10", args)


class DailyCharts(unittest.TestCase):
    def test_period_mean_and_caption(self):
        days = BET_C["days"][:5]
        html = CHART("nta-daily-by-process", [days[0], days[-1]])
        mean = sum(BET_C["daily"][d] for d in days) / len(days)
        self.assertIn(f"平均 {mean:,.0f}件", html)
        self.assertIn(f"{days[0]}〜{days[-1]}、{len(days)}日分", html)


class Strip(unittest.TestCase):
    def test_every_held_day_is_in_the_storehouse_row(self):
        oldest = BET_C["days"][2]
        html = CHART("nta-strip", [oldest, "2026-09-27"])
        self.assertEqual(html.count('class="c-kura"'), len(BET_C["days"]))
        self.assertEqual(html.count('class="c-gone"'), 2)
        self.assertIn("2026-09-27 確認", html)


if __name__ == "__main__":
    unittest.main(verbosity=1)
