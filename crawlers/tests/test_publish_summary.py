"""publish_summary: per-day breakdowns make a partial (one-night) run additive.

Regression: a nightly run on a runner holding one night's file replaced the
archive's prefecture and entity-kind totals with that night's (89,724 -> 2,101).
"""

from __future__ import annotations

import csv
import gzip
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CRAWLERS = HERE.parent
sys.path.insert(0, str(CRAWLERS))

_spec = importlib.util.spec_from_file_location("deltakura_publish_summary", CRAWLERS / "nta_diff" / "publish_summary.py")
ps = importlib.util.module_from_spec(_spec)
sys.modules["deltakura_publish_summary"] = ps
_spec.loader.exec_module(ps)  # type: ignore[union-attr]

REPO = CRAWLERS.parent


def _store(root: Path, rows):
    norm = root / "nta" / "normalized"
    norm.mkdir(parents=True, exist_ok=True)
    with gzip.open(norm / "nta_diff_2026-09.csv.gz", "wt", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["file_date", "process_code", "kind_code", "prefecture"])
        w.writeheader()
        for r in rows:
            w.writerow(dict(zip(["file_date", "process_code", "kind_code", "prefecture"], r)))


def test_partial_run_adds_to_the_totals_instead_of_replacing_them():
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        full, partial = Path(a), Path(b)
        _store(full, [
            ("2026-09-01", "01", "301", "東京都"),
            ("2026-09-01", "12", "301", "大阪府"),
            ("2026-09-02", "01", "305", "東京都"),
        ])
        first = ps.build(full)
        assert first["prefectures"] == {"大阪府": 1, "東京都": 2}
        assert first["days"]["2026-09-01"]["prefectures"] == {"大阪府": 1, "東京都": 1}

        # The next night's runner holds only the new day.
        _store(partial, [("2026-09-03", "21", "301", "愛知県")])
        second = ps.build(partial, previous=json.loads(json.dumps(first)))
        assert second["coverage"]["records"] == 4
        assert second["prefectures"] == {"大阪府": 1, "愛知県": 1, "東京都": 2}
        assert second["kind_codes"] == {"301": 3, "305": 1}


def test_published_totals_equal_the_sum_of_the_day_table():
    summary = json.loads((REPO / "data" / "published" / "nta" / "summary.json").read_text(encoding="utf-8"))
    for field in ("prefectures", "kind_codes", "process_codes"):
        summed = {}
        for entry in summary["days"].values():
            assert field in entry, f"a day lacks its {field} breakdown"
            for k, n in entry[field].items():
                summed[k] = summed.get(k, 0) + n
        assert summed == summary[field], field
    # Prefecture is blank on a few records only; a partial overwrite would show here.
    assert sum(summary["prefectures"].values()) >= 0.99 * summary["coverage"]["records"]
    assert sum(summary["kind_codes"].values()) == summary["coverage"]["records"]
