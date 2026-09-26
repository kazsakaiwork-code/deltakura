"""Tests for site/articles.py: front matter and the Markdown subset.

Standard library only (unittest); no article files and no network.

    python site/tests/test_articles.py
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import articles as A  # noqa: E402

FRONT = """---
slug: test-article
title: テスト
date: 2026-10-05
description: 要約
lang: ja
sources:
  - 出典：調達ポータル（https://www.p-portal.go.jp/）
  - name: 国税庁法人番号公表サイト
    url: https://www.houjin-bangou.nta.go.jp/
---
"""


def no_chart(cid, args):
    raise A.ArticleError(f"unexpected chart {cid}")


class FrontMatter(unittest.TestCase):
    def test_reads_scalars_lists_and_mapping_items(self):
        meta, body, first = A.parse_front_matter(FRONT + "本文\n")
        art = A.validate(meta, "t.md")
        self.assertEqual(art["slug"], "test-article")
        self.assertEqual(str(art["date"]), "2026-10-05")
        self.assertEqual(art["updated"], art["date"])
        self.assertEqual(art["product"], "bet_a")
        self.assertEqual(len(art["sources"]), 2)
        self.assertIn("https://www.houjin-bangou.nta.go.jp/", art["sources"][1])
        self.assertEqual(body.strip(), "本文")
        self.assertEqual(first, 12)

    def test_rejects_missing_unknown_and_malformed_fields(self):
        for bad, needle in [
            (FRONT.replace("lang: ja\n", ""), "missing"),
            (FRONT.replace("lang: ja", "lang: fr"), "lang"),
            (FRONT.replace("slug: test-article", "slug: Test_Article"), "slug"),
            (FRONT.replace("date: 2026-10-05", "date: 2026-13-05"), "date"),
            (FRONT.replace("lang: ja", "lang: ja\nauthor: someone"), "unknown"),
            (FRONT.replace("lang: ja", "lang: ja\nproduct: pricing"), "product"),
        ]:
            with self.assertRaises(A.ArticleError) as cm:
                A.validate(A.parse_front_matter(bad)[0], "t.md")
            self.assertIn(needle, str(cm.exception))

    def test_load_skips_drafts_and_refuses_duplicate_slugs(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "a.md").write_text(FRONT + "x\n", encoding="utf-8")
            (p / "b.md").write_text(FRONT.replace("lang: ja", "lang: ja\ndraft: true") + "x\n", encoding="utf-8")
            self.assertEqual([a["file"] for a in A.load_articles(p)], ["a.md"])
            (p / "c.md").write_text(FRONT + "y\n", encoding="utf-8")
            with self.assertRaises(A.ArticleError):
                A.load_articles(p)


class Markdown(unittest.TestCase):
    def render(self, text, chart=no_chart):
        return A.render_markdown(text, chart, "t.md")[0]

    def test_headings_paragraphs_and_japanese_line_joins(self):
        html = self.render("# 見出し\n\n一行目\n二行目\n\n### 小見出し\n")
        self.assertIn('<h2 id="s1">見出し</h2>', html)
        self.assertIn("<p>一行目二行目</p>", html)
        self.assertIn("<h3>小見出し</h3>", html)

    def test_inline_markup_and_link_safety(self):
        html = self.render("**強** *em* `a<b` [ok](/ja/) [ext](https://example.com/) [bad](javascript:x) <b>raw</b>")
        self.assertIn("<strong>強</strong>", html)
        self.assertIn("<em>em</em>", html)
        self.assertIn("<code>a&lt;b</code>", html)
        self.assertIn('<a href="/ja/">ok</a>', html)
        self.assertIn('<a href="https://example.com/" rel="noopener">ext</a>', html)
        self.assertNotIn("javascript:", html.replace("bad", ""))
        self.assertNotIn("<b>", html)
        self.assertIn("&lt;b&gt;raw&lt;/b&gt;", html)

    def test_bare_url_stops_at_japanese_punctuation(self):
        html = self.render("取得先：https://example.com/x。次")
        self.assertIn('<a href="https://example.com/x" rel="noopener">https://example.com/x</a>。次', html)

    def test_lists_nest_by_indentation(self):
        html = self.render("- a\n  - b\n- c\n\n1. x\n2. y\n")
        self.assertIn("<ul><li>a<ul><li>b</li></ul></li><li>c</li></ul>", html)
        self.assertIn("<ol><li>x</li><li>y</li></ol>", html)

    def test_tables_align_numeric_columns(self):
        html = self.render("| 府省 | 件数 |\n|---|---|\n| 外交 | 2,112 |\n| 防衛 | 11,157 |\n")
        self.assertIn('<th scope="col" class="n">件数</th>', html)
        self.assertIn('<td class="n">2,112</td>', html)
        self.assertIn("<td>外交</td>", html)

    def test_chart_placeholder_calls_the_chart_hook(self):
        seen = []

        def chart(cid, args):
            seen.append((cid, args))
            return "<figure>c</figure>"

        html = self.render("前\n\n{{chart:bet-a-box:mlit:2025}}\n\n後", chart)
        self.assertEqual(seen, [("bet-a-box", ["mlit", "2025"])])
        self.assertIn("<figure>c</figure>", html)

    def test_chart_placeholder_must_stand_alone(self):
        with self.assertRaises(A.ArticleError):
            self.render("文中の {{chart:nta-strip}} は不可")

    def test_comments_are_dropped_with_their_placeholders(self):
        html = self.render("前\n<!--\nnote {{chart:nope}}\n-->\n後")
        self.assertNotIn("note", html)
        self.assertIn("<p>後</p>", html)

    def test_images_are_refused(self):
        with self.assertRaises(A.ArticleError):
            self.render("![alt](https://example.com/x.png)")

    def test_errors_carry_the_line_number(self):
        with self.assertRaises(A.ArticleError) as cm:
            A.render_markdown("a\n\n```\nunclosed", no_chart, "t.md", first_line=10)
        self.assertIn("t.md:12", str(cm.exception))


if __name__ == "__main__":
    unittest.main(verbosity=1)
